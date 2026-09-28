"""票据识别：图片走视觉接口；PDF 先提文本，文本不足或解析为空则渲染成图再走视觉接口。

凭据未配置或识别失败时返回空 dict，不阻塞上传。返回的 dict 含 "_source"
（"text" 或 "image"），供页面显示识别来源。
"""

import base64
import json
import logging
import urllib.error
import urllib.request

import pymupdf

from django.conf import settings

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".pdf")
TIMEOUT_SECONDS = 60
PDF_TEXT_MIN_CHARS = 50
PDF_RENDER_SCALE = 2
PLATFORMS = ("alipay", "wechat", "other", "unknown")

INVOICE_PROMPT = (
    "这是一张发票文件。只输出一个 JSON 对象，不要输出任何其他文字或代码围栏，格式："
    '{"invoice_amount": 数字, "invoice_no": "发票号码", '
    '"remark_order_no": "发票备注区的订单号/单号", '
    '"buyer_name": "购买方名称", "buyer_id": "购买方纳税人识别号"}。'
    "invoice_amount 为价税合计（元，数字）；无法识别的字段：金额用 null，其余用空字符串。"
)

PAYMENT_PROMPT = (
    "这是一张支付凭证（支付宝或微信的支付记录、账单或交易详情）。只输出一个 JSON 对象，"
    '不要输出任何其他文字或代码围栏，格式：{"amount": 数字, "platform": "alipay|wechat|other|unknown", '
    '"order_no": "平台单号", "merchant_no": "商户单号"}。'
    "amount 为支付金额（元，数字）；order_no 指平台交易单号：支付宝订单号（20 开头，共 28 位）"
    "或微信交易单号（4 开头，共 28 位）；merchant_no 指商家订单号/商户单号（长度不定、可含字母）。"
    '无法识别的字段：amount 用 null，platform 用 "unknown"，其余用空字符串。'
)

REFUND_PROMPT = PAYMENT_PROMPT.replace(
    "这是一张支付凭证（支付宝或微信的支付记录、账单或交易详情）。",
    "这是一张退款凭证（支付宝或微信的退款记录或交易详情）。",
).replace("amount 为支付金额（元，数字）；", "amount 为退款金额（元，数字）；")

PROMPTS = {
    "invoice": INVOICE_PROMPT,
    "payment": PAYMENT_PROMPT,
    "refund": REFUND_PROMPT,
}


def configured():
    return bool(settings.DEEPSEEK_API_KEY)


def validate_upload(upload):
    """返回错误文案；None 表示通过。"""
    if not (upload.name or "").lower().endswith(ALLOWED_EXTENSIONS):
        return "仅支持 jpg / jpeg / png / pdf 文件"
    if upload.size > MAX_FILE_BYTES:
        return "单个文件不能超过 10MB"
    return None


def prefill(data, filename, kind):
    """识别单个票据文件，返回回填字段 dict；失败返回 {}。"""
    if not configured() or kind not in PROMPTS:
        return {}
    try:
        if (filename or "").lower().endswith(".pdf"):
            return _prefill_pdf(data, kind)
        return _prefill_image(data, kind)
    except Exception:
        logger.exception("识别失败：%s", filename)
        return {}


def _prefill_image(data, kind):
    result = _recognize([_image_part(data), _text_part(PROMPTS[kind])], kind)
    if result:
        result["_source"] = "image"
    return result


def _prefill_pdf(data, kind):
    """PDF：文本充足走文本接口；否则逐页渲染成 PNG 走视觉接口。"""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        text = "\n".join(page.get_text() for page in doc).strip()
        if len(text) >= PDF_TEXT_MIN_CHARS:
            result = _recognize([_text_part(PROMPTS[kind] + "\n\n以下是发票文件的文本内容：\n" + text)], kind)
            if result:
                result["_source"] = "text"
                return result
        for page in doc:
            matrix = pymupdf.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE)
            png = page.get_pixmap(matrix=matrix).tobytes("png")
            result = _recognize([_image_part(png), _text_part(PROMPTS[kind])], kind)
            if result:
                result["_source"] = "image"
                return result
    return {}


def _recognize(parts, kind):
    """调一次模型并解析，返回有意义的结果；失败/无意义返回 {}。"""
    response = _post({
        "model": settings.DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": parts}],
        "thinking": {"type": "disabled"},
    })
    if not response:
        return {}
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("DeepSeek 回复结构异常：%s", str(response)[:200])
        return {}
    try:
        result = _parse_content(content, kind)
    except (TypeError, ValueError):
        logger.warning("DeepSeek 回复无法解析为 JSON：%s", (content or "")[:200])
        return {}
    return result if _meaningful(kind, result) else {}


def _post(payload):
    """POST chat/completions，失败重试 1 次；不支持 thinking 参数时去掉并重试。"""
    for attempt in (1, 2):
        request = urllib.request.Request(
            settings.DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + settings.DEEPSEEK_API_KEY,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read()[:300]
            if exc.code == 400 and "thinking" in payload:
                logger.warning("模型不支持 thinking 参数，去掉后重试：%s", body)
                payload = {key: value for key, value in payload.items() if key != "thinking"}
                continue
            logger.warning("DeepSeek 返回 %s: %s", exc.code, body)
        except Exception:
            logger.exception("DeepSeek 请求失败（第 %s 次）", attempt)
    return None


def _text_part(text):
    return {"type": "text", "text": text}


def _image_part(data):
    mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    url = f"data:{mime};base64," + base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": url}}


def _meaningful(kind, result):
    if kind == "invoice":
        return result.get("invoice_amount") is not None or bool(result.get("invoice_no"))
    return result.get("amount") is not None or bool(result.get("order_no")) or bool(result.get("merchant_no"))


def _parse_amount(raw):
    if raw is None:
        return None
    text = str(raw).replace("￥", "").replace("¥", "").replace(",", "").strip()
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def _text(raw):
    return str(raw or "").strip()


def _parse_content(content, kind):
    """解析回复正文为回填字段；JSON 非法时抛 ValueError。实测为纯 JSON，兜底剥离围栏。"""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)
    if kind == "invoice":
        return {
            "invoice_amount": _parse_amount(data.get("invoice_amount")),
            "invoice_no": _text(data.get("invoice_no")),
            "remark_order_no": _text(data.get("remark_order_no")),
            "buyer_name": _text(data.get("buyer_name")),
            "buyer_id": _text(data.get("buyer_id")),
        }
    platform = _text(data.get("platform")).lower()
    return {
        "amount": _parse_amount(data.get("amount")),
        "platform": platform if platform in PLATFORMS else "unknown",
        "order_no": _text(data.get("order_no")),
        "merchant_no": _text(data.get("merchant_no")),
    }
