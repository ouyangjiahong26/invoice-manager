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
