"""报销条目自动校验：返回警告字符串列表，只警告不阻断提交。

后端保存后以 messages 形式展示。金额与单号口径一律以附件为准。
"""

import re
from decimal import Decimal

from PIL import Image, ImageStat

from django.conf import settings

from .models import Attachment

# 截图 RGB 均值低于该阈值视为疑似深色模式
DARK_MEAN_THRESHOLD = 80
# 平台单号：支付宝订单号（20 开头，28 位，银联等银行渠道可到 32 位）/ 微信交易单号
# （4 开头，28 位）/ 京东订单编号（3 开头，12 或 16 位）
ALIPAY_ORDER_NO_RE = re.compile(r"^20\d{26,30}\Z")
WECHAT_ORDER_NO_RE = re.compile(r"^4\d{27}\Z")
JD_ORDER_NO_RE = re.compile(r"^3\d{11}(?:\d{4})?\Z")
PLATFORM_ORDER_NO_RE = re.compile("|".join(
    r.pattern for r in (ALIPAY_ORDER_NO_RE, WECHAT_ORDER_NO_RE, JD_ORDER_NO_RE)
))
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
AMOUNT_TOLERANCE = Decimal("0.005")


def check_item(item):
    """检查条目，返回警告列表。"""
    return check_values(list(item.attachments.all()), item.actual_amount, item.invoice_amount)


def check_values(attachments, actual_amount, invoice_amount):
    """警告规则本体：按附件列表与填写金额返回警告列表。

    attachments 元素需有 kind、amount、order_no、file、ocr_data 属性。未保存草稿
    传 file=None 即可，依赖文件与识别结果的检查会自动跳过。
    """
    warnings = []
    invoices = [a for a in attachments if a.kind == Attachment.KIND_INVOICE]
    payments = [a for a in attachments if a.kind == Attachment.KIND_PAYMENT]
    refunds = [a for a in attachments if a.kind == Attachment.KIND_REFUND]

    invoice_total = _sum_amounts(invoices)
    paid = _sum_amounts(payments)
    refunded = _sum_amounts(refunds)
    net_total = None
    if paid is not None or refunded is not None:
        net_total = (paid or Decimal("0")) - (refunded or Decimal("0"))

    if (invoice_amount is not None and invoice_total is not None
            and abs(invoice_total - invoice_amount) > AMOUNT_TOLERANCE):
        warnings.append("发票附件金额合计与填写的发票金额不一致，请核对")
    if (actual_amount is not None and net_total is not None
            and abs(net_total - actual_amount) > AMOUNT_TOLERANCE):
        warnings.append("支付与退款附件合计与填写的实付款不一致，请核对")

    if invoice_amount is not None and actual_amount is not None and actual_amount > invoice_amount:
        warnings.append("实付金额高于发票金额，请核对")

    if any(not PLATFORM_ORDER_NO_RE.match(a.order_no or "") for a in payments):
        warnings.append("某笔支付的平台单号格式不符（支付宝订单号/微信交易单号/京东订单编号），报销时需抄到发票上")

    if any(_has_file(a) and a.file.name.lower().endswith(IMAGE_SUFFIXES) and _is_dark(a.file)
           for a in payments + refunds):
        warnings.append("支付记录疑似深色模式，打印效果差，请改用亮色模式重截")

    if _buyer_mismatch(invoices, settings.EXPECTED_INVOICE_TITLE, "buyer_name"):
        warnings.append("发票抬头与要求不符")
    if _buyer_mismatch(invoices, settings.EXPECTED_INVOICE_TAX_ID, "buyer_id"):
        warnings.append("发票税号与要求不符")

    return warnings


def _has_file(attachment):
    return getattr(attachment, "file", None) is not None


def batch_group_issues(kinds, title, category_id, actual_amount):
    """批量提交单组的待补事项，与 batch_submit 的拒绝条件同源。空列表表示可提交。

    kinds 为组内附件类型（可含未知值），actual_amount 为解析后的实付金额（缺省 None）。
    单边发票组（有发票无支付记录）不要求实付金额，提交时由服务端固定为 0.00。
    """
    if not kinds:
        return ["缺少文件"]
    known = {choice[0] for choice in Attachment.KIND_CHOICES}
    issues = []
    if any(kind not in known for kind in kinds):
        issues.append("含未识别类型的文件，请先修正类型")
    if Attachment.KIND_INVOICE not in kinds:
        issues.append("缺少发票")
    if not (title or "").strip():
        issues.append("缺少明细")
    if not category_id:
        issues.append("类别无效或未选择")
    solo = Attachment.KIND_INVOICE in kinds and Attachment.KIND_PAYMENT not in kinds
    if not solo and actual_amount is None:
        issues.append("缺少实付金额")
    return issues


def batch_group_state(kinds, attachments):
    """批量预览组的对账结论 key：no_invoice / solo / mismatch / ok。

    前端只按 key 选文案，判定只在这里。mismatch 用发票合计与支付净额比对（容差同
    AMOUNT_TOLERANCE），任一侧金额全空时不判不一致。
    """
    if Attachment.KIND_INVOICE not in kinds:
        return "no_invoice"
    if Attachment.KIND_PAYMENT not in kinds:
        return "solo"
    invoice_total = _sum_amounts([a for a in attachments if a.kind == Attachment.KIND_INVOICE])
    paid = _sum_amounts([a for a in attachments if a.kind == Attachment.KIND_PAYMENT])
    refunded = _sum_amounts([a for a in attachments if a.kind == Attachment.KIND_REFUND])
    if invoice_total is None or paid is None:
        return "ok"
    net_total = paid - (refunded or Decimal("0"))
    if abs(invoice_total - net_total) > AMOUNT_TOLERANCE:
        return "mismatch"
    return "ok"


def _sum_amounts(attachments):
    """附件金额合计。全部为空时返回 None（不参与核对）。"""
    amounts = [a.amount for a in attachments if a.amount is not None]
    if not amounts:
        return None
    return sum(amounts, Decimal("0"))


def _is_dark(file_field):
    try:
        with Image.open(file_field) as img:
            stat = ImageStat.Stat(img.convert("RGB"))
            mean = sum(stat.mean) / len(stat.mean)
        return mean < DARK_MEAN_THRESHOLD
    except Exception:
        return False  # 图片损坏等场景交由表单校验/上传报错处理


def _buyer_mismatch(invoices, expected, key):
    """任一发票的识别值非空且与期望不符则 True。ocr_data 缺失则跳过该项。"""
    if not expected:
        return False
    for attachment in invoices:
        ocr = attachment.ocr_data or {}
        value = (ocr.get(key) or "").strip()
        if value and value != expected:
            return True
    return False
