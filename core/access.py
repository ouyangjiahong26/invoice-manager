"""组与数据可见范围：管理员看全部组，其他用户只看自己所属组。"""

from django.contrib.auth.models import Group, User

from .models import Batch

MAX_BATCHES_PER_USER = 100


def batch_quota_reached(user):
    """用户创建的批次是否已达上限。

    先锁定用户行再计数，调用方必须处于事务中。同一用户的并发建批请求因此
    串行执行，计数不会都停在创建之前。
    """
    User.objects.select_for_update().get(pk=user.pk)
    return Batch.objects.filter(created_by=user).count() >= MAX_BATCHES_PER_USER


def accessible_groups(user):
    """当前用户可访问的组。管理员不受组限制，其余用户看自己的组。"""
    if user.is_staff:
        return Group.objects.order_by("id")
    return user.groups.order_by("id")


def can_access_group(user, group):
    return user.is_staff or user.groups.filter(pk=group.pk).exists()


def group_is_self_service(group):
    """组是否开启成员自助：成员可建批，并审批自己创建的批次。"""
    settings_obj = getattr(group, "settings", None)
    return bool(settings_obj and settings_obj.self_service)


def can_create_batch(user, group):
    if user.is_staff:
        return True
    return can_access_group(user, group) and group_is_self_service(group)


def can_review_batch(user, batch):
    """批次可审批：管理员，或自助组里仍在该组的批次创建人。"""
    if user.is_staff:
        return True
    return (
        batch.created_by_id == user.id
        and group_is_self_service(batch.group)
        and can_access_group(user, batch.group)
    )


def can_review_item(user, item):
    return can_review_batch(user, item.batch)


def can_view_item(user, item):
    """条目可见：管理员、条目付款人本人、与条目所属组同组的用户。"""
    if user.is_staff or item.owner_id == user.id:
        return True
    return user.groups.filter(pk=item.batch.group_id).exists()


def can_edit_item(user, item, group_ids=None):
    """条目可改：管理员，或仍在该组的付款人本人。调出组后只保留查看权。

    group_ids 传入当前用户可访问的组 id 集合时跳过逐条查询，供看板批量判断。
    """
    if user.is_staff:
        return True
    if group_ids is None:
        in_group = can_access_group(user, item.batch.group)
    else:
        in_group = item.batch.group_id in group_ids
    return item.owner_id == user.id and in_group
