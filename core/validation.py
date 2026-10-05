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
# 平台单号：支付宝订单号（20 开头，28 位，银联等银行渠道可到 32 位）/ 微信交易单号（4 开头，28 位）
PLATFORM_ORDER_NO_RE = re.compile(r"^(20\d{26,30}|4\d{27})\Z")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
AMOUNT_TOLERANCE = Decimal("0.005")


def check_item(item):
    """检查条目，返回警告列表。"""
    warnings = []
    attachments = list(item.attachments.all())
    invoices = [a for a in attachments if a.kind == Attachment.KIND_INVOICE]
    payments = [a for a in attachments if a.kind == Attachment.KIND_PAYMENT]
    refunds = [a for a in attachments if a.kind == Attachment.KIND_REFUND]

    invoice_total = _sum_amounts(invoices)
    paid = _sum_amounts(payments)
    refunded = _sum_amounts(refunds)
    net_total = None
    if paid is not None or refunded is not None:
        net_total = (paid or Decimal("0")) - (refunded or Decimal("0"))

    if (item.invoice_amount is not None and invoice_total is not None
            and abs(invoice_total - item.invoice_amount) > AMOUNT_TOLERANCE):
        warnings.append("发票附件金额合计与填写的发票金额不一致，请核对")
    if (item.actual_amount is not None and net_total is not None
            and abs(net_total - item.actual_amount) > AMOUNT_TOLERANCE):
        warnings.append("支付与退款附件合计与填写的实付款不一致，请核对")

    if item.invoice_amount is not None and item.actual_amount > item.invoice_amount:
        warnings.append("实付金额高于发票金额，请核对")

    if any(not PLATFORM_ORDER_NO_RE.match(a.order_no or "") for a in payments):
        warnings.append("某笔支付的平台单号格式不符（支付宝订单号/微信交易单号），报销时需抄到发票上")

    if any(a.file.name.lower().endswith(IMAGE_SUFFIXES) and _is_dark(a.file)
           for a in payments + refunds):
        warnings.append("支付记录疑似深色模式，打印效果差，请改用亮色模式重截")

    if _buyer_mismatch(invoices, settings.EXPECTED_INVOICE_TITLE, "buyer_name"):
        warnings.append("发票抬头与要求不符")
    if _buyer_mismatch(invoices, settings.EXPECTED_INVOICE_TAX_ID, "buyer_id"):
        warnings.append("发票税号与要求不符")

    return warnings


def _sum_amounts(attachments):
    """附件金额合计；全部为空时返回 None（不参与核对）。"""
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
    """任一发票的识别值非空且与期望不符则 True；ocr_data 缺失则跳过该项。"""
    if not expected:
        return False
    for attachment in invoices:
        ocr = attachment.ocr_data or {}
        value = (ocr.get(key) or "").strip()
        if value and value != expected:
            return True
    return False
