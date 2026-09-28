"""条目附件改为多附件模型：新建 Attachment，旧单文件字段数据搬入后删除。

迁移顺序关键：先建表，再把旧字段的文件名/金额/号码落到新表，最后删旧字段。
文件不搬物理路径，只复制 FieldFile.name（upload_to 变了，但 name 仍是原相对
路径，storage 照常打开）。
"""

import django.db.models.deletion
from django.db import migrations, models


def migrate_legacy_files(apps, schema_editor):
    """每条 Item 的旧发票/截图各生成一条 Attachment；空文件名跳过。"""
    Item = apps.get_model("core", "Item")
    Attachment = apps.get_model("core", "Attachment")
    for item in Item.objects.all():
        if item.invoice_file:
            Attachment.objects.create(
                item_id=item.pk,
                kind="invoice",
                file=item.invoice_file.name,
                amount=item.invoice_amount,
                invoice_no=item.invoice_no,
            )
        if item.payment_screenshot:
            Attachment.objects.create(
                item_id=item.pk,
                kind="payment",
                file=item.payment_screenshot.name,
                amount=item.actual_amount,
                order_no=item.order_no,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_auditlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="description",
            field=models.CharField(blank=True, default="", max_length=200, verbose_name="类别说明"),
        ),
        migrations.CreateModel(
            name="Attachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("invoice", "发票"), ("payment", "支付记录"), ("refund", "退款记录")], max_length=10, verbose_name="类型")),
                ("file", models.FileField(upload_to="attachments/%Y%m/", verbose_name="文件")),
                ("amount", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="金额")),
                ("order_no", models.CharField(blank=True, max_length=64, verbose_name="平台单号")),
                ("merchant_no", models.CharField(blank=True, max_length=64, verbose_name="商户单号")),
                ("invoice_no", models.CharField(blank=True, max_length=50, verbose_name="发票号码")),
                ("ocr_data", models.JSONField(blank=True, null=True, verbose_name="识别结果")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="上传时间")),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="core.item", verbose_name="所属条目")),
            ],
            options={
                "verbose_name": "附件",
                "verbose_name_plural": "附件",
                "ordering": ["kind", "id"],
            },
        ),
        migrations.RunPython(migrate_legacy_files, migrations.RunPython.noop),
        migrations.RemoveField(model_name="item", name="invoice_file"),
        migrations.RemoveField(model_name="item", name="invoice_no"),
        migrations.RemoveField(model_name="item", name="order_no"),
        migrations.RemoveField(model_name="item", name="payment_screenshot"),
    ]
