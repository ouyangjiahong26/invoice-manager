"""组与数据可见范围：管理员看全部组，其他用户只看自己所属组。"""

from django.contrib.auth.models import Group


def accessible_groups(user):
    """当前用户可访问的组。管理员不受组限制，其余用户看自己的组。"""
    if user.is_staff:
        return Group.objects.order_by("id")
    return user.groups.order_by("id")


def can_access_group(user, group):
    return user.is_staff or user.groups.filter(pk=group.pk).exists()


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
