"""附件构造与解析的共享工具：视图与 AI 导入命令共用。

从 views.py 平移而来，函数体保持不变，仅去下划线改公开名。
"""

import json
from decimal import Decimal, InvalidOperation

from .models import Attachment

KIND_LABELS = dict(Attachment.KIND_CHOICES)


def attachment_groups(item):
    """编辑侧边栏与看板条目详情共用：按 kind 汇总已保存附件。"""
    groups = {kind: [] for kind in KIND_LABELS}
    for attachment in item.attachments.all():
        groups[attachment.kind].append(attachment)
    return groups


def build_attachment(kind, upload, fields):
    """按字段 dict 构造未保存的 Attachment；金额非法抛 ValueError。

    fields 取值兼容字符串（单条表单 POST）与 JSON 值（批量 plan）。
    """
    attachment = Attachment(kind=kind, file=upload)

    def text(name, limit):
        raw = fields.get(name)
        return ("" if raw is None else str(raw).strip())[:limit]

    raw_amount = fields.get("amount")
    if raw_amount not in (None, ""):
        try:
            attachment.amount = Decimal(str(raw_amount).strip())
        except InvalidOperation:
            raise ValueError("金额格式不正确")
        if attachment.amount < 0:
            raise ValueError("金额不能为负")
    attachment.order_no = text("order_no", 64)
    attachment.merchant_no = text("merchant_no", 64)
    attachment.invoice_no = text("invoice_no", 50)
    attachment.ocr_data = ocr_value(fields.get("ocr"))
    return attachment


def ocr_value(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def decimal_or_none(raw):
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw).strip())
    except InvalidOperation:
        return None


def plan_decimal(raw, label):
    """解析分组计划金额并按 DecimalField(max_digits=10, decimal_places=2) 限位；
    None/空串返回 None（可选金额用），非法抛 ValueError，文案直接拼进 400 响应。"""
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        raise ValueError(f"{label}格式不正确")
    if (not value.is_finite() or abs(value) >= Decimal("100000000")
            or value.quantize(Decimal("0.01")) != value):
        raise ValueError(f"{label}最多两位小数、绝对值需小于一亿")
    return value
