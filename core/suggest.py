"""LLM 配对与字段建议的共享工具：批量配对视图与 AI 导入命令共用。

从 views.py 平移而来，函数体保持不变，仅去下划线改公开名。
"""

from . import vision
from .attachments import KIND_LABELS
from .models import Attachment, Category


def category_payloads():
    """模板下拉与 LLM 建议共用的类别摘要。"""
    return [
        {"id": category.pk, "name": category.name, "description": category.description}
        for category in Category.objects.all()
    ]


def llm_merge_groups(by_id, groups, pending, unmatched):
    """LLM 兜底配对：把未配对与待确认记录交给模型归组，配成的移入 groups，仍配不上的留 unmatched。"""
    summaries = []
    candidate_ids = set()
    for entry in unmatched:
        record = by_id.get(entry["id"])
        if record and record.get("kind") in KIND_LABELS:
            summaries.append(pair_summary(record))
            candidate_ids.add(entry["id"])
    for group in pending:
        for rid in group:
            record = by_id.get(rid)
            if record:
                summaries.append(pair_summary(record))
                candidate_ids.add(rid)
    kinds = {by_id[rid].get("kind") for rid in candidate_ids}
    if Attachment.KIND_INVOICE not in kinds or Attachment.KIND_PAYMENT not in kinds:
        return groups, pending, unmatched

    consumed = set()
    llm_groups = []
    for group in vision.group_suggest(summaries):
        if any(rid in consumed for rid in group):
            continue
        llm_groups.append(group)
        consumed.update(group)
    if not llm_groups:
        return groups, pending, unmatched
    rest_pending = []
    for group in pending:
        rest = [rid for rid in group if rid not in consumed]
        if rest:
            rest_pending.append(rest)
    rest_unmatched = [entry for entry in unmatched if entry["id"] not in consumed]
    return groups + llm_groups, rest_pending, rest_unmatched


def pair_summary(record):
    """LLM 配对用的记录摘要。"""
    amount = record.get("amount")
    return {
        "id": record["id"],
        "kind": record.get("kind"),
        "amount": str(amount) if amount is not None else None,
        "order_no": record.get("order_no") or "",
        "merchant_no": record.get("merchant_no") or "",
        "remark_order_no": record.get("remark_order_no") or "",
    }


def suggest_member(record):
    """LLM 字段建议用的组成员摘要。"""
    amount = record.get("amount")
    return {
        "id": record["id"],
        "kind": record.get("kind"),
        "amount": str(amount) if amount is not None else None,
        "items_summary": record.get("items_summary") or "",
    }
