"""条目操作留痕：快照常量、操作人捕获中间件、Item 信号处理器。

留痕语义单点：任何 Item 保存/删除路径（视图、admin 表单、admin list_editable、
shell）经信号统一落 AuditLog；操作人经 contextvar 从请求中间件传入。
"""
import contextvars

from django.db.models.fields.files import FieldFile
from django.db.models.signals import post_delete, post_save, pre_save

from .models import AuditLog, Item

SNAPSHOT_FIELDS = [
    "title", "category", "actual_amount", "invoice_amount",
    "invoice_no", "order_no", "invoice_file", "payment_screenshot", "status",
]
FIELD_LABELS = {
    "title": "明细", "category": "类别", "actual_amount": "实付款",
    "invoice_amount": "发票金额", "invoice_no": "发票号码",
    "order_no": "支付订单号", "invoice_file": "票据",
    "payment_screenshot": "支付截图", "status": "状态",
}

_actor_var = contextvars.ContextVar("audit_actor", default=None)


def current_actor():
    """当前请求的已登录用户；非请求路径（shell/级联）返回 None。"""
    return _actor_var.get()


class ActorMiddleware:
    """同步标准中间件：把 request.user 暂存到 contextvar，供信号读取。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        token = _actor_var.set(user if user is not None and user.is_authenticated else None)
        try:
            return self.get_response(request)
        finally:
            _actor_var.reset(token)


def _snapshot(item):
    """逐跟踪字段取值，全部转字符串；返回 {字段名: "值"}。"""
    data = {}
    for field in SNAPSHOT_FIELDS:
        value = getattr(item, field)
        if field == "category":
            value = value.name
        elif isinstance(value, FieldFile):
            value = value.name or ""
        data[field] = "" if value is None else str(value)
    return data


def _log(item_pk, action, snapshot, item=None):
    actor = current_actor()
    AuditLog.objects.create(
        item=item,
        item_pk=item_pk,
        action=action,
        actor=actor,
        actor_name=(actor.get_full_name() or actor.username) if actor else "",
        snapshot=snapshot,
    )


def _pre_save_item(sender, instance, **kwargs):
    old = None
    if instance.pk is not None:
        old = Item.objects.select_related("category").filter(pk=instance.pk).first()
    instance._audit_old = _snapshot(old) if old is not None else None


def _post_save_item(sender, instance, created, **kwargs):
    current = _snapshot(instance)
    if created:
        _log(instance.pk, AuditLog.ACTION_CREATE, current, item=instance)
        return
    old = getattr(instance, "_audit_old", None)
    if old is None:
        return
    diff = {
        field: [old[field], current[field]]
        for field in SNAPSHOT_FIELDS
        if old[field] != current[field]
    }
    if diff:
        _log(instance.pk, AuditLog.ACTION_UPDATE, diff, item=instance)


def _post_delete_item(sender, instance, **kwargs):
    # item 行已在库中删除，item_id 必须为 NULL，留痕仅按 item_pk 检索。
    _log(instance.pk, AuditLog.ACTION_DELETE, _snapshot(instance))
