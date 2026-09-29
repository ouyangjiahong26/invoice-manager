"""批量配对：把混合上传的票据记录按确定性规则配成报销组。

纯函数模块：不调 LLM、不碰 Django ORM（Decimal 除外），可独立单测。
输入 records：[{id, kind, amount, order_no, merchant_no, invoice_no, remark_order_no}]，
kind ∈ invoice/payment/refund/unknown，amount 为 Decimal 或 None；多余键（如 items_summary）忽略。
输出：{"groups": [[id, ...]], "pending": [[id, ...]], "unmatched": [{"id": id, "reason": 文案}]}。

规则按序执行：
1. 单号互证：发票备注单号 = 支付平台单号/商户单号 → 同组；同单号多张发票、多笔支付并入，
   一张发票命中多组支付时并为一组。同发票号码的多张发票视为重复拍摄并为一组。
2. 退款挂组：退款单号命中任一支付的同名字段 → 挂入该支付所在组；挂不上单号时，金额与某唯一组
   的支付合计相等（容差 AMOUNT_TOLERANCE）→ 挂入；仍挂不上进 unmatched。
3. 金额兜底：仅剩单边发票组与单边支付组时，总额相等且两侧各唯一候选 → 成组；任一侧多候选 →
   进 pending（待确认组，由用户在预览页手动并组）。
4. 剩余进 unmatched：发票找不到支付、支付找不到发票、退款找不到支付、unknown 未能识别类型。
"""

from decimal import Decimal

# 与 validation.AMOUNT_TOLERANCE 同值；独立定义以保持本模块零 Django 依赖
AMOUNT_TOLERANCE = Decimal("0.005")

KIND_INVOICE, KIND_PAYMENT, KIND_REFUND = "invoice", "payment", "refund"

REASON_NO_PAYMENT = "找不到对应支付记录"
REASON_NO_INVOICE = "找不到对应发票"
REASON_REFUND_NO_PAYMENT = "退款记录找不到对应支付记录"
REASON_UNKNOWN_KIND = "未能识别票据类型"


def pair(records):
    """按规则 1-4 配对；返回 {"groups", "pending", "unmatched"}。"""
    records = [dict(record) for record in records]
    union = _Union([record["id"] for record in records])
    invoices = [r for r in records if r.get("kind") == KIND_INVOICE]
    payments = [r for r in records if r.get("kind") == KIND_PAYMENT]
    refunds = [r for r in records if r.get("kind") == KIND_REFUND]

    # 规则 1 单号互证：发票备注单号 ↔ 支付平台单号/商户单号
    payments_by_no = {}
    for payment in payments:
        for no in (_norm(payment.get("order_no")), _norm(payment.get("merchant_no"))):
            if no:
                payments_by_no.setdefault(no, []).append(payment["id"])
    for invoice in invoices:
        remark = _norm(invoice.get("remark_order_no"))
        if remark:
            for pid in payments_by_no.get(remark, []):
                union.union(invoice["id"], pid)

    # 规则 1b 同发票号码并组：同号视为同一张发票的重复拍摄
    invoices_by_no = {}
    for invoice in invoices:
        no = _norm(invoice.get("invoice_no"))
        if no:
            invoices_by_no.setdefault(no, []).append(invoice["id"])
    for ids in invoices_by_no.values():
        for rid in ids[1:]:
            union.union(ids[0], rid)

    # 规则 2a 退款单号挂组：退款单号命中任一支付的同名字段
    for refund in refunds:
        for no in (_norm(refund.get("order_no")), _norm(refund.get("merchant_no"))):
            for pid in payments_by_no.get(no, []):
                union.union(refund["id"], pid)

    # 规则 2b 退款金额兜底：金额与唯一组的支付合计相等
    components = _components(records, union)
    payment_sums = {root: _kind_total(members, KIND_PAYMENT) for root, members in components.items()}
    for refund in refunds:
        amount = refund.get("amount")
        members = components[union.find(refund["id"])]
        if amount is None or _has_kind(members, KIND_PAYMENT):
            continue
        candidates = [
            root for root, total in payment_sums.items()
            if total is not None and abs(total - amount) <= AMOUNT_TOLERANCE
        ]
        if len(candidates) == 1:
            union.union(candidates[0], refund["id"])
    components = _components(records, union)

    # 规则 3 金额兜底：单边发票组 ↔ 单边支付组，总额相等连边后按连通块判定
    invoice_only = {root: members for root, members in components.items()
                    if _has_kind(members, KIND_INVOICE) and not _has_kind(members, KIND_PAYMENT)}
    payment_only = {root: members for root, members in components.items()
                    if _has_kind(members, KIND_PAYMENT) and not _has_kind(members, KIND_INVOICE)}
    link = _Union(list(components))
    for iroot, imembers in invoice_only.items():
        itotal = _kind_total(imembers, KIND_INVOICE)
        if itotal is None:
            continue
        for proot, pmembers in payment_only.items():
            ptotal = _kind_total(pmembers, KIND_PAYMENT)
            if ptotal is not None and abs(itotal - ptotal) <= AMOUNT_TOLERANCE:
                link.union(iroot, proot)

    # 已有发票+支付的组直接成组；金额连通块里两侧各唯一 → 成组，否则待确认
    groups = [
        _ids(members) for members in components.values()
        if _has_kind(members, KIND_INVOICE) and _has_kind(members, KIND_PAYMENT)
    ]
    pending = []
    clusters = {}
    for root in components:
        clusters.setdefault(link.find(root), []).append(root)
    for roots in clusters.values():
        iroots = [root for root in roots if root in invoice_only]
        proots = [root for root in roots if root in payment_only]
        if not iroots or not proots:
            continue
        members = [member for root in roots for member in components[root]]
        if len(iroots) == 1 and len(proots) == 1:
            groups.append(_ids(members))
        else:
            pending.append(_ids(members))

    position = {record["id"]: index for index, record in enumerate(records)}
    groups.sort(key=lambda ids: min(position[i] for i in ids))
    pending.sort(key=lambda ids: min(position[i] for i in ids))
    paired = {rid for ids in groups + pending for rid in ids}

    unmatched = []
    for record in records:
        rid = record["id"]
        if rid in paired:
            continue
        kind = record.get("kind")
        if kind == KIND_INVOICE:
            reason = REASON_NO_PAYMENT
        elif kind == KIND_PAYMENT:
            reason = REASON_NO_INVOICE
        elif kind == KIND_REFUND:
            members = components[union.find(rid)]
            reason = REASON_NO_INVOICE if _has_kind(members, KIND_PAYMENT) else REASON_REFUND_NO_PAYMENT
        else:
            reason = REASON_UNKNOWN_KIND
        unmatched.append({"id": rid, "reason": reason})
    return {"groups": groups, "pending": pending, "unmatched": unmatched}


def _components(records, union):
    """按并查集根聚合记录，保持输入顺序。"""
    result = {}
    for record in records:
        result.setdefault(union.find(record["id"]), []).append(record)
    return result


def _has_kind(members, kind):
    return any(member.get("kind") == kind for member in members)


def _kind_total(members, kind):
    """某类成员金额合计；无该类成员或有成员缺金额时返回 None（总额不可信，不参与互证）。"""
    amounts = [member.get("amount") for member in members if member.get("kind") == kind]
    if not amounts or any(amount is None for amount in amounts):
        return None
    return sum(amounts, Decimal("0"))


def _ids(members):
    return [member["id"] for member in members]


def _norm(no):
    return str(no or "").strip()


class _Union:
    """并查集：key 需可哈希且互异。"""

    def __init__(self, keys):
        self.parent = {key: key for key in keys}

    def find(self, key):
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]  # 路径减半
            key = self.parent[key]
        return key

    def union(self, a, b):
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a
