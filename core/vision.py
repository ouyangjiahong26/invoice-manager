"""DeepSeek 识图发票预填。凭据未配置或识别失败时返回空 dict，不阻塞上传。"""

import base64
import json
import logging
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png")
TIMEOUT_SECONDS = 30

PROMPT = (
    "识别图片中的发票信息。只输出一个 JSON 对象，不要输出任何其他文字或代码围栏，"
    '格式：{"invoice_amount": 数字, "invoice_no": "发票号码", '
    '"buyer_name": "购买方名称", "buyer_id": "纳税人识别号"}。'
    "金额为数字（元）；无法识别的字段：金额用 null，其余用空字符串。"
)


def configured():
    return bool(settings.DEEPSEEK_API_KEY)


def _parse_amount(raw):
    if raw is None:
        return None
    text = str(raw).replace("￥", "").replace("¥", "").replace(",", "").strip()
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def _parse_content(content):
    """解析回复正文。实测为纯 JSON；兜底剥离可能的 ```json 围栏。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)
    return {
        "invoice_amount": _parse_amount(data.get("invoice_amount")),
        "invoice_no": str(data.get("invoice_no") or "").strip(),
        "buyer_name": str(data.get("buyer_name") or "").strip(),
        "buyer_id": str(data.get("buyer_id") or "").strip(),
    }


def _data_url(image_bytes):
    mime = "image/png" if image_bytes.startswith(b"\x89PNG") else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode()


def prefill(image_bytes):
    """识别发票图片，返回 {invoice_amount, invoice_no, buyer_name, buyer_id}；失败返回 {}。"""
    if not configured():
        return {}
    request = urllib.request.Request(
        settings.DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps({
            "model": settings.DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": _data_url(image_bytes)}},
                {"type": "text", "text": PROMPT},
            ]}],
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + settings.DEEPSEEK_API_KEY,
        },
        method="POST",
    )
    for attempt in (1, 2):  # 失败重试 1 次
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status == 200:
                    content = json.load(response)["choices"][0]["message"]["content"]
                    return _parse_content(content)
                logger.warning("DeepSeek 返回 %s: %s", response.status, response.read()[:200])
        except Exception:
            logger.exception("DeepSeek 请求失败（第 %s 次）", attempt)
    return {}
