import django.db.models.deletion
from django.db import migrations, models


def seed_default_group(apps, schema_editor):
    """把历史数据归入默认组。全新部署（无账号也无无组批次）不预置组，由管理员自建。"""
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")
    Batch = apps.get_model("core", "Batch")

    has_orphans = Batch.objects.filter(group__isnull=True).exists()
    if not has_orphans and not User.objects.exists():
        return

    group, _ = Group.objects.get_or_create(name="默认组")
    Batch.objects.filter(group__isnull=True).update(group=group)

    UserGroup = User.groups.through
    existing = set(
        UserGroup.objects.filter(group=group).values_list("user_id", flat=True)
    )
    UserGroup.objects.bulk_create(
        [
            UserGroup(user_id=user_id, group_id=group.pk)
            for user_id in User.objects.values_list("pk", flat=True)
            if user_id not in existing
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("core", "0010_alter_batch_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="batch",
            name="group",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="batches",
                to="auth.group",
                verbose_name="组",
            ),
        ),
        migrations.RunPython(seed_default_group, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="batch",
            name="group",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="batches",
                to="auth.group",
                verbose_name="组",
            ),
        ),
    ]
