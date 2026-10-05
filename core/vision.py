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
    '"buyer_name": "购买方名称", "buyer_id": "购买方纳税人识别号", '
    '"items_summary": "发票经营业务范围或商品服务内容摘要"}。'
    "invoice_amount 为价税合计（元，数字）；无法识别的字段：金额用 null，其余用空字符串。"
)

PAYMENT_PROMPT = (
    "这是一张支付凭证（支付宝或微信的支付记录、账单或交易详情）。只输出一个 JSON 对象，"
    '不要输出任何其他文字或代码围栏，格式：{"amount": 数字, "platform": "alipay|wechat|other|unknown", '
    '"order_no": "平台单号", "merchant_no": "商户单号"}。'
    "amount 为支付金额（元，数字）；order_no 指平台交易单号：支付宝订单号（20 开头，通常 28 位，"
    "银联等银行渠道可到 32 位）或微信交易单号（4 开头，共 28 位）；"
    "merchant_no 指商家订单号/商户单号（长度不定、可含字母）。"
    '无法识别的字段：amount 用 null，platform 用 "unknown"，其余用空字符串。'
)

REFUND_PROMPT = PAYMENT_PROMPT.replace(
    "这是一张支付凭证（支付宝或微信的支付记录、账单或交易详情）。",
    "这是一张退款凭证（支付宝或微信的退款记录或交易详情）。",
).replace("amount 为支付金额（元，数字）；", "amount 为退款金额（元，数字）；")

SUPPORT_PROMPT = (
    "这是一张报销证明材料（充值或余额消费流水、转账回单、订单或账单截图等佐证文件）。"
    "只输出一个 JSON 对象，不要输出任何其他文字或代码围栏，格式："
    '{"amount": 数字, "items_summary": "内容摘要（如：腾讯云充值 1096.50 元入余额）"}。'
    "amount 为图面金额（元，数字），识别不出用 null；items_summary 简述这份材料证明什么。"
)

PROMPTS = {
    "invoice": INVOICE_PROMPT,
    "payment": PAYMENT_PROMPT,
    "refund": REFUND_PROMPT,
    "support": SUPPORT_PROMPT,
}

DETECT_KINDS = ("invoice", "payment", "refund", "support", "unknown")

DETECT_PROMPT = (
    "这是一张报销票据文件，可能是电子发票、支付凭证（支付宝或微信的支付记录、账单或交易详情）、"
    "退款凭证（支付宝或微信的退款记录或交易详情）或证明材料（充值/余额消费流水、转账回单、"
    "订单或账单截图等佐证文件）之一。先判断票据类型，再提取字段。"
    "只输出一个 JSON 对象，不要输出任何其他文字或代码围栏，格式："
    '{"kind": "invoice|payment|refund|support|unknown", "invoice_amount": 数字, '
    '"invoice_no": "发票号码", "remark_order_no": "发票备注区的订单号/单号", '
    '"buyer_name": "购买方名称", "buyer_id": "购买方纳税人识别号", '
    '"items_summary": "发票经营业务范围或商品服务内容摘要", "amount": 数字, '
    '"platform": "alipay|wechat|other|unknown", "order_no": "平台单号", '
    '"merchant_no": "商户单号", "handwritten_notes": "图面上的手写文字"}。'
    "kind 为票据类型：invoice 发票 / payment 支付凭证 / refund 退款凭证 / support 证明材料"
    "（流水、回单、账单等佐证，不是发票也不是支付凭证本身），看不出类型用 unknown；"
    "invoice_amount 为价税合计，amount 为支付、退款或流水金额（元，数字）；"
    "order_no 指平台交易单号：支付宝订单号（20 开头，通常 28 位，银联等银行渠道可到 32 位）"
    "或微信交易单号（4 开头，共 28 位）；"
    "merchant_no 指商家订单号/商户单号（长度不定、可含字母）。"
    "handwritten_notes 抄录图面上所有手写内容（如手写编号、手写金额、勾画批注），没有手写用空字符串。"
    "与票据类型无关的字段以及无法识别的字段：金额用 null，platform 用 \"unknown\"，其余用空字符串。"
)


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
        if _is_pdf(data, filename):
            return _prefill_pdf(data, kind, PROMPTS[kind])
        return _prefill_image(data, kind, PROMPTS[kind])
    except Exception:
        logger.exception("识别失败：%s", filename)
        return {}


def detect(data, filename):
    """判单个票据文件类型并提取字段，返回含 kind 的字段 dict；失败返回 {}。"""
    if not configured():
        return {}
    try:
        if _is_pdf(data, filename):
            return _prefill_pdf(data, "detect", DETECT_PROMPT)
        return _prefill_image(data, "detect", DETECT_PROMPT)
    except Exception:
        logger.exception("识别失败：%s", filename)
        return {}


def reread(data, filename, hints):
    """配对智能体定向重读（ADR-0008）：标准 detect 模式加针对性线索，返回同形状字段 dict。

    hints 为给模型的补充线索文本（候选记录摘要、要重点核对的区域等）；失败返回 {}。
    """
    if not configured():
        return {}
    prompt = (
        "重新仔细识别这张报销票据文件。此前自动识别可能看错了手写标注、备注区文字或金额数字，"
        "请逐字核对后输出修正结果。\n\n" + DETECT_PROMPT + "\n\n补充线索：\n" + hints
    )
    try:
        if _is_pdf(data, filename):
            return _prefill_pdf(data, "detect", prompt)
        return _prefill_image(data, "detect", prompt)
    except Exception:
        logger.exception("重读失败：%s", filename)
        return {}


def _is_pdf(data, filename):
    """按内容判 PDF：头部一段内有 %PDF- 魔数即算（腾讯云导出的发票文件名可能无后缀，
    且头部带脏字节，仅按文件名分流会把 PDF 字节当图发给识别服务被拒收）。"""
    if (filename or "").lower().endswith(".pdf"):
        return True
    return b"%PDF-" in data[:1024]


def _prefill_image(data, kind, prompt):
    result = _recognize([_image_part(data), _text_part(prompt)], kind)
    if result:
        result["_source"] = "image"
    return result


def _prefill_pdf(data, kind, prompt):
    """PDF：文本充足走文本接口；否则逐页渲染识别。

    多页同为一种票据时合并结果——支付/退款金额相加（一份账单 PDF 常含多笔支付，
    如 20260924 批次的 100+50+10 对一张 160 发票），发票金额取首个非空值（多页
    通常是同一发票重复打印）；各页类型不一致时退回首个有效页的结果。
    """
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        text = "\n".join(page.get_text() for page in doc).strip()
        if len(text) >= PDF_TEXT_MIN_CHARS:
            result = _recognize([_text_part(prompt + "\n\n以下是文件中提取的文本内容：\n" + text)], kind)
            if result:
                result["_source"] = "text"
                return result
        results = []
        for page in doc:
            matrix = pymupdf.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE)
            png = page.get_pixmap(matrix=matrix).tobytes("png")
            result = _recognize([_image_part(png), _text_part(prompt)], kind)
            if result:
                results.append(result)
    if not results:
        return {}
    merged = _merge_page_results(results)
    merged["_source"] = "image"
    return merged


def _merge_page_results(results):
    """合并逐页识别结果；各页类型不一致时返回首个有效页。"""
    kinds = {result.get("kind") for result in results} - {"unknown", None}
    if len(kinds) != 1:
        return dict(results[0])
    merged = dict(results[0])
    if merged.get("kind") in ("payment", "refund"):
        values = [result.get("amount") for result in results if result.get("amount") is not None]
        if values:
            merged["amount"] = round(sum(values), 2)
    for key in ("order_no", "merchant_no", "invoice_no", "remark_order_no", "items_summary"):
        for result in results:
            if result.get(key):
                merged[key] = result[key]
                break
    notes = [result.get("handwritten_notes") for result in results if result.get("handwritten_notes")]
    if notes:
        merged["handwritten_notes"] = "；".join(notes)
    return merged


def _recognize(parts, kind):
    """调一次模型并解析，返回有意义的结果；失败/无意义返回 {}。"""
    content = _content(parts)
    if content is None:
        return {}
    try:
        result = _parse_content(content, kind)
    except (TypeError, ValueError):
        logger.warning("DeepSeek 回复无法解析为 JSON：%s", (content or "")[:200])
        return {}
    return result if _meaningful(kind, result) else {}


def _content(parts):
    """调一次模型并返回回复正文；失败返回 None。"""
    response = _post({
        "model": settings.DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": parts}],
        "thinking": {"type": "disabled"},
    })
    if not response:
        return None
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("DeepSeek 回复结构异常：%s", str(response)[:200])
        return None


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
    if kind == "detect":
        return (result.get("kind") != "unknown" or result.get("amount") is not None
                or result.get("invoice_amount") is not None)
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


def _load_json(content):
    """解析回复正文为 JSON；非法时抛 ValueError。实测为纯 JSON，兜底剥离围栏。"""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def _parse_content(content, kind):
    """解析回复正文为回填字段；JSON 非法时抛 ValueError。"""
    data = _load_json(content)
    if kind == "invoice":
        return {
            "invoice_amount": _parse_amount(data.get("invoice_amount")),
            "invoice_no": _text(data.get("invoice_no")),
            "remark_order_no": _text(data.get("remark_order_no")),
            "buyer_name": _text(data.get("buyer_name")),
            "buyer_id": _text(data.get("buyer_id")),
            "items_summary": _text(data.get("items_summary")),
        }
    if kind == "support":
        return {
            "amount": _parse_amount(data.get("amount")),
            "items_summary": _text(data.get("items_summary")),
        }
    platform = _text(data.get("platform")).lower()
    if kind == "detect":
        detected = _text(data.get("kind")).lower()
        return {
            "kind": detected if detected in DETECT_KINDS else "unknown",
            "invoice_amount": _parse_amount(data.get("invoice_amount")),
            "invoice_no": _text(data.get("invoice_no")),
            "remark_order_no": _text(data.get("remark_order_no")),
            "buyer_name": _text(data.get("buyer_name")),
            "buyer_id": _text(data.get("buyer_id")),
            "items_summary": _text(data.get("items_summary")),
            "amount": _parse_amount(data.get("amount")),
            "platform": platform if platform in PLATFORMS else "unknown",
            "order_no": _text(data.get("order_no")),
            "merchant_no": _text(data.get("merchant_no")),
            "handwritten_notes": _text(data.get("handwritten_notes")),
        }
    return {
        "amount": _parse_amount(data.get("amount")),
        "platform": platform if platform in PLATFORMS else "unknown",
        "order_no": _text(data.get("order_no")),
        "merchant_no": _text(data.get("merchant_no")),
    }


def group_suggest(records):
    """LLM 兜底配对：records 为未配对记录摘要（{id, kind, amount, order_no, merchant_no,
    remark_order_no}），返回可成组的 id 分组 [[id, ...], ...]；失败返回 []（退化为未配对
    提示，不阻断）。"""
    if not configured() or not records:
        return []
    prompt = (
        "以下是一批未能按单号自动配对的报销票据记录（kind：invoice 发票 / payment 支付 / "
        "refund 退款；amount 为金额，order_no 为平台单号，merchant_no 为商户单号，"
        "remark_order_no 为发票备注单号，items_summary 为商品或订单摘要）。"
        "按单号、金额与常识把能组成一条报销的记录归组：每组至少一张发票（invoice）和一笔支付"
        "（payment），退款（refund）挂到对应支付所在组。一笔支付可能对应多张发票（合并下单、"
        "分单开票），这些发票与该笔支付应归入同一组。归组前先核算金额：每组的发票金额合计"
        "应与支付金额相等，或只差商家优惠、立减造成的小额差异（几元以内），合计对不上的组合"
        "不要输出；结合商家、订单号与商品摘要判断归属。金额与组合对不上、或同金额记录"
        "多到无法区分时不要强行分组。"
        '只输出一个 JSON 对象，不要输出任何其他文字或代码围栏，格式：{"groups": [[id, ...], ...]}，'
        "groups 只包含能配对的记录 id，配不上的不要输出。"
    )
    try:
        content = _content([_text_part(
            prompt + "\n\n记录列表：\n" + json.dumps(records, ensure_ascii=False))])
        if content is None:
            return []
        valid = {record["id"] for record in records}
        groups = _load_json(content).get("groups")
        if not isinstance(groups, list):
            return []
        result = []
        for group in groups:
            if not isinstance(group, list):
                continue
            ids = [i for i in group if i in valid]
            if len(ids) >= 2:
                result.append(ids)
        return result
    except Exception:
        logger.warning("LLM 配对建议解析失败", exc_info=True)
        return []


def field_suggest(groups, categories):
    """LLM 建议明细文案与类别：groups 为组成员摘要列表的列表（成员 {id, kind, amount,
    items_summary}），categories 为 [{id, name, description}]；返回
    {"suggestions": [{"title", "category_id"}]}（与组等长，无建议的位置为 None）；
    失败返回 {}（确认页字段留空手填）。"""
    if not configured() or not groups or not categories:
        return {}
    prompt = (
        "以下是若干组报销票据（每组至少一张发票和一笔支付；kind：invoice 发票 / payment 支付 / "
        "refund 退款；amount 为金额；items_summary 为发票商品服务摘要）。参考类别表，为每组拟一条"
        "简短的明细文案（买了什么，如：夸克网盘会员）并从类别表中选一个类别 id。"
        "只输出一个 JSON 对象，不要输出任何其他文字或代码围栏，格式："
        '{"suggestions": [{"title": "明细文案", "category_id": 数字}, ...]}，'
        "suggestions 与组顺序一致、等长。"
    )
    try:
        content = _content([_text_part(
            prompt + "\n\n分组：\n" + json.dumps(groups, ensure_ascii=False)
            + "\n\n类别表：\n" + json.dumps(categories, ensure_ascii=False))])
        if content is None:
            return {}
        suggestions = _load_json(content).get("suggestions")
        if not isinstance(suggestions, list):
            return {}
        valid_ids = {int(category["id"]) for category in categories}
        result = []
        for entry in suggestions[:len(groups)]:
            if not isinstance(entry, dict):
                result.append(None)
                continue
            title = _text(entry.get("title"))[:200]
            try:
                category_id = int(entry.get("category_id"))
            except (TypeError, ValueError):
                category_id = None
            if category_id not in valid_ids:
                category_id = None
            result.append({"title": title, "category_id": category_id} if title else None)
        result.extend([None] * (len(groups) - len(result)))  # 模型少给时补 None，保证与组等长
        return {"suggestions": result}
    except Exception:
        logger.warning("LLM 字段建议解析失败", exc_info=True)
        return {}
