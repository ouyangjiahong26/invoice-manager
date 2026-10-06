"""条目操作留痕：快照常量、操作人捕获中间件、Item 信号处理器。

留痕语义单点：任何 Item 保存/删除路径（视图、admin 表单、admin list_editable、
shell）经信号统一落 AuditLog。操作人经 contextvar 从请求中间件传入。

附件不在 Item 表内，项目值先于附件保存，因此视图在改动附件前后调用
`mark_attachments(item, after, before)` 注入快照，diff 仍由信号统一计算。
"""
import contextvars

from django.db.models.signals import post_delete, post_save, pre_save

from .models import Attachment, AuditLog, Item

SNAPSHOT_FIELDS = [
    "title", "category", "actual_amount", "invoice_amount", "attachments", "status", "owner",
]
FIELD_LABELS = {
    "title": "明细", "category": "类别", "actual_amount": "实付款",
    "invoice_amount": "发票金额", "attachments": "附件", "status": "状态", "owner": "付款人",
}
ATTACHMENT_LABELS = {
    "kind": "类型", "amount": "金额", "order_no": "平台单号",
    "merchant_no": "商户单号", "invoice_no": "发票号码", "file": "文件",
}
ATTACHMENT_FIELDS = tuple(ATTACHMENT_LABELS)

_actor_var = contextvars.ContextVar("audit_actor", default=None)


def current_actor():
    """当前请求的已登录用户。非请求路径（shell/级联）返回 None。"""
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


def attachment_entry(attachment):
    """单个附件的快照条目。允许未保存实例（file 取上传文件名）。"""
    return {
        "kind": attachment.kind,
        "kind_label": attachment.get_kind_display(),
        "amount": "" if attachment.amount is None else str(attachment.amount),
        "order_no": attachment.order_no or "",
        "merchant_no": attachment.merchant_no or "",
        "invoice_no": attachment.invoice_no or "",
        "file": attachment.file.name or "",
    }


def attachment_entries(item):
    """条目当前附件的快照列表（查库）。"""
    return [attachment_entry(a) for a in item.attachments.all()]


def mark_attachments(item, after, before):
    """视图在附件改动前后注入快照，供下一次 Item.save() 的信号计算 diff。"""
    item._audit_attachments = after
    item._audit_attachments_before = before


def format_attachments(value):
    """把附件快照渲染成一行文本。value 为列表（终值）或字符串（由旧值改为新值的 diff）。"""
    if isinstance(value, str):
        return value
    if not value:
        return "无"
    return "；".join(_format_entry(entry) for entry in value)


def _format_entry(entry):
    amount = f" {entry['amount']} 元" if entry["amount"] else ""
    return f"{entry['kind_label']} {entry['file'].rsplit('/', 1)[-1]}{amount}"


def _attachment_diff(old_entries, new_entries):
    """“新增/删除/修改”逐条列出。无变化返回空串。"""
    old_by_key = {(e["kind"], e["file"]): e for e in old_entries}
    new_by_key = {(e["kind"], e["file"]): e for e in new_entries}
    parts = [
        f"新增 {_format_entry(entry)}"
        for key, entry in new_by_key.items() if key not in old_by_key
    ]
    parts += [
        f"删除 {_format_entry(entry)}"
        for key, entry in old_by_key.items() if key not in new_by_key
    ]
    for key, entry in new_by_key.items():
        old_entry = old_by_key.get(key)
        if old_entry is None:
            continue
        changed = [
            f"{ATTACHMENT_LABELS[field]} 由 {old_entry[field] or '空'} 改为 {entry[field] or '空'}"
            for field in ATTACHMENT_FIELDS
            if old_entry[field] != entry[field]
        ]
        if changed:
            parts.append(f"修改 {_format_entry(entry)}：" + "、".join(changed))
    return "；".join(parts)


def _snapshot(item):
    """逐跟踪字段取值。attachments 取自注入值或库中附件。"""
    data = {}
    for field in SNAPSHOT_FIELDS:
        if field == "attachments":
            injected = getattr(item, "_audit_attachments", None)
            data[field] = attachment_entries(item) if injected is None else injected
            continue
        value = getattr(item, field)
        if field == "category":
            value = value.name
        if field == "owner":
            value = value.get_full_name() or value.username
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
        old = Item.objects.select_related("category", "owner").filter(pk=instance.pk).first()
    if old is None:
        instance._audit_old = None
        return
    snapshot = _snapshot(old)
    before = getattr(instance, "_audit_attachments_before", None)
    if before is not None:
        snapshot["attachments"] = before
    instance._audit_old = snapshot


def _post_save_item(sender, instance, created, **kwargs):
    current = _snapshot(instance)
    if created:
        _log(instance.pk, AuditLog.ACTION_CREATE, current, item=instance)
        return
    old = getattr(instance, "_audit_old", None)
    if old is None:
        return
    diff = {}
    for field in SNAPSHOT_FIELDS:
        if field == "attachments":
            text = _attachment_diff(old[field], current[field])
            if text:
                diff[field] = text
        elif old[field] != current[field]:
            diff[field] = [old[field], current[field]]
    if diff:
        _log(instance.pk, AuditLog.ACTION_UPDATE, diff, item=instance)


def _post_delete_item(sender, instance, **kwargs):
    # item 行已在库中删除，item_id 必须为 NULL，留痕仅按 item_pk 检索。
    _log(instance.pk, AuditLog.ACTION_DELETE, _snapshot(instance))
