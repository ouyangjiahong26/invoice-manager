from django.conf import settings
from django.db import models


class Category(models.Model):
    name = models.CharField("类别名称", max_length=50, unique=True)
    order = models.PositiveSmallIntegerField("排序", default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "类别"
        verbose_name_plural = "类别"

    def __str__(self):
        return self.name


class Item(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "待审核"),
        (STATUS_APPROVED, "已通过"),
        (STATUS_REJECTED, "已退回"),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="付款人",
    )
    title = models.CharField("明细", max_length=200)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="items",
        verbose_name="类别",
    )
    actual_amount = models.DecimalField("实付款", max_digits=10, decimal_places=2)
    invoice_amount = models.DecimalField(
        "发票金额", max_digits=10, decimal_places=2, null=True, blank=True
    )
    invoice_no = models.CharField("发票号码", max_length=50, blank=True)
    order_no = models.CharField("支付订单号", max_length=64)
    invoice_file = models.FileField("票据", upload_to="invoices/%Y%m/")
    payment_screenshot = models.ImageField("支付截图", upload_to="payments/%Y%m/")
    status = models.CharField(
        "状态", max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    created_at = models.DateTimeField("提交时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "报销条目"
        verbose_name_plural = "报销条目"

    def __str__(self):
        return f"{self.payer_name} {self.title} {self.actual_amount}"

    @property
    def payer_name(self):
        return self.owner.get_full_name() or self.owner.username


class AuditLog(models.Model):
    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_CHOICES = [
        (ACTION_CREATE, "创建"),
        (ACTION_UPDATE, "修改"),
        (ACTION_DELETE, "删除"),
    ]
    item = models.ForeignKey(
        Item, null=True, on_delete=models.SET_NULL,
        related_name="audit_logs", verbose_name="条目",
    )
    item_pk = models.PositiveIntegerField("条目编号")
    action = models.CharField("动作", max_length=10, choices=ACTION_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="audit_logs", verbose_name="操作人",
    )
    actor_name = models.CharField("操作人姓名", max_length=100, blank=True)
    snapshot = models.JSONField("变更明细", default=dict)
    created_at = models.DateTimeField("操作时间", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "操作留痕"
        verbose_name_plural = "操作留痕"

    def __str__(self):
        return f"{self.actor_name or '系统'} {self.get_action_display()} #{self.item_pk}"

    def detail_rows(self):
        """返回 [(字段标签, 明细文本)]。update 为 "旧 → 新"，create/delete 为终值。"""
        from .audit import FIELD_LABELS

        rows = []
        for field, change in self.snapshot.items():
            label = FIELD_LABELS.get(field, field)
            if isinstance(change, list):
                text = " → ".join(change)
            else:
                text = change
            rows.append((label, text))
        return rows
