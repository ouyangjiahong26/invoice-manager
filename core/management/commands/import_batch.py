"""AI 导入历史批次目录：扫文件 → vision.detect 逐张识别 → 规则+LLM 配对 → 建待审核条目。

纯 AI 路径，不读人工汇总 xlsx；配对与字段建议和批量提交页共用 pairing/suggest。
用法：python manage.py import_batch <目录> [--batch 名称] [--dry-run]
"""

import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError

from core import pairing, vision
from core.attachments import build_attachment, decimal_or_none
from core.audit import attachment_entry, current_actor, mark_attachments
from core.models import Attachment, Batch, Category, Item
from core.suggest import category_payloads, llm_merge_groups, suggest_member
from core.validation import check_item

SKIP_DIRS = {"汇总", ".zcode", "__pycache__", "原始备份", "需要整理"}
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".pdf"}


class Command(BaseCommand):
    help = "扫目录 → AI 识别 → 配对 → 建待审核条目（不读汇总 xlsx；需配置 DEEPSEEK_API_KEY）"

    def add_arguments(self, parser):
        parser.add_argument("directory", help="批次材料目录（顶层散文件 + 一级人名子目录）")
        parser.add_argument("--batch", default="", help="批次名称，缺省取目录名；同名批次复用")
        parser.add_argument("--dry-run", action="store_true", help="只打印报告，不落库")

    def handle(self, *args, **options):
        root = Path(options["directory"])
        if not root.is_dir():
            raise CommandError(f"目录不存在：{root}")
        if not vision.configured():
            raise CommandError("未配置 DEEPSEEK_API_KEY，无法执行 AI 识别")
        actor = current_actor()
        if actor is not None and not (actor.is_staff or actor.is_superuser):
            raise CommandError("仅管理员可执行导入")
        self.executor = actor or User.objects.filter(is_superuser=True).first()
        if self.executor is None:
            raise CommandError("找不到可用的执行者账号（无超级管理员）")

        self.skipped = []        # [(相对路径, 原因)]
        self.detect_failed = []  # [文件标识]
        self.owner_notes = []    # 付款人归属说明
        self.dry_run = options["dry_run"]

        batch_name = (options["batch"] or root.name).strip()
        batch = Batch.objects.filter(name=batch_name).first()
        if batch is None:
            if self.dry_run:
                self.stdout.write(f"批次：{batch_name}（dry-run，不创建）")
            else:
                batch = Batch.objects.create(name=batch_name)
                self.stdout.write(f"已创建批次：{batch_name}")
        else:
            self.stdout.write(f"复用已有批次：{batch_name}")

        with tempfile.TemporaryDirectory() as unzip_root:
            owner_groups = self._collect_by_owner(root, Path(unzip_root))
            records_by_owner = self._recognize(owner_groups)
            created, singles = self._create_items(records_by_owner, batch)
            self._print_report(root, records_by_owner, created, singles)

    # ---------- 收集文件 ----------

    def _collect_by_owner(self, root, unzip_root):
        """扫描目录：顶层散文件归执行者，一级子目录按人名归档；返回 [(人名 or None, [Path])]。"""
        loose, people, zip_files = [], {}, []

        def classify(path, bucket):
            ext = path.suffix.lower()
            if ext in ALLOWED_EXTS:
                bucket.append(path)
            elif ext == ".zip":
                zip_files.append((path, bucket))
            else:
                self.skipped.append((self._rel(root, path), f"不支持的扩展名 {ext or '(无后缀)'}"))

        for entry in sorted(root.iterdir()):
            if entry.name.startswith("."):
                self.skipped.append((entry.name, "隐藏文件"))
                continue
            if entry.is_dir():
                if entry.name in SKIP_DIRS:
                    self.skipped.append((entry.name, "目录在跳过名单"))
                    continue
                bucket = people.setdefault(entry.name, [])
                for path in sorted(entry.rglob("*")):
                    if path.is_file():
                        classify(path, bucket)
            elif entry.is_file():
                classify(entry, loose)

        # zip（含人名目录内）解开到临时目录，内容归属 zip 所在的目录桶
        for index, (zip_path, bucket) in enumerate(zip_files):
            target = unzip_root / f"zip-{index}"
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(target)
            except zipfile.BadZipFile:
                self.skipped.append((self._rel(root, zip_path), "zip 文件损坏"))
                continue
            for path in sorted(target.rglob("*")):
                if path.is_file() and path.suffix.lower() in ALLOWED_EXTS:
                    bucket.append(path)


        owners = [(None, loose)]
        owners.extend(people.items())
        return owners

    @staticmethod
    def _rel(root, path):
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    # ---------- 识别 ----------

    def _recognize(self, owner_groups):
        """逐文件 detect；返回 {(owner, 归属说明): [record]}，record 含配对与建条所需字段。"""
        records_by_owner = {}
        record_id = 0
        for name, paths in owner_groups:
            if name is None:
                owner, note = self._executor_note()
            else:
                owner, note = self._match_person(name)
            records = records_by_owner.setdefault((owner, note), [])
            for path in paths:
                data = path.read_bytes()
                result = vision.detect(data, path.name)
                kind = result.get("kind") if isinstance(result, dict) else None
                if not result or not kind:
                    self.detect_failed.append(f"[{note}] {path.name}")
                    continue
                amount = result.get("invoice_amount") if kind == "invoice" else result.get("amount")
                record_id += 1
                records.append({
                    "id": record_id,
                    "kind": kind,
                    "amount": decimal_or_none(amount),
                    "order_no": result.get("order_no") or "",
                    "merchant_no": result.get("merchant_no") or "",
                    "invoice_no": result.get("invoice_no") or "",
                    "remark_order_no": result.get("remark_order_no") or "",
                    "items_summary": result.get("items_summary") or "",
                    "ocr": result,
                    "name": path.name,
                    "data": data,
                })
        return records_by_owner

    def _executor_note(self):
        if not any("顶层散文件" in line for line in self.owner_notes):
            self.owner_notes.append(
                f"顶层散文件 → owner=执行者 {self._user_label(self.executor)}（本人垫付）"
            )
        return self.executor, "顶层散文件"

    def _match_person(self, name):
        candidates = list(User.objects.filter(first_name=name, is_active=True))
        if len(candidates) == 1:
            return candidates[0], name
        if not candidates:
            self.owner_notes.append(
                f"人名 {name} 无匹配账号 → owner=执行者 {self._user_label(self.executor)}"
            )
        else:
            names = "、".join(self._user_label(user) for user in candidates)
            self.owner_notes.append(
                f"人名 {name} 匹配到多个账号（{names}）→ owner=执行者 {self._user_label(self.executor)}"
            )
        return self.executor, name

    @staticmethod
    def _user_label(user):
        return user.get_full_name() or user.username

    # ---------- 配对与建条 ----------

    def _create_items(self, records_by_owner, batch):
        created, singles = [], []
        for (owner, note), records in records_by_owner.items():
            if not records:
                continue
            by_id = {record["id"]: record for record in records}
            result = pairing.pair(records)
            groups, pending, unmatched = result["groups"], result["pending"], result["unmatched"]
            groups, pending, unmatched = llm_merge_groups(by_id, groups, pending, unmatched)
            suggestions = []
            if groups:
                suggestions = (vision.field_suggest(
                    [[suggest_member(by_id[rid]) for rid in group] for group in groups],
                    category_payloads(),
                ) or {}).get("suggestions") or []

            for index, group in enumerate(groups):
                members = [by_id[rid] for rid in group]
                kinds = {member["kind"] for member in members}
                if not {Attachment.KIND_INVOICE, Attachment.KIND_PAYMENT} <= kinds:
                    singles.append((note, " / ".join(m["name"] for m in members), "组内缺发票或支付记录"))
                    continue
                suggestion = suggestions[index] if index < len(suggestions) else None
                created.append(self._create_item(owner, note, members, suggestion, batch))
            for group in pending:
                singles.append((note, " / ".join(by_id[rid]["name"] for rid in group), "待确认组（未自动成条目）"))
            for entry in unmatched:
                record = by_id.get(entry["id"])
                name = record["name"] if record else str(entry["id"])
                singles.append((note, name, entry["reason"]))
        return created, singles

    def _create_item(self, owner, note, members, suggestion, batch):
        invoices = [m for m in members if m["kind"] == Attachment.KIND_INVOICE]
        payments = [m for m in members if m["kind"] == Attachment.KIND_PAYMENT]
        refunds = [m for m in members if m["kind"] == Attachment.KIND_REFUND]

        title = ((suggestion or {}).get("title") or "").strip()
        if not title:
            title = next((m["items_summary"][:50] for m in invoices if m["items_summary"]), "")
        title = title.strip() or "待命名条目"

        category = None
        category_id = (suggestion or {}).get("category_id")
        if category_id:
            category = Category.objects.filter(pk=category_id).first()
        category_note = ""
        if category is None:
            category = Category.objects.first()
            category_note = "（类别无有效建议，回退第一个类别）"

        amounts_missing = [m["name"] for m in members if m["amount"] is None]
        paid = sum((m["amount"] for m in payments), Decimal("0"))
        refunded = sum((m["amount"] for m in refunds), Decimal("0"))
        invoice_total = sum((m["amount"] for m in invoices), Decimal("0"))

        attachments = [
            build_attachment(
                member["kind"],
                SimpleUploadedFile(member["name"], member["data"]),
                {
                    "amount": str(member["amount"]) if member["amount"] is not None else "",
                    "order_no": member["order_no"],
                    "merchant_no": member["merchant_no"],
                    "invoice_no": member["invoice_no"],
                    "ocr": member["ocr"],
                },
            )
            for member in members
        ]

        report = {
            "owner": self._user_label(owner), "note": note, "title": title,
            "category": category.name if category else "", "category_note": category_note,
            "actual": paid - refunded, "invoice": invoice_total,
            "files": [m["name"] for m in members], "amounts_missing": amounts_missing,
            "warnings": [], "created": False,
        }
        if self.dry_run:
            return report

        item = Item(
            owner=owner,
            batch=batch,
            position=Item.next_position(),
            title=title[:200],
            category=category,
            actual_amount=paid - refunded,
            invoice_amount=invoice_total,
            status=Item.STATUS_PENDING,
        )
        mark_attachments(item, [attachment_entry(a) for a in attachments], [])
        item.save()
        for attachment in attachments:
            attachment.item = item
            attachment.save()
        report["created"] = True
        report["warnings"] = check_item(item)
        return report

    # ---------- 报告 ----------

    def _print_report(self, root, records_by_owner, created, singles):
        out = self.stdout.write
        recognized = sum(len(records) for records in records_by_owner.values())
        out("")
        out(f"目录：{root} · 识别成功 {recognized} 张，识别失败 {len(self.detect_failed)} 张")
        if created:
            out("")
            out(f"建条 {len(created)} 条：")
            for index, report in enumerate(created, start=1):
                flag = "已建" if report["created"] else "dry-run"
                out(f"  #{index} [{flag}] {report['owner']} · {report['title']}"
                    f" · 类别 {report['category']}{report['category_note']}"
                    f" · 实付 {report['actual']} · 发票 {report['invoice']}"
                    f" · 附件 {len(report['files'])} 张（{'、'.join(report['files'])}）")
                for name in report["amounts_missing"]:
                    out(f"      金额缺失：{name}")
                for warning in report["warnings"]:
                    out(f"      警告：{warning}")
        else:
            out("未建任何条目。")
        if self.owner_notes:
            out("")
            out("付款人归属：")
            for line in self.owner_notes:
                out(f"  {line}")
        if singles:
            out("")
            out("未成条目：")
            for note, name, reason in singles:
                out(f"  [{note}] {name}：{reason}")
        if self.skipped:
            out("")
            out("跳过文件：")
            for path, reason in self.skipped:
                out(f"  {path}：{reason}")
        if self.detect_failed:
            out("")
            out("识别失败：")
            for name in self.detect_failed:
                out(f"  {name}")
        if self.dry_run:
            out("")
            out("DRY-RUN 未落库")
