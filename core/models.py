from django.conf import settings
from django.db import models


class Category(models.Model):
    name = models.CharField("类别名称", max_length=50, unique=True)
    description = models.CharField("类别说明", max_length=200, blank=True, default="")
    order = models.PositiveSmallIntegerField("排序", default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "类别"
        verbose_name_plural = "类别"

    def __str__(self):
        return self.name


class Batch(models.Model):
    name = models.CharField("批次名称", max_length=100, unique=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    archived = models.BooleanField("已存档", default=False, help_text="存档批次只读：条目与附件不可增改删")


    class Meta:
        ordering = ["-created_at"]
        verbose_name = "报销批次"
        verbose_name_plural = "报销批次"

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
    status = models.CharField(
        "状态", max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    batch = models.ForeignKey(
        Batch,
        on_delete=models.PROTECT,
        related_name="items",
        verbose_name="批次",
    )
    position = models.PositiveIntegerField("顺序", default=0)

    created_at = models.DateTimeField("提交时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["position", "id"]
        verbose_name = "报销条目"
        verbose_name_plural = "报销条目"

    def __str__(self):
        return f"{self.payer_name} {self.title} {self.actual_amount}"

    @property
    def payer_name(self):
        return self.owner.get_full_name() or self.owner.username

    @classmethod
    def next_position(cls):
        return (cls.objects.aggregate(m=models.Max("position"))["m"] or 0) + 1

    def attachment_of_kind(self, kind):
        """该条目下指定类型的第一张附件（配合 prefetch_related 零额外查询）。"""
        for attachment in self.attachments.all():
            if attachment.kind == kind:
                return attachment
        return None

    @property
    def first_invoice(self):
        return self.attachment_of_kind(Attachment.KIND_INVOICE)

    @property
    def first_payment(self):
        return self.attachment_of_kind(Attachment.KIND_PAYMENT)


class Attachment(models.Model):
    """条目下的单个票据文件：发票 / 支付记录 / 退款记录，一条目可有多张。"""

    KIND_INVOICE, KIND_PAYMENT, KIND_REFUND = "invoice", "payment", "refund"
    KIND_CHOICES = [
        (KIND_INVOICE, "发票"),
        (KIND_PAYMENT, "支付记录"),
        (KIND_REFUND, "退款记录"),
    ]

    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="attachments", verbose_name="所属条目"
    )
    kind = models.CharField("类型", max_length=10, choices=KIND_CHOICES)
    file = models.FileField("文件", upload_to="attachments/%Y%m/")
    amount = models.DecimalField(
        "金额", max_digits=10, decimal_places=2, null=True, blank=True
    )
    order_no = models.CharField("平台单号", max_length=64, blank=True)
    merchant_no = models.CharField("商户单号", max_length=64, blank=True)
    invoice_no = models.CharField("发票号码", max_length=50, blank=True)
    ocr_data = models.JSONField("识别结果", null=True, blank=True)
    created_at = models.DateTimeField("上传时间", auto_now_add=True)

    class Meta:
        ordering = ["kind", "id"]
        verbose_name = "附件"
        verbose_name_plural = "附件"

    def __str__(self):
        return f"{self.get_kind_display()} {self.file.name}"

    @property
    def is_pdf(self):
        return self.file.name.lower().endswith(".pdf")

    @property
    def display_name(self):
        """文件名（去掉存储路径前缀），供页面显示。"""
        return self.file.name.rsplit("/", 1)[-1]


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
        from .audit import FIELD_LABELS, format_attachments

        rows = []
        for field, change in self.snapshot.items():
            label = FIELD_LABELS.get(field, field)
            if field == "attachments":
                text = format_attachments(change)
            elif isinstance(change, list):
                text = " → ".join(change)
            else:
                text = change
            rows.append((label, text))
        return rows
