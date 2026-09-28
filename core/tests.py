import base64
import shutil
import tempfile

from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from .audit import ActorMiddleware
from .models import AuditLog, Category, Item

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

TEMP_MEDIA_ROOT = tempfile.mkdtemp()


def _png_upload(name):
    return SimpleUploadedFile(name, PNG_1X1, content_type="image/png")


def _item_data(**overrides):
    data = {
        "title": "硬盘",
        "category": None,  # 由 setUp 填充
        "actual_amount": "100.00",
        "invoice_amount": "100.00",
        "invoice_no": "12345678",
        "order_no": "order-001",
        "invoice_file": _png_upload("invoice.png"),
        "payment_screenshot": _png_upload("payment.png"),
    }
    data.update(overrides)
    return data


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class AuditLogTests(TestCase):
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

    def _create_item(self, user):
        self.client.force_login(user)
        data = _item_data(category=self.category.pk)
        response = self.client.post("/items/new/", data)
        self.assertRedirects(response, "/")
        return Item.objects.get(order_no="order-001")

    def test_create_logs_full_snapshot(self):
        item = self._create_item(self.student)
        logs = AuditLog.objects.filter(action="create")
        self.assertEqual(logs.count(), 1)
        log = logs.get()
        self.assertEqual(log.actor, self.student)
        self.assertEqual(log.actor_name, "张三")
        self.assertEqual(log.item_pk, item.pk)
        self.assertEqual(log.snapshot["title"], "硬盘")
        self.assertEqual(log.snapshot["order_no"], "order-001")
        self.assertEqual(log.snapshot["actual_amount"], "100.00")
        self.assertTrue(log.snapshot["invoice_file"].startswith("invoices/"))

    def test_update_logs_field_diff_only(self):
        item = self._create_item(self.student)
        before = AuditLog.objects.count()
        # ItemForm 的文件字段必填，编辑必须重传；重传同名文件会被存储层加随机
        # 后缀改名，留痕按规格记录文件名变化。
        data = _item_data(
            category=self.category.pk,
            actual_amount="200.00",
            invoice_file=_png_upload("invoice2.png"),
            payment_screenshot=_png_upload("payment2.png"),
        )
        self.client.post(f"/items/{item.pk}/edit/", data)
        item.refresh_from_db()
        self.assertEqual(AuditLog.objects.count(), before + 1)
        log = AuditLog.objects.filter(action="update").latest("id")
        create_snapshot = AuditLog.objects.get(action="create", item_pk=item.pk).snapshot
        self.assertEqual(log.snapshot, {
            "actual_amount": ["100.00", "200.00"],
            "invoice_file": [create_snapshot["invoice_file"], item.invoice_file.name],
            "payment_screenshot": [create_snapshot["payment_screenshot"], item.payment_screenshot.name],
        })
        self.assertEqual(log.actor, self.student)

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
            actual_amount="50.00", order_no="order-002",
            invoice_file="invoices/a.png", payment_screenshot="payments/a.png",
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
            actual_amount="10.00", order_no="order-003",
            invoice_file="invoices/b.png", payment_screenshot="payments/b.png",
        )
        log = AuditLog.objects.get(action="create", item_pk=item.pk)
        self.assertIsNone(log.actor)
        self.assertEqual(log.actor_name, "")

    def test_update_page_shows_audit_section(self):
        item = self._create_item(self.student)
        response = self.client.get(f"/items/{item.pk}/edit/")
        self.assertContains(response, "操作记录")
        self.assertContains(response, "明细：硬盘")
        self.assertContains(response, "创建")
