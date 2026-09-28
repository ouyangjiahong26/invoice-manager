"""报销批次与条目持久化顺序：新建 Batch；Item 挂批次（PROTECT）并加全局递增 position。

迁移顺序关键：batch 先以 null=True 加入，把存量条目（生产仅 1 条测试数据）挂到
自建的"历史条目"批次并给 position=1，最后收紧为非空。
"""

import django.db.models.deletion
from django.db import migrations, models


def assign_default_batch(apps, schema_editor):
    Batch = apps.get_model("core", "Batch")
    Item = apps.get_model("core", "Item")
    batch = Batch.objects.create(name="历史条目")
    Item.objects.update(batch=batch, position=1)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_item_attachments"),
    ]

    operations = [
        migrations.CreateModel(
            name="Batch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True, verbose_name="批次名称")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
            ],
            options={
                "ordering": ["-created_at"],
                "verbose_name": "报销批次",
                "verbose_name_plural": "报销批次",
            },
        ),
        migrations.AddField(
            model_name="item",
            name="batch",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="items", to="core.batch", verbose_name="批次"),
        ),
        migrations.AddField(
            model_name="item",
            name="position",
            field=models.PositiveIntegerField(default=0, verbose_name="顺序"),
        ),
        migrations.RunPython(assign_default_batch, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="item",
            name="batch",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="items", to="core.batch", verbose_name="批次"),
        ),
        migrations.AlterModelOptions(
            name="item",
            options={"ordering": ["position", "id"], "verbose_name": "报销条目", "verbose_name_plural": "报销条目"},
        ),
    ]
