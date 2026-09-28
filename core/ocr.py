"""华为云增值税发票 OCR 预填。凭据未配置或识别失败时返回空 dict，不阻塞上传。"""

import logging

from django.conf import settings

from huaweicloud_ocr_sdk.HWOcrClientToken import HWOcrClientToken

logger = logging.getLogger(__name__)

URI = "/v1.0/ocr/vat-invoice"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png")


def configured():
    return all(
        getattr(settings, name, "")
        for name in ("OCR_DOMAIN", "OCR_USERNAME", "OCR_PASSWORD", "OCR_REGION")
    )


def _parse_amount(raw):
    if raw is None:
        return None
    text = str(raw).replace("￥", "").replace("¥", "").replace(",", "").strip()
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def prefill(image_bytes):
    """识别发票图片，返回 {invoice_amount, invoice_no, buyer_name, buyer_id}；失败返回 {}。"""
    if not configured():
        return {}
    for attempt in (1, 2):  # 失败重试 1 次（重建客户端以重取 token）
        client = HWOcrClientToken(
            settings.OCR_DOMAIN,
            settings.OCR_USERNAME,
            settings.OCR_PASSWORD,
            settings.OCR_REGION,
        )
        try:
            response = client.request_ocr_service_base64(URI, image_bytes)
            if response is not None and response.status_code == 200:
                result = response.json().get("result") or {}
                return {
                    "invoice_amount": _parse_amount(result.get("total")),
                    "invoice_no": (result.get("number") or "").strip(),
                    "buyer_name": (result.get("buyer_name") or "").strip(),
                    "buyer_id": (result.get("buyer_id") or "").strip(),
                }
            if response is not None:
                logger.warning("OCR 返回 %s: %s", response.status_code, response.text[:200])
        except Exception:
            logger.exception("OCR 请求失败（第 %s 次）", attempt)
    return {}
