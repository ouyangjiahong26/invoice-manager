"""新增报销类别：办公用品。"""

from django.db import migrations

CATEGORIES = [
    ("办公用品", 4),
]


def seed(apps, schema_editor):
    Category = apps.get_model("core", "Category")
    for name, order in CATEGORIES:
        Category.objects.update_or_create(name=name, defaults={"order": order})


def unseed(apps, schema_editor):
    Category = apps.get_model("core", "Category")
    Category.objects.filter(name__in=[name for name, _ in CATEGORIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_alter_attachment_kind"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
