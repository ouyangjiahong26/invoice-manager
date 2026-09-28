"""灌入初始三个报销类别（对应人工报销表的类别列）。"""

from django.db import migrations

CATEGORIES = [
    ("信息服务费", 1),
    ("竞赛报名费", 2),
    ("书籍", 3),
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
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
