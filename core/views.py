import json
import zipfile
from io import BytesIO
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from openpyxl import Workbook
from openpyxl.styles import Font

from . import pairing, vision
from .attachments import KIND_LABELS, attachment_groups, build_attachment, decimal_or_none, plan_decimal
from .audit import attachment_entries, attachment_entry, mark_attachments
from .forms import AttachmentForm, ItemPanelForm
from .models import Attachment, Batch, Category, Item
from .suggest import category_payloads, llm_merge_groups, suggest_member
from .validation import check_item
from .vision import prefill as vision_prefill, validate_upload

STATUS_FILTERS = {Item.STATUS_PENDING, Item.STATUS_APPROVED, Item.STATUS_REJECTED}
AMOUNT_FORMAT = "0.00_ "

# 上传文件字段名、POST 字段前缀、附件类型 三元组
UPLOAD_FIELDS = (
    ("invoices", "new_invoice", Attachment.KIND_INVOICE),
    ("payments", "new_payment", Attachment.KIND_PAYMENT),
    ("refunds", "new_refund", Attachment.KIND_REFUND),
)


def _is_fetch(request):
    return request.headers.get("x-requested-with") == "fetch"


def _first_error(form, errors):
    for error in errors:
        return error
    for field_errors in form.errors.values():
        for error in field_errors:
            return str(error)
    return "保存失败"


@login_required
def board(request):
    """表格主视图：按批次展示全部条目，seq 为导出序号；筛选/行内编辑/拖拽调序由前端承载。"""
    batches = Batch.objects.all()
    current = None
    batch_id = request.GET.get("batch", "")
    if batch_id.isdigit():
        current = batches.filter(pk=batch_id).first()
    if current is None:
        current = batches.first()

    rows, payers, seen_payers = [], [], set()
    if current is not None:
        items = (
            current.items.select_related("owner", "category")
            .prefetch_related("attachments")
            .order_by("category__order", "category__id", "position", "id")
        )
        for seq, item in enumerate(items, start=1):
            counts = {"invoice": 0, "payment": 0, "refund": 0}
            for attachment in item.attachments.all():
                if attachment.kind in counts:
                    counts[attachment.kind] += 1
            if item.owner_id not in seen_payers:
                seen_payers.add(item.owner_id)
                payers.append(item.owner)
            rows.append({
                "seq": seq,
                "item": item,
                "can_edit": request.user.is_staff or item.owner_id == request.user.id,
                "counts": counts,
            })

    status = request.GET.get("status", "")
    category_id = request.GET.get("category", "")
    payer_id = request.GET.get("payer", "")
    query = request.GET.get("q", "").strip()
    if status in STATUS_FILTERS:
        rows = [row for row in rows if row["item"].status == status]
    if category_id.isdigit():
        rows = [row for row in rows if str(row["item"].category_id) == category_id]
    if payer_id.isdigit():
        rows = [row for row in rows if str(row["item"].owner_id) == payer_id]
    if query:
        rows = [row for row in rows if query in row["item"].title]

    return render(request, "core/board.html", {
        "batches": batches,
        "current_batch": current,
        "categories": Category.objects.all(),
        "payers": payers,
        "rows": rows,
        "status": status,
        "category_id": category_id,
        "payer_id": payer_id,
        "q": query,
        "status_choices": Item.STATUS_CHOICES,
    })


@login_required
def item_create(request):
    batch = Batch.objects.first()
    if request.method == "POST":
        if batch is None:
            messages.error(request, "请先创建批次")
            return redirect("board")
        form = ItemPanelForm(request.POST, request.FILES, staff=request.user.is_staff)
        attachments, errors = _collect_new_attachments(request)
        if form.is_valid() and not errors:
            item = form.save(commit=False)
            if not request.user.is_staff:
                item.owner = request.user
            item.batch = batch
            item.position = Item.next_position()
            mark_attachments(item, [attachment_entry(a) for a in attachments], [])
            item.save()
            for attachment in attachments:
                attachment.item = item
                attachment.save()
            if _is_fetch(request):
                return JsonResponse({"ok": True, "warnings": check_item(item)})
            for warning in check_item(item):
                messages.warning(request, warning)
            messages.success(request, "已提交，等待管理员审核。")
            return redirect("board")
        for error in errors:
            form.add_error(None, error)
        if _is_fetch(request):
            return JsonResponse({"error": _first_error(form, errors)}, status=400)
    else:
        form = ItemPanelForm(staff=request.user.is_staff)
    return render(request, "core/item_panel.html", {
        "form": form,
        "is_create": True,
        "can_edit": True,
        "is_staff": request.user.is_staff,
    })


@login_required
def item_update(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if item.owner_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied("只能修改自己的条目")
    if request.method == "POST":
        form = ItemPanelForm(request.POST, request.FILES, instance=item, staff=request.user.is_staff)
        attachments, errors = _collect_new_attachments(request)
        if form.is_valid() and not errors:
            before = attachment_entries(item)
            item = form.save(commit=False)
            if not request.user.is_staff:
                item.owner = request.user
            mark_attachments(item, before + [attachment_entry(a) for a in attachments], before)
            item.save()
            for attachment in attachments:
                attachment.item = item
                attachment.save()
            if _is_fetch(request):
                return JsonResponse({"ok": True, "warnings": check_item(item)})
            for warning in check_item(item):
                messages.warning(request, warning)
            messages.success(request, "已更新。")
            return redirect("board")
        for error in errors:
            form.add_error(None, error)
        if _is_fetch(request):
            return JsonResponse({"error": _first_error(form, errors)}, status=400)
    else:
        form = ItemPanelForm(instance=item, staff=request.user.is_staff)
    return render(request, "core/item_panel.html", {
        "form": form,
        "item": item,
        "is_create": False,
        "can_edit": True,
        "is_staff": request.user.is_staff,
        "audit_logs": item.audit_logs.all(),
        "attachment_groups": attachment_groups(item),
        "warnings": check_item(item),
    })


@login_required
def item_panel(request, pk):
    """条目侧边栏片段：登录即可看（含附件与留痕），编辑控件限本人/staff。"""
    item = get_object_or_404(
        Item.objects.select_related("owner", "category", "batch"), pk=pk
    )
    can_edit = request.user.is_staff or item.owner_id == request.user.id
    context = {
        "item": item,
        "is_create": False,
        "can_edit": can_edit,
        "is_staff": request.user.is_staff,
        "audit_logs": item.audit_logs.all(),
        "attachment_groups": attachment_groups(item),
        "warnings": check_item(item),
    }
    if can_edit:
        context["form"] = ItemPanelForm(instance=item, staff=request.user.is_staff)
    return render(request, "core/item_panel.html", context)


@login_required
@require_POST
def item_field_update(request, pk):
    """表格行内单字段保存；状态/付款人仅 staff。"""
    item = get_object_or_404(Item, pk=pk)
    if item.owner_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied("只能修改自己的条目")
    try:
        payload = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    field = payload.get("field") if isinstance(payload, dict) else None
    value = payload.get("value") if isinstance(payload, dict) else None

    student_fields = {"title", "category", "actual_amount", "invoice_amount"}
    staff_fields = {"status", "owner"}
    if field in staff_fields and not request.user.is_staff:
        raise PermissionDenied("只有管理员可以修改审核状态或付款人")
    if field not in student_fields | staff_fields:
        return JsonResponse({"error": "不允许修改的字段"}, status=400)

    try:
        if field == "title":
            item.title = str(value or "").strip()[:200]
        elif field == "category":
            category = Category.objects.filter(pk=value).first()
            if category is None:
                return JsonResponse({"error": "类别无效"}, status=400)
            item.category = category
            item.position = Item.next_position()  # 行内改类别：落到目标类别末尾
        elif field in ("actual_amount", "invoice_amount"):
            try:
                setattr(item, field, plan_decimal(value, "金额"))
            except ValueError as exc:
                return JsonResponse({"error": str(exc)}, status=400)
        elif field == "status":
            if value not in STATUS_FILTERS:
                return JsonResponse({"error": "状态无效"}, status=400)
            item.status = value
        elif field == "owner":
            owner = User.objects.filter(pk=value, is_active=True).first()
            if owner is None:
                return JsonResponse({"error": "付款人无效"}, status=400)
            item.owner = owner
        item.full_clean()
    except ValidationError as exc:
        message = exc.message_dict and next(iter(exc.message_dict.values()))[0] or exc.messages[0]
        return JsonResponse({"error": message}, status=400)
    item.save()
    return JsonResponse({"ok": True, "warnings": check_item(item)})


@login_required
@require_POST
def item_reorder(request):
    """staff 拖拽调序：order 必须恰为该类别下当前批次的全部条目。"""
    if not request.user.is_staff:
        raise PermissionDenied("只有管理员可以调整顺序")
    try:
        payload = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    order = payload.get("order") if isinstance(payload, dict) else None
    category = Category.objects.filter(pk=payload.get("category")).first() if isinstance(payload, dict) else None
    if category is None or not isinstance(order, list) or not order:
        return JsonResponse({"error": "参数无效"}, status=400)
    pks = []
    for pk in order:
        if not isinstance(pk, int) or isinstance(pk, bool):
            return JsonResponse({"error": "order 元素必须是条目 id"}, status=400)
        pks.append(pk)
    if len(set(pks)) != len(pks):
        return JsonResponse({"error": "order 存在重复条目"}, status=400)
    items = {obj.pk: obj for obj in Item.objects.filter(pk__in=pks)}
    if len(items) != len(pks):
        return JsonResponse({"error": "order 含不存在的条目"}, status=400)
    if any(obj.category_id != category.pk for obj in items.values()):
        return JsonResponse({"error": "order 含跨类别的条目"}, status=400)
    batch_id = next(iter(items.values())).batch_id
    expected = set(
        Item.objects.filter(batch_id=batch_id, category=category).values_list("pk", flat=True)
    )
    if set(pks) != expected:
        return JsonResponse({"error": "order 必须覆盖该类别下的全部条目"}, status=400)
    with transaction.atomic():
        position = Item.next_position()
        for pk in pks:
            item = items[pk]
            item.position = position
            item.save()
            position += 1
    return JsonResponse({"ok": True})


@login_required
@require_POST
def item_delete(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if item.owner_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied("只能删除自己的条目")
    mark_attachments(item, attachment_entries(item), [])
    item.delete()
    if _is_fetch(request):
        return JsonResponse({"ok": True})
    messages.success(request, "已删除。")
    return redirect("board")


@login_required
@require_POST
def batch_add(request):
    if not request.user.is_staff:
        raise PermissionDenied("只有管理员可以创建批次")
    name = (request.POST.get("name") or "").strip()[:100]
    if not name:
        messages.error(request, "批次名称不能为空")
        return redirect("board")
    if Batch.objects.filter(name=name).exists():
        messages.error(request, f"批次 {name} 已存在")
        return redirect("board")
    Batch.objects.create(name=name)
    messages.success(request, f"已创建批次 {name}")
    return redirect("board")


@login_required
def file_serve(request, pk):
    attachment = get_object_or_404(Attachment, pk=pk)
    return FileResponse(attachment.file.open("rb"), as_attachment=False)


@login_required
@require_POST
def attachment_update(request, pk):
    attachment = _owned_attachment(request, pk)
    item = attachment.item
    before = attachment_entries(item)
    form = AttachmentForm(request.POST, instance=attachment)
    if form.is_valid():
        form.save()
        mark_attachments(item, attachment_entries(item), before)
        item.save()
        if _is_fetch(request):
            return JsonResponse({"ok": True})
        messages.success(request, "附件已更新。")
    else:
        error = next(iter(form.errors.values()))[0]
        if _is_fetch(request):
            return JsonResponse({"error": f"附件保存失败：{error}"}, status=400)
        messages.error(request, f"附件保存失败：{error}")
    return redirect("board")


@login_required
@require_POST
def attachment_delete(request, pk):
    attachment = _owned_attachment(request, pk)
    item = attachment.item
    before = attachment_entries(item)
    attachment.file.delete(save=False)
    attachment.delete()
    mark_attachments(item, attachment_entries(item), before)
    item.save()
    if _is_fetch(request):
        return JsonResponse({"ok": True})
    messages.success(request, "已删除附件。")
    return redirect("board")


@require_POST
@login_required
def prefill(request):
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "缺少 file 字段"}, status=400)
    kind = request.POST.get("kind", "")
    if kind not in KIND_LABELS:
        return JsonResponse({"error": "kind 必须是 invoice/payment/refund"}, status=400)
    error = validate_upload(upload)
    if error:
        return JsonResponse({"error": error}, status=400)
    return JsonResponse(vision_prefill(upload.read(), upload.name, kind))


@login_required
def batch_create(request):
    """批量配对提交页：混合上传 → 识别 → 配对 → 确认创建。"""
    categories = Category.objects.all()
    notes = [f"{category.name}：{category.description}" for category in categories if category.description]
    return render(request, "core/batch_form.html", {
        "vision_configured": vision.configured(),
        "categories": category_payloads(),
        "category_notes": "；".join(notes),
    })


@require_POST
@login_required
def batch_detect(request):
    """批量页逐张识别：判类型并提取字段，不落库。"""
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "缺少 file 字段"}, status=400)
    error = validate_upload(upload)
    if error:
        return JsonResponse({"error": error}, status=400)
    return JsonResponse(vision.detect(upload.read(), upload.name))


@require_POST
@login_required
def batch_pair(request):
    """规则配对 + LLM 兜底：返回分组、待确认组、未配对与字段建议。"""
    try:
        payload = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    raw_records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(raw_records, list):
        return JsonResponse({"error": "缺少 records 列表"}, status=400)

    records = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            return JsonResponse({"error": "records 元素必须是对象"}, status=400)
        records.append({
            "id": raw.get("id"),
            "kind": raw.get("kind"),
            "amount": decimal_or_none(raw.get("amount")),
            "order_no": raw.get("order_no") or "",
            "merchant_no": raw.get("merchant_no") or "",
            "invoice_no": raw.get("invoice_no") or "",
            "remark_order_no": raw.get("remark_order_no") or "",
            "items_summary": raw.get("items_summary") or "",
        })

    result = pairing.pair(records)
    groups, pending, unmatched = result["groups"], result["pending"], result["unmatched"]
    by_id = {record["id"]: record for record in records}
    if vision.configured():
        groups, pending, unmatched = llm_merge_groups(by_id, groups, pending, unmatched)
    suggestions = {}
    if vision.configured() and groups:
        suggestions = vision.field_suggest(
            [[suggest_member(by_id[rid]) for rid in group] for group in groups], category_payloads()
        )
    return JsonResponse({
        "groups": groups, "pending": pending, "unmatched": unmatched, "suggestions": suggestions,
    })


@require_POST
@login_required
def batch_submit(request):
    """按确认后的分组计划一次性创建多条待审核条目；任一校验失败整体拒绝，不建任何条目。"""
    uploads = request.FILES.getlist("files")
    try:
        plan = json.loads(request.POST.get("plan") or "")
    except ValueError:
        return JsonResponse({"error": "plan 不是合法 JSON"}, status=400)
    raw_groups = plan.get("groups") if isinstance(plan, dict) else None
    if not isinstance(raw_groups, list) or not raw_groups:
        return JsonResponse({"error": "缺少分组计划"}, status=400)
    batch = Batch.objects.first()
    if batch is None:
        return JsonResponse({"error": "请先创建批次"}, status=400)

    prepared = []
    for number, group in enumerate(raw_groups, start=1):
        if not isinstance(group, dict):
            return JsonResponse({"error": f"第 {number} 组信息无效"}, status=400)
        title = str(group.get("title") or "").strip()[:200]
        if not title:
            return JsonResponse({"error": f"第 {number} 组缺少明细"}, status=400)
        category = Category.objects.filter(pk=group.get("category")).first()
        if category is None:
            return JsonResponse({"error": f"第 {number} 组类别无效"}, status=400)
        try:
            actual_amount = plan_decimal(group.get("actual_amount"), "实付金额")
        except ValueError as exc:
            return JsonResponse({"error": f"第 {number} 组{exc}"}, status=400)
        if actual_amount is None:
            return JsonResponse({"error": f"第 {number} 组缺少实付金额"}, status=400)
        try:
            invoice_amount = plan_decimal(group.get("invoice_amount"), "发票金额")
        except ValueError as exc:
            return JsonResponse({"error": f"第 {number} 组{exc}"}, status=400)
        entries = group.get("files")
        if not isinstance(entries, list) or not entries:
            return JsonResponse({"error": f"第 {number} 组缺少文件"}, status=400)
        attachments, kinds = [], set()
        for entry in entries:
            if not isinstance(entry, dict):
                return JsonResponse({"error": f"第 {number} 组文件信息无效"}, status=400)
            kind = entry.get("kind")
            if kind not in KIND_LABELS:
                return JsonResponse({"error": f"第 {number} 组含未识别类型的文件，请先修正类型"}, status=400)
            try:
                upload = uploads[int(entry.get("index"))]
            except (TypeError, ValueError, IndexError):
                return JsonResponse({"error": f"第 {number} 组文件序号无效"}, status=400)
            error = validate_upload(upload)
            if error:
                return JsonResponse({"error": f"{upload.name}：{error}"}, status=400)
            try:
                attachments.append(build_attachment(kind, upload, entry))
            except ValueError as exc:
                return JsonResponse({"error": f"{upload.name}：{exc}"}, status=400)
            kinds.add(kind)
        if Attachment.KIND_INVOICE not in kinds or Attachment.KIND_PAYMENT not in kinds:
            return JsonResponse({"error": f"第 {number} 组缺少发票或支付记录，无法报销"}, status=400)
        prepared.append((title, category, actual_amount, invoice_amount, attachments))

    with transaction.atomic():
        for title, category, actual_amount, invoice_amount, attachments in prepared:
            item = Item(
                owner=request.user,
                batch=batch,
                position=Item.next_position(),
                title=title,
                category=category,
                actual_amount=actual_amount,
                invoice_amount=invoice_amount,
                status=Item.STATUS_PENDING,
            )
            mark_attachments(item, [attachment_entry(a) for a in attachments], [])
            item.save()
            for attachment in attachments:
                attachment.item = item
                attachment.save()
            for warning in check_item(item):
                messages.warning(request, f"{title}：{warning}")
    messages.success(request, f"已批量提交 {len(prepared)} 条，等待管理员审核。")
    return redirect("board")


def _owned_attachment(request, pk):
    attachment = get_object_or_404(Attachment, pk=pk)
    if attachment.item.owner_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied("只能修改自己的条目")
    return attachment


def _collect_new_attachments(request):
    """从 POST 的文件与同名前缀字段构造未保存的 Attachment；返回 (附件, 错误文案)。"""
    attachments, errors = [], []
    for field, prefix, kind in UPLOAD_FIELDS:
        for index, upload in enumerate(request.FILES.getlist(field)):
            error = validate_upload(upload)
            if error:
                errors.append(f"{upload.name}：{error}")
                continue
            try:
                attachments.append(_new_attachment(request, upload, prefix, index, kind))
            except ValueError as exc:
                errors.append(f"{upload.name}：{exc}")
    return attachments, errors


def _new_attachment(request, upload, prefix, index, kind):
    def value(name):
        return (request.POST.get(f"{prefix}_{index}_{name}") or "").strip()

    return build_attachment(kind, upload, {
        "amount": value("amount"),
        "order_no": value("order_no"),
        "merchant_no": value("merchant_no"),
        "invoice_no": value("invoice_no"),
        "ocr": value("ocr"),
    })


def _staff(user):
    return user.is_staff


def _current_batch(request):
    batch_id = request.GET.get("batch", "")
    if batch_id.isdigit():
        batch = Batch.objects.filter(pk=batch_id).first()
        if batch is not None:
            return batch
    return Batch.objects.first()


def _approved_by_category(batch):
    """当前批次 approved 条目，按类别顺序 + 持久化顺序排序，返回 [(category, items)]。"""
    approved = (
        Item.objects.filter(status=Item.STATUS_APPROVED, batch=batch)
        .select_related("owner", "category")
        .prefetch_related("attachments")
        .order_by("category__order", "category__id", "position", "id")
    )
    return [
        (category, list(group))
        for category, group in groupby(approved, key=lambda item: item.category)
    ]


def _download_filename(prefix, batch, extension):
    """批次名只去除会破坏 Content-Disposition 的字符（引号/换行），中文原样保留。"""
    name = (batch.name if batch else "").replace('"', "").replace("\r", "").replace("\n", "")
    stem = f"{prefix}_{name}" if name else prefix
    return f'attachment; filename="{stem}.{extension}"'


@user_passes_test(_staff)
def export_excel(request):
    batch = _current_batch(request)
    wb = Workbook()
    ws = wb.active
    ws.title = "报销明细"
    header = ["序号", "明细", "类别", "实付款", "付款人", "支付记录", "票据"]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    seq = 0
    subtotal_rows = []
    for category, items in _approved_by_category(batch):
        start = None
        for item in items:
            seq += 1
            ws.append([
                seq,
                item.title,
                category.name,
                float(item.actual_amount),
                item.payer_name,
                "已整理",
                "已整理",
            ])
            row = ws.max_row
            amount_cell = ws.cell(row=row, column=4)
            amount_cell.number_format = AMOUNT_FORMAT
            # 多张附件时超链接指向首张，完整材料走 zip 导出（ADR-0005）
            if item.first_payment is not None:
                ws.cell(row=row, column=6).hyperlink = request.build_absolute_uri(
                    reverse("file_serve", args=[item.first_payment.pk])
                )
            if item.first_invoice is not None:
                ws.cell(row=row, column=7).hyperlink = request.build_absolute_uri(
                    reverse("file_serve", args=[item.first_invoice.pk])
                )
            if start is None:
                start = row
        ws.append(["小记", None, None, f"=SUM(D{start}:D{ws.max_row})", None, None, None])
        subtotal_row = ws.max_row
        ws.cell(row=subtotal_row, column=4).number_format = AMOUNT_FORMAT
        subtotal_rows.append(subtotal_row)

    if subtotal_rows:
        ws.append(["合计", None, None, "=" + "+".join(f"D{r}" for r in subtotal_rows), None, None, None])
        ws.cell(row=ws.max_row, column=4).number_format = AMOUNT_FORMAT

    widths = {"A": 6, "B": 40, "C": 12, "D": 10, "E": 10, "F": 10, "G": 10}
    for column, width in widths.items():
        ws.column_dimensions[column].width = width

    buffer = BytesIO()
    wb.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = _download_filename("报销汇总", batch, "xlsx")
    return response


@user_passes_test(_staff)
def export_zip(request):
    batch = _current_batch(request)
    buffer = BytesIO()
    seen = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        seq = 0
        for category, items in _approved_by_category(batch):
            for item in items:
                seq += 1  # 与 Excel"序号"列一致（全局连续）
                counters = {}
                for attachment in item.attachments.all():
                    kind_label = KIND_LABELS[attachment.kind]
                    counters[kind_label] = counters.get(kind_label, 0) + 1
                    extension = attachment.file.name.rsplit(".", 1)[-1]
                    name = (
                        f"{category.name}_{seq:02d}_{item.payer_name}"
                        f"_{kind_label}_{counters[kind_label]}.{extension}"
                    )
                    if name in seen:
                        name = (
                            f"{category.name}_{seq:02d}_{item.payer_name}"
                            f"_{kind_label}_{counters[kind_label]}_{item.pk}.{extension}"
                        )
                    seen.add(name)
                    zf.writestr(name, attachment.file.read())
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = _download_filename("报销材料", batch, "zip")
    return response
