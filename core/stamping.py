"""导出票据标注：把附件字节标注成盖红色标记的 PDF。

规格来自人工批次处理流程（共享盘 20260924 课题组报销 处理脚本.py）：
条目导出序号盖在该条目全部附件每一页左下角（22pt hebo），各支付记录的
平台单号逐行写在第一张发票附件首页序号上方（8.5pt china-s，前缀称谓对齐
支付平台截图字段名），图片附件先转单页 PDF（150dpi 换算页宽）。
只处理内存字节，不碰 ORM 与文件系统。任何一步失败返回 None，
调用方降级把原文件放入导出包（详见 CONTEXT.md 票据标注）。
"""

import pymupdf
from django.conf import settings

from .validation import (
    ALIPAY_ORDER_NO_RE, IMAGE_SUFFIXES, JD_ORDER_NO_RE, WECHAT_ORDER_NO_RE,
)

RED = (1, 0, 0)
# 单号前缀称谓对齐支付平台截图里的字段名，方便秘书逐字核对。判不出平台时兜底
PLATFORM_LABELS = {"alipay": "订单号", "wechat": "交易单号"}
FALLBACK_LABEL = "平台单号"


def order_lines(payments):
    """把支付记录附件列表转成发票首页单号标注行。order_no 为空的支付不生成行。"""
    return [
        f"{_label(payment, no)}: {no}"
        for payment in payments
        if (no := (payment.order_no or "").strip())
    ]


def _label(payment, no):
    ocr = payment.ocr_data if isinstance(payment.ocr_data, dict) else {}
    platform = (ocr.get("platform") or "").strip()
    if platform in PLATFORM_LABELS:
        return PLATFORM_LABELS[platform]
    if ALIPAY_ORDER_NO_RE.match(no):
        return PLATFORM_LABELS["alipay"]
    if WECHAT_ORDER_NO_RE.match(no):
        return PLATFORM_LABELS["wechat"]
    if JD_ORDER_NO_RE.match(no):
        return "总订单编号"
    return FALLBACK_LABEL


def stamped_pdf(data, filename, seq, lines):
    """附件字节标注为 PDF：图片先转单页 PDF，每页盖序号、首页写单号行。

    lines 为空则只盖序号。文件损坏、加密无法写入等一律返回 None。
    """
    try:
        if filename.lower().endswith(IMAGE_SUFFIXES):
            data = _image_pdf(data)
        doc = pymupdf.open(stream=data, filetype="pdf")
        _stamp(doc, seq, lines)
        return doc.tobytes()
    except Exception:
        return None


def _image_pdf(data):
    """把图片字节转成单页 PDF：按 150dpi 换算页面尺寸，图片全幅铺放（人工脚本口径）。"""
    pix = pymupdf.Pixmap(data)
    doc = pymupdf.open()
    page = doc.new_page(width=pix.width * 72 / 150, height=pix.height * 72 / 150)
    page.insert_image(page.rect, stream=data)
    return doc.tobytes()


def _stamp(doc, seq, lines):
    margin = settings.EXPORT_STAMP_MARGIN
    seq_size = settings.EXPORT_STAMP_SEQ_SIZE
    for pno, page in enumerate(doc):
        height = page.rect.height
        page.insert_text((margin, height - margin), seq,
                         fontsize=seq_size, fontname="hebo", color=RED)
        if pno == 0 and lines:
            y = height - margin - seq_size
            for line in lines:
                page.insert_text((margin, y), line,
                                 fontsize=settings.EXPORT_STAMP_ORDER_SIZE,
                                 fontname="china-s", color=RED)
                y -= settings.EXPORT_STAMP_ORDER_GAP
