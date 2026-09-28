import base64
import json
import shutil
import tempfile
import zipfile
from decimal import Decimal
from io import BytesIO
from unittest import mock

from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from openpyxl import load_workbook

from . import pairing, vision
from .audit import ActorMiddleware
from .models import Attachment, AuditLog, Category, Item
from .validation import check_item

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

TEMP_MEDIA_ROOT = tempfile.mkdtemp()

ALIPAY_ORDER_NO = "20" + "1" * 26  # 28 位，20 开头


def _png_upload(name):
    return SimpleUploadedFile(name, PNG_1X1, content_type="image/png")


def _item_data(**overrides):
    data = {
        "title": "硬盘",
        "category": None,  # 由 setUp 填充
        "actual_amount": "100.00",
        "invoice_amount": "100.00",
        "invoices": [_png_upload("invoice.png")],
        "payments": [_png_upload("payment.png")],
    }
    data.update(overrides)
    return data


class SubmissionTestCase(TestCase):
    """条目提交、附件、校验、导出的公共基建。"""

    def setUp(self):
        # 清空媒体目录：文件不随 TestCase 事务回滚，跨用例残留会让重传同名
        # 文件获得随机后缀，破坏快照的确定性断言。
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)
        self.addCleanup(shutil.rmtree, TEMP_MEDIA_ROOT, ignore_errors=True)
        self.category, _ = Category.objects.get_or_create(
            name="信息服务费", defaults={"order": 0}
        )
        self.student = User.objects.create_user(
            "zhangsan22", password="pw", first_name="张三"
        )
        self.student_b = User.objects.create_user(
            "lisi23", password="pw", first_name="李四"
        )
        self.staff = User.objects.create_user(
            "admin22", password="pw", first_name="管理员", is_staff=True
        )
        self.factory = RequestFactory()

    def _create_item(self, user, **overrides):
        self.client.force_login(user)
        data = _item_data(category=self.category.pk, **overrides)
        response = self.client.post("/items/new/", data)
        self.assertRedirects(response, "/")
        return Item.objects.latest("id")


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class AttachmentSubmissionTests(SubmissionTestCase):
    def test_create_multi_attachment_records_amounts(self):
        response = self.client.force_login(self.student)
        data = _item_data(
            category=self.category.pk,
            actual_amount="148.40",
            invoice_amount="148.40",
            invoices=[_png_upload("a.pdf"), _png_upload("b.pdf")],
            payments=[_png_upload("p.pdf")],
        )
        data["new_invoice_0_amount"] = "67.70"
        data["new_invoice_1_amount"] = "80.70"
        data["new_payment_0_amount"] = "148.40"
        data["new_payment_0_order_no"] = ALIPAY_ORDER_NO
        data["new_invoice_0_ocr"] = '{"buyer_name": "清华大学"}'
        response = self.client.post("/items/new/", data)
        self.assertRedirects(response, "/")

        item = Item.objects.latest("id")
        self.assertEqual(item.attachments.count(), 3)
        self.assertEqual(item.attachments.filter(kind=Attachment.KIND_INVOICE).count(), 2)
        first = item.attachments.filter(kind=Attachment.KIND_INVOICE).order_by("id").first()
        self.assertEqual(str(first.amount), "67.70")
        self.assertEqual(first.ocr_data, {"buyer_name": "清华大学"})
        self.assertEqual(item.attachments.get(kind=Attachment.KIND_PAYMENT).order_no, ALIPAY_ORDER_NO)

    def test_create_warns_on_amount_mismatch(self):
        self.client.force_login(self.student)
        data = _item_data(
            category=self.category.pk,
            actual_amount="100.00",
            invoices=[_png_upload("a.png")],
            payments=[_png_upload("p.png")],
        )
        data["new_payment_0_amount"] = "90.00"
        data["new_payment_0_order_no"] = ALIPAY_ORDER_NO
        response = self.client.post("/items/new/", data, follow=True)
        self.assertRedirects(response, "/")
        self.assertContains(response, "支付与退款附件合计与填写的实付款不一致，请核对")

    def test_create_matching_amounts_has_no_amount_warning(self):
        self.client.force_login(self.student)
        data = _item_data(
            category=self.category.pk,
            actual_amount="179.00",
            invoice_amount="199.00",
            invoices=[_png_upload("a.png")],
            payments=[_png_upload("p.png")],
            refunds=[_png_upload("r.png")],
        )
        data["new_invoice_0_amount"] = "199.00"
        data["new_payment_0_amount"] = "199.00"
        data["new_payment_0_order_no"] = ALIPAY_ORDER_NO
        data["new_refund_0_amount"] = "20.00"
        response = self.client.post("/items/new/", data, follow=True)
        self.assertRedirects(response, "/")
        self.assertNotContains(response, "不一致")

    def test_non_dict_ocr_is_dropped(self):
        self.client.force_login(self.student)
        data = _item_data(category=self.category.pk, payments=[])
        data["new_invoice_0_amount"] = "100.00"
        data["new_invoice_0_ocr"] = "5"
        response = self.client.post("/items/new/", data, follow=True)
        self.assertRedirects(response, "/")
        invoice = Item.objects.latest("id").attachments.get(kind=Attachment.KIND_INVOICE)
        self.assertIsNone(invoice.ocr_data)

    def test_rejects_disallowed_extension(self):
        self.client.force_login(self.student)
        data = _item_data(
            category=self.category.pk,
            invoices=[SimpleUploadedFile("evil.exe", b"x")],
            payments=[],
        )
        response = self.client.post("/items/new/", data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "仅支持 jpg / jpeg / png / pdf 文件")
        self.assertFalse(Item.objects.exists())

    def test_attachment_delete_by_other_user_forbidden(self):
        item = self._create_item(self.student)
        attachment = item.attachments.first()
        self.client.force_login(self.student_b)
        response = self.client.post(f"/items/attachments/{attachment.pk}/delete/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Attachment.objects.filter(pk=attachment.pk).exists())

    def test_attachment_update_edits_amount(self):
        item = self._create_item(self.student)
        attachment = item.attachments.get(kind=Attachment.KIND_PAYMENT)
        self.client.force_login(self.student)
        response = self.client.post(
            f"/items/attachments/{attachment.pk}/edit/",
            {"kind": Attachment.KIND_PAYMENT, "amount": "55.50",
             "order_no": ALIPAY_ORDER_NO, "merchant_no": "", "invoice_no": ""},
        )
        self.assertRedirects(response, f"/items/{item.pk}/edit/")
        attachment.refresh_from_db()
        self.assertEqual(str(attachment.amount), "55.50")

    def test_attachment_delete_by_owner_removes_record(self):
        item = self._create_item(self.student)
        attachment = item.attachments.get(kind=Attachment.KIND_INVOICE)
        self.client.force_login(self.student)
        response = self.client.post(f"/items/attachments/{attachment.pk}/delete/")
        self.assertRedirects(response, f"/items/{item.pk}/edit/")
        self.assertFalse(Attachment.objects.filter(pk=attachment.pk).exists())
        self.assertTrue(item.attachments.filter(kind=Attachment.KIND_PAYMENT).exists())

    def test_file_serve_requires_login(self):
        item = self._create_item(self.student)
        attachment = item.attachments.first()
        self.client.logout()
        response = self.client.get(f"/items/attachments/{attachment.pk}/file/")
        self.assertEqual(response.status_code, 302)

    def test_prefill_view_returns_vision_payload(self):
        self.client.force_login(self.student)
        payload = {"amount": 19.9, "platform": "alipay", "order_no": ALIPAY_ORDER_NO,
                   "merchant_no": "", "_source": "image"}
        with mock.patch("core.views.vision_prefill", return_value=payload) as vision:
            response = self.client.post(
                "/items/prefill/", {"file": _png_upload("p.png"), "kind": "payment"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        self.assertEqual(vision.call_args[0][2], "payment")

    def test_prefill_view_rejects_bad_kind_and_oversize(self):
        self.client.force_login(self.student)
        response = self.client.post(
            "/items/prefill/", {"file": _png_upload("p.png"), "kind": "unknown"}
        )
        self.assertEqual(response.status_code, 400)
        big = SimpleUploadedFile("big.png", b"0" * (10 * 1024 * 1024 + 1),
                                content_type="image/png")
        response = self.client.post("/items/prefill/", {"file": big, "kind": "invoice"})
        self.assertEqual(response.status_code, 400)

    def test_export_excel_header_and_hyperlinks(self):
        item = self._create_item(self.student)
        item.status = Item.STATUS_APPROVED
        item.save()
        self.client.force_login(self.staff)
        response = self.client.get("/export/excel/")
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(
            [cell.value for cell in sheet[1]],
            ["序号", "明细", "类别", "实付款", "付款人", "支付记录", "票据"],
        )
        self.assertIn(f"/items/attachments/{item.first_payment.pk}/file/",
                      sheet.cell(row=2, column=6).hyperlink.target)

    def test_export_zip_names_include_kind_labels(self):
        item = self._create_item(self.student)
        item.status = Item.STATUS_APPROVED
        item.save()
        self.client.force_login(self.staff)
        response = self.client.get("/export/zip/")
        names = zipfile.ZipFile(BytesIO(response.content)).namelist()
        self.assertEqual(len(names), 2)
        self.assertTrue(any("_发票_1." in name for name in names))
        self.assertTrue(any("_支付记录_1." in name for name in names))


@override_settings(EXPECTED_INVOICE_TITLE="", EXPECTED_INVOICE_TAX_ID="")
@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class CheckItemTests(SubmissionTestCase):
    def _item(self, **kwargs):
        data = {
            "owner": self.student,
            "title": "书",
            "category": self.category,
            "actual_amount": "100.00",
            "invoice_amount": "100.00",
        }
        data.update(kwargs)
        item = Item.objects.create(**data)
        item.refresh_from_db()  # 从库回读，字段值与真实请求路径一致（Decimal）
        return item

    def _payment(self, item, **kwargs):
        data = {"item": item, "kind": Attachment.KIND_PAYMENT,
                "file": "attachments/p.png", "amount": "100.00",
                "order_no": ALIPAY_ORDER_NO}
        data.update(kwargs)
        return Attachment.objects.create(**data)

    def test_missing_platform_order_no_warns(self):
        item = self._item()
        self._payment(item, order_no="")
        self.assertIn(
            "某笔支付缺少 28 位平台单号（支付宝订单号/微信交易单号），报销时需抄到发票上",
            check_item(item),
        )

    def test_refund_net_amount_is_clean(self):
        item = self._item(actual_amount="179.00", invoice_amount="199.00")
        self._payment(item, amount="199.00")
        Attachment.objects.create(item=item, kind=Attachment.KIND_REFUND,
                                  file="attachments/r.png", amount="20.00")
        self.assertEqual(check_item(item), [])

    def test_attachment_totals_must_match_item_amounts(self):
        item = self._item(actual_amount="100.00", invoice_amount="100.00")
        self._payment(item, amount="90.00")
        Attachment.objects.create(item=item, kind=Attachment.KIND_INVOICE,
                                  file="attachments/i.png", amount="80.00")
        warnings = check_item(item)
        self.assertIn("支付与退款附件合计与填写的实付款不一致，请核对", warnings)
        self.assertIn("发票附件金额合计与填写的发票金额不一致，请核对", warnings)

    @override_settings(EXPECTED_INVOICE_TITLE="清华大学", EXPECTED_INVOICE_TAX_ID="12345678")
    def test_invoice_buyer_checked_against_expected(self):
        item = self._item()
        Attachment.objects.create(
            item=item, kind=Attachment.KIND_INVOICE, file="attachments/i.png",
            amount="100.00",
            ocr_data={"buyer_name": "北京大学", "buyer_id": "12345678"},
        )
        warnings = check_item(item)
        self.assertIn("发票抬头与要求不符", warnings)
        self.assertNotIn("发票税号与要求不符", warnings)

    @override_settings(EXPECTED_INVOICE_TITLE="清华大学", EXPECTED_INVOICE_TAX_ID="12345678")
    def test_missing_ocr_data_skips_buyer_checks(self):
        item = self._item()
        Attachment.objects.create(item=item, kind=Attachment.KIND_INVOICE,
                                  file="attachments/i.png", amount="100.00")
        self.assertNotIn("发票抬头与要求不符", check_item(item))
        self.assertNotIn("发票税号与要求不符", check_item(item))


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class AuditLogTests(SubmissionTestCase):
    def test_create_logs_full_snapshot(self):
        item = self._create_item(self.student)
        logs = AuditLog.objects.filter(action="create")
        self.assertEqual(logs.count(), 1)
        log = logs.get()
        self.assertEqual(log.actor, self.student)
        self.assertEqual(log.actor_name, "张三")
        self.assertEqual(log.item_pk, item.pk)
        self.assertEqual(log.snapshot["title"], "硬盘")
        self.assertEqual(log.snapshot["actual_amount"], "100.00")
        entries = log.snapshot["attachments"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            sorted(entry["kind"] for entry in entries), ["invoice", "payment"]
        )
        self.assertTrue(entries[0]["file"])

    def test_update_logs_field_diff_only(self):
        item = self._create_item(self.student)
        before = AuditLog.objects.count()
        data = _item_data(
            category=self.category.pk,
            actual_amount="200.00",
            invoices=[_png_upload("invoice2.png")],
            payments=[_png_upload("payment2.png")],
        )
        self.client.post(f"/items/{item.pk}/edit/", data)
        item.refresh_from_db()
        self.assertEqual(AuditLog.objects.count(), before + 1)
        log = AuditLog.objects.filter(action="update").latest("id")
        self.assertEqual(log.actor, self.student)
        self.assertEqual(log.snapshot["actual_amount"], ["100.00", "200.00"])
        self.assertIn("新增 发票 invoice2.png", log.snapshot["attachments"])
        self.assertIn("新增 支付记录 payment2.png", log.snapshot["attachments"])

    def test_attachment_only_change_is_logged(self):
        item = self._create_item(self.student)
        attachment = item.attachments.get(kind=Attachment.KIND_PAYMENT)
        before = AuditLog.objects.count()
        self.client.force_login(self.student)
        self.client.post(
            f"/items/attachments/{attachment.pk}/edit/",
            {"kind": Attachment.KIND_PAYMENT, "amount": "55.50",
             "order_no": ALIPAY_ORDER_NO, "merchant_no": "", "invoice_no": ""},
        )
        self.assertEqual(AuditLog.objects.count(), before + 1)
        log = AuditLog.objects.filter(action="update").latest("id")
        self.assertNotIn("actual_amount", log.snapshot)
        self.assertIn("金额 空 → 55.50", log.snapshot["attachments"])

    def test_no_op_save_writes_nothing(self):
        item = self._create_item(self.student)
        before = AuditLog.objects.count()
        item.save()
        item.save()
        self.assertEqual(AuditLog.objects.count(), before)

    def test_owner_delete(self):
        item = self._create_item(self.student)
        response = self.client.post(f"/items/{item.pk}/delete/")
        self.assertRedirects(response, "/")
        self.assertFalse(Item.objects.filter(pk=item.pk).exists())
        log = AuditLog.objects.filter(action="delete").latest("id")
        self.assertEqual(log.actor, self.student)
        self.assertEqual(log.item_pk, item.pk)
        self.assertIsNone(log.item)
        self.assertEqual(log.snapshot["title"], "硬盘")
        self.assertEqual(log.snapshot["status"], "pending")
        self.assertEqual(len(log.snapshot["attachments"]), 2)

    def test_delete_other_users_item_forbidden(self):
        item = self._create_item(self.student)
        self.client.force_login(self.student_b)
        response = self.client.post(f"/items/{item.pk}/delete/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action="delete").exists())

    def _request_with_messages(self, user):
        request = self.factory.post("/")
        request.user = user
        request._messages = CookieStorage(request)
        return request

    def test_admin_bulk_action_logs_per_item(self):
        item1 = self._create_item(self.student)
        item2 = Item.objects.create(
            owner=self.student, title="书", category=self.category,
            actual_amount="50.00",
        )
        ma = admin.site._registry[Item]
        request = self._request_with_messages(self.staff)
        queryset = Item.objects.filter(pk__in=[item1.pk, item2.pk])
        ActorMiddleware(lambda r: ma.approve_items(r, queryset))(request)
        for item in (item1, item2):
            item.refresh_from_db()
            self.assertEqual(item.status, Item.STATUS_APPROVED)
        logs = AuditLog.objects.filter(action="update")
        self.assertEqual(logs.count(), 2)
        for log in logs:
            self.assertEqual(log.actor, self.staff)
            self.assertEqual(log.snapshot, {"status": ["pending", "approved"]})

    def test_shell_path_has_no_actor(self):
        item = Item.objects.create(
            owner=self.student, title="无请求", category=self.category,
            actual_amount="10.00",
        )
        log = AuditLog.objects.get(action="create", item_pk=item.pk)
        self.assertIsNone(log.actor)
        self.assertEqual(log.actor_name, "")
        self.assertEqual(log.snapshot["attachments"], [])

    def test_update_page_shows_audit_section(self):
        item = self._create_item(self.student)
        response = self.client.get(f"/items/{item.pk}/edit/")
        self.assertContains(response, "操作记录")
        self.assertContains(response, "明细：硬盘")
        self.assertContains(response, "创建")

@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class ItemDetailTests(SubmissionTestCase):
    """看板条目详情模态浮层（issue #13）：所有登录用户可见，编辑入口限本人/管理员。"""

    def test_board_renders_detail_dialog_with_images(self):
        item = self._create_item(self.student)
        invoice = item.attachments.get(kind=Attachment.KIND_INVOICE)
        payment = item.attachments.get(kind=Attachment.KIND_PAYMENT)
        self.client.force_login(self.student)
        response = self.client.get("/")
        self.assertContains(response, f'id="item-detail-{item.pk}"')
        self.assertContains(response, f'src="/items/attachments/{invoice.pk}/file/"')
        self.assertContains(response, f'src="/items/attachments/{payment.pk}/file/"')
        self.assertContains(response, "<h3>发票</h3>")
        self.assertContains(response, "<h3>支付记录</h3>")
        self.assertNotContains(response, "<h3>退款记录</h3>")

    def test_board_shows_refund_group_when_present(self):
        self._create_item(self.student, refunds=[_png_upload("r.png")])
        self.client.force_login(self.student)
        self.assertContains(self.client.get("/"), "<h3>退款记录</h3>")

    def test_pdf_attachment_uses_lazy_iframe(self):
        item = self._create_item(self.student, invoices=[_png_upload("a.pdf")])
        pdf = item.attachments.get(kind=Attachment.KIND_INVOICE)
        self.client.force_login(self.student)
        response = self.client.get("/")
        self.assertContains(response, f'data-src="/items/attachments/{pdf.pk}/file/"')
        self.assertNotContains(response, f'<iframe src="/items/attachments/{pdf.pk}/file/"')

    def test_other_user_sees_images_without_edit_entry(self):
        item = self._create_item(self.student)
        invoice = item.attachments.get(kind=Attachment.KIND_INVOICE)
        self.client.force_login(self.student_b)
        response = self.client.get("/")
        self.assertContains(response, f'src="/items/attachments/{invoice.pk}/file/"')
        self.assertNotContains(response, f"/items/{item.pk}/edit/")

    def test_owner_and_staff_see_edit_entry(self):
        item = self._create_item(self.student)
        edit_url = f"/items/{item.pk}/edit/"
        self.client.force_login(self.student)
        self.assertContains(self.client.get("/"), edit_url)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get("/"), edit_url)

    def test_board_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/").status_code, 302)


class PairingTests(SimpleTestCase):
    """pair() 纯函数：配对规则 1-4 的可观察行为。"""

    @staticmethod
    def _record(rid, kind, amount=None, **extra):
        record = {"id": rid, "kind": kind, "amount": None if amount is None else Decimal(str(amount))}
        record.update(extra)
        return record

    def test_remark_matches_payment_order_no(self):
        result = pairing.pair([
            self._record(0, "invoice", "100", remark_order_no="X1"),
            self._record(1, "payment", "100", order_no="X1"),
        ])
        self.assertEqual(result["groups"], [[0, 1]])
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["unmatched"], [])

    def test_remark_matches_merchant_no_and_merges_duplicates(self):
        result = pairing.pair([
            self._record(0, "invoice", "60", remark_order_no="M9"),
            self._record(1, "invoice", "40", remark_order_no="M9"),
            self._record(2, "payment", "60", merchant_no="M9"),
            self._record(3, "payment", "40", merchant_no="M9"),
        ])
        self.assertEqual(result["groups"], [[0, 1, 2, 3]])

    def test_invoice_hitting_two_payment_groups_merges_them(self):
        result = pairing.pair([
            self._record(0, "invoice", "30", remark_order_no="X1"),
            self._record(1, "payment", "10", order_no="X1"),
            self._record(2, "payment", "20", merchant_no="X1"),
        ])
        self.assertEqual(result["groups"], [[0, 1, 2]])

    def test_refund_attaches_by_order_no(self):
        result = pairing.pair([
            self._record(0, "invoice", "80", remark_order_no="X1"),
            self._record(1, "payment", "100", order_no="X1"),
            self._record(2, "refund", "20", order_no="X1"),
        ])
        self.assertEqual(result["groups"], [[0, 1, 2]])
        self.assertEqual(result["unmatched"], [])

    def test_refund_attaches_by_amount_fallback(self):
        result = pairing.pair([
            self._record(0, "invoice", "80", remark_order_no="X1"),
            self._record(1, "payment", "100", order_no="X1"),
            self._record(2, "refund", "100"),
        ])
        self.assertEqual(result["groups"], [[0, 1, 2]])

    def test_refund_without_payment_is_unmatched(self):
        result = pairing.pair([self._record(0, "refund", "5", order_no="NOPE")])
        self.assertEqual(result["unmatched"], [{"id": 0, "reason": "退款记录找不到对应支付记录"}])

    def test_amount_fallback_pairs_unique_candidates(self):
        result = pairing.pair([
            self._record(0, "invoice", "199"),
            self._record(1, "payment", "199"),
        ])
        self.assertEqual(result["groups"], [[0, 1]])

    def test_amount_fallback_multi_candidates_go_pending(self):
        result = pairing.pair([
            self._record(0, "invoice", "50"),
            self._record(1, "invoice", "50"),
            self._record(2, "payment", "50"),
            self._record(3, "payment", "50"),
        ])
        self.assertEqual(result["pending"], [[0, 1, 2, 3]])
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["unmatched"], [])

    def test_all_unmatched_carry_reasons(self):
        result = pairing.pair([
            self._record(0, "invoice", "10"),
            self._record(1, "payment", "99"),
            self._record(2, "unknown"),
        ])
        self.assertEqual(result["unmatched"], [
            {"id": 0, "reason": "找不到对应支付记录"},
            {"id": 1, "reason": "找不到对应发票"},
            {"id": 2, "reason": "未能识别票据类型"},
        ])



@override_settings(DEEPSEEK_API_KEY="test-key")
class VisionDetectTests(SimpleTestCase):
    """detect / group_suggest / field_suggest 的解析与降级行为。"""

    @staticmethod
    def _response(content):
        return {"choices": [{"message": {"content": content}}]}

    def test_detect_returns_kind_and_fields(self):
        content = json.dumps({
            "kind": "payment", "amount": 12.5, "platform": "alipay",
            "order_no": ALIPAY_ORDER_NO, "merchant_no": "M123",
            "invoice_amount": None, "invoice_no": "", "remark_order_no": "",
            "buyer_name": "", "buyer_id": "", "items_summary": "",
        })
        with mock.patch("core.vision._post", return_value=self._response(content)) as post:
            result = vision.detect(PNG_1X1, "p.png")
        self.assertTrue(post.called)
        self.assertEqual(result["kind"], "payment")
        self.assertEqual(result["amount"], 12.5)
        self.assertEqual(result["platform"], "alipay")
        self.assertEqual(result["order_no"], ALIPAY_ORDER_NO)
        self.assertEqual(result["_source"], "image")

    def test_detect_invoice_passes_items_summary(self):
        content = json.dumps({
            "kind": "invoice", "invoice_amount": 100, "invoice_no": "INV1",
            "remark_order_no": "X1", "buyer_name": "清华大学", "buyer_id": "TAX1",
            "items_summary": "*信息技术服务*云存储", "amount": None,
            "platform": "unknown", "order_no": "", "merchant_no": "",
        })
        with mock.patch("core.vision._post", return_value=self._response(content)):
            result = vision.detect(PNG_1X1, "i.png")
        self.assertEqual(result["kind"], "invoice")
        self.assertEqual(result["invoice_amount"], 100)
        self.assertEqual(result["items_summary"], "*信息技术服务*云存储")
        self.assertEqual(result["remark_order_no"], "X1")

    def test_detect_garbage_returns_empty(self):
        with mock.patch("core.vision._post", return_value=self._response("not json")):
            self.assertEqual(vision.detect(PNG_1X1, "p.png"), {})

    def test_detect_unconfigured_returns_empty(self):
        with override_settings(DEEPSEEK_API_KEY=""):
            with mock.patch("core.vision._post") as post:
                self.assertEqual(vision.detect(PNG_1X1, "p.png"), {})
        self.assertFalse(post.called)

    def test_group_suggest_filters_unknown_ids_and_short_groups(self):
        content = json.dumps({"groups": [[7, 8, 99], ["bad"], [9]]})
        records = [
            {"id": 7, "kind": "invoice", "amount": "30", "order_no": "", "merchant_no": "", "remark_order_no": ""},
            {"id": 8, "kind": "payment", "amount": "30", "order_no": "Z", "merchant_no": "", "remark_order_no": ""},
            {"id": 9, "kind": "refund", "amount": "1", "order_no": "", "merchant_no": "", "remark_order_no": ""},
        ]
        with mock.patch("core.vision._post", return_value=self._response(content)):
            self.assertEqual(vision.group_suggest(records), [[7, 8]])

    def test_field_suggest_drops_invalid_category(self):
        content = json.dumps({"suggestions": [
            {"title": "打印费", "category_id": 1}, {"title": "书", "category_id": 999}, "bad",
        ]})
        with mock.patch("core.vision._post", return_value=self._response(content)):
            result = vision.field_suggest(
                [[{"id": 0, "kind": "invoice", "amount": "10", "items_summary": "打印"}]],
                [{"id": 1, "name": "书籍", "description": ""}],
            )
        self.assertEqual(result["suggestions"][0], {"title": "打印费", "category_id": 1})
        self.assertEqual(result["suggestions"][1]["title"], "书")
        self.assertIsNone(result["suggestions"][1]["category_id"])
        self.assertIsNone(result["suggestions"][2])



@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class BatchSubmitTests(SubmissionTestCase):
    def _post_batch(self, groups, files):
        return self.client.post(
            "/items/batch/submit/", {"plan": json.dumps({"groups": groups}), "files": files}
        )

    def test_submit_creates_pending_items_with_attachments(self):
        self.client.force_login(self.student)
        response = self._post_batch([
            {
                "title": "硬盘", "category": self.category.pk,
                "actual_amount": "80.00", "invoice_amount": "100.00",
                "files": [
                    {"index": 0, "kind": "invoice", "amount": "100.00", "invoice_no": "INV1",
                     "ocr": {"buyer_name": "清华大学"}},
                    {"index": 1, "kind": "payment", "amount": "100.00", "order_no": ALIPAY_ORDER_NO},
                    {"index": 2, "kind": "refund", "amount": "20.00", "merchant_no": "R1"},
                ],
            },
            {
                "title": "打印费", "category": self.category.pk,
                "actual_amount": "5.00", "invoice_amount": "5.00",
                "files": [
                    {"index": 3, "kind": "invoice", "amount": "5.00"},
                    {"index": 4, "kind": "payment", "amount": "5.00"},
                ],
            },
        ], [
            _png_upload("invoice.png"), _png_upload("payment.png"), _png_upload("refund.png"),
            _png_upload("invoice2.png"), _png_upload("payment2.png"),
        ])
        self.assertRedirects(response, "/")
        self.assertEqual(Item.objects.count(), 2)
        item = Item.objects.order_by("id").first()
        self.assertEqual(item.owner, self.student)
        self.assertEqual(item.title, "硬盘")
        self.assertEqual(item.status, Item.STATUS_PENDING)
        self.assertEqual(str(item.actual_amount), "80.00")
        self.assertEqual(item.attachments.count(), 3)
        payment = item.attachments.get(kind=Attachment.KIND_PAYMENT)
        self.assertEqual(str(payment.amount), "100.00")
        self.assertEqual(payment.order_no, ALIPAY_ORDER_NO)
        invoice = item.attachments.get(kind=Attachment.KIND_INVOICE)
        self.assertEqual(invoice.ocr_data, {"buyer_name": "清华大学"})
        log = AuditLog.objects.get(action="create", item_pk=item.pk)
        self.assertEqual(len(log.snapshot["attachments"]), 3)

    def test_single_sided_group_rejected_without_items(self):
        self.client.force_login(self.student)
        response = self._post_batch([
            {
                "title": "只有发票", "category": self.category.pk,
                "actual_amount": "10.00",
                "files": [{"index": 0, "kind": "invoice", "amount": "10.00"}],
            },
        ], [_png_upload("invoice.png")])
        self.assertEqual(response.status_code, 400)
        self.assertIn("缺少发票或支付记录", response.json()["error"])
        self.assertFalse(Item.objects.exists())

    def test_requires_login(self):
        response = self.client.post("/items/batch/submit/", {})
        self.assertEqual(response.status_code, 302)



@override_settings(DEEPSEEK_API_KEY="test-key")
class BatchPairViewTests(SubmissionTestCase):
    def _post_pair(self, records):
        return self.client.post(
            "/items/batch/pair/", json.dumps({"records": records}), content_type="application/json"
        )

    def test_rule_groups_returned_when_unconfigured(self):
        self.client.force_login(self.student)
        with override_settings(DEEPSEEK_API_KEY=""):
            response = self._post_pair([
                {"id": 0, "kind": "invoice", "amount": "100", "remark_order_no": "X1"},
                {"id": 1, "kind": "payment", "amount": "100", "order_no": "X1"},
            ])
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["groups"], [[0, 1]])
        self.assertEqual(data["pending"], [])
        self.assertEqual(data["unmatched"], [])
        self.assertEqual(data["suggestions"], {})

    def test_llm_groups_merged_with_suggestions(self):
        self.client.force_login(self.student)
        with mock.patch("core.vision.group_suggest", return_value=[[0, 1]]) as group_mock:
            with mock.patch(
                "core.vision.field_suggest",
                return_value={"suggestions": [{"title": "打印费", "category_id": self.category.pk}]},
            ) as field_mock:
                response = self._post_pair([
                    {"id": 0, "kind": "invoice", "amount": "30"},
                    {"id": 1, "kind": "payment", "amount": "35", "order_no": "Z9"},
                ])
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["groups"], [[0, 1]])
        self.assertEqual(data["unmatched"], [])
        self.assertEqual(group_mock.call_args[0][0][0]["id"], 0)
        self.assertEqual(field_mock.call_args[0][0][0][0]["id"], 0)
        self.assertEqual(data["suggestions"]["suggestions"][0]["title"], "打印费")

    def test_requires_login(self):
        response = self.client.post("/items/batch/pair/", "{}", content_type="application/json")
        self.assertEqual(response.status_code, 302)
