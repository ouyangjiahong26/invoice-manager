"""批量配对：把混合上传的票据记录按确定性规则配成报销组。

纯函数模块：不调 LLM、不碰 Django ORM（Decimal 除外），可独立单测。
输入 records：[{id, kind, amount, order_no, merchant_no, invoice_no, remark_order_no}]，
kind ∈ invoice/payment/refund/unknown，amount 为 Decimal/int/float/数字串（入口统一转
Decimal，解析失败按 None）。多余键（如 items_summary）忽略。

规则按序执行：
1. 单号互证：发票备注单号 = 支付平台单号/商户单号时归同组。同单号多张发票、多笔支付并入，
   一张发票命中多组支付时并为一组。同发票号码的多张发票视为重复拍摄并为一组。
2. 退款挂组：退款单号命中任一支付的同名字段时挂入该支付所在组。挂不上单号时，金额与某唯一组
   的支付合计相等（容差 AMOUNT_TOLERANCE）也挂入。仍挂不上进 unmatched。
3. 金额兜底：单边发票组与单边支付组总额配平，含一笔支付对应多张发票的合付组合（发票侧组合
   总额 = 支付侧总额）。组合唯一（同一发票/支付不被多个匹配占用）时直接成组，否则连通后整体
   进 pending（待确认组，由用户在预览页手动并组）。
4. 剩余进 unmatched：发票找不到支付、支付找不到发票、退款找不到支付、unknown 未能识别类型。
"""

from collections import Counter
from decimal import Decimal, InvalidOperation

# 与 validation.AMOUNT_TOLERANCE 同值。独立定义以保持本模块零 Django 依赖
AMOUNT_TOLERANCE = Decimal("0.005")

# 规则 3 发票侧组合枚举的组件数上限，超过只做单张对单笔比对，避免子集枚举爆炸
MAX_SUBSET_ROOTS = 15

KIND_INVOICE, KIND_PAYMENT, KIND_REFUND = "invoice", "payment", "refund"

REASON_NO_PAYMENT = "找不到对应支付记录"
REASON_NO_INVOICE = "找不到对应发票"
REASON_REFUND_NO_PAYMENT = "退款记录找不到对应支付记录"
REASON_SUPPORT = "证明材料不参与自动配对，请加入对应组"
REASON_UNKNOWN_KIND = "未能识别票据类型"


def pair(records):
    """按规则 1-4 配对。返回 {"groups", "pending", "unmatched"}。"""
    records = [dict(record) for record in records]
    for record in records:
        amount = record.get("amount")
        if amount is not None and not isinstance(amount, Decimal):
            record["amount"] = _to_decimal(amount)
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

    # 规则 3 金额兜底：单边发票组与单边支付组总额配平（含一笔支付对多张发票的合付）。
    # 唯一匹配（发票组合、支付各不被其他匹配占用）直接并成一组。多候选连成连通块进 pending。
    invoice_only = {root: members for root, members in components.items()
                    if _has_kind(members, KIND_INVOICE) and not _has_kind(members, KIND_PAYMENT)}
    payment_only = {root: members for root, members in components.items()
                    if _has_kind(members, KIND_PAYMENT) and not _has_kind(members, KIND_INVOICE)}
    matches = _amount_matches(invoice_only, payment_only)
    iroot_use = Counter(iroot for iroots, _ in matches for iroot in iroots)
    proot_use = Counter(proot for _, proot in matches)
    link = _Union(list(components))
    for iroots, proot in matches:
        if proot_use[proot] > 1 or any(iroot_use[iroot] > 1 for iroot in iroots):
            for iroot in iroots:  # 多候选：连成连通块，交由下方整体判 pending
                link.union(iroot, proot)
        else:
            for iroot in iroots:  # 唯一匹配：直接并组（含多张发票合付一笔支付）
                union.union(iroot, proot)
    components = _components(records, union)

    # 含发票+支付的连通块即成组（规则 1/2 组 + 规则 3 唯一匹配）。多候选连通块整体待确认
    groups = [
        _ids(members) for members in components.values()
        if _has_kind(members, KIND_INVOICE) and _has_kind(members, KIND_PAYMENT)
    ]
    pending = []
    clusters = {}
    for root in list(invoice_only) + list(payment_only):
        clusters.setdefault(link.find(root), []).append(root)
    for roots in clusters.values():
        iroots = [root for root in roots if root in invoice_only]
        proots = [root for root in roots if root in payment_only]
        if not iroots or not proots:
            continue
        members = [member for root in roots
                   for member in (invoice_only.get(root) or payment_only[root])]
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
        elif kind == "support":
            reason = REASON_SUPPORT
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
    """某类成员金额合计。无该类成员或有成员缺金额时返回 None（总额不可信，不参与互证）。"""
    amounts = [member.get("amount") for member in members if member.get("kind") == kind]
    if not amounts or any(amount is None for amount in amounts):
        return None
    return sum(amounts, Decimal("0"))


def _to_decimal(raw):
    """金额统一转 Decimal。解析失败返回 None（原值不可信，等同缺金额）。"""
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _amount_matches(invoice_only, payment_only):
    """规则 3 的金额匹配全集：[(发票组件集合, 支付组件)]，总额相等（容差内）即一条。

    发票侧组件数超过 MAX_SUBSET_ROOTS 时退化为单张发票对单笔支付，不枚举组合。
    """
    itotals = {root: _kind_total(members, KIND_INVOICE) for root, members in invoice_only.items()}
    roots = [root for root, total in itotals.items() if total is not None]
    multi = len(roots) <= MAX_SUBSET_ROOTS
    matches = []
    for proot, pmembers in payment_only.items():
        ptotal = _kind_total(pmembers, KIND_PAYMENT)
        if ptotal is None:
            continue
        matches.extend((subset, proot) for subset in _subsets_totaling(roots, itotals, ptotal, multi))
    return matches


def _subsets_totaling(roots, totals, target, multi):
    """总额等于 target（容差内）的组件子集。multi=False 时只比单张。"""
    result = [
        frozenset({root}) for root in roots
        if abs(totals[root] - target) <= AMOUNT_TOLERANCE
    ]
    if not multi:
        return result

    def walk(index, chosen, total):
        if len(chosen) > 1 and abs(total - target) <= AMOUNT_TOLERANCE:
            result.append(frozenset(chosen))
        for i in range(index, len(roots)):
            walk(i + 1, chosen + [roots[i]], total + totals[roots[i]])

    walk(0, [], Decimal("0"))
    return result


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
