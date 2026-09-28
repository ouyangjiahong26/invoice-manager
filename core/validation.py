"""报销条目自动校验：返回警告字符串列表，只警告不阻断提交。

后端保存后以 messages 形式展示。
"""

from PIL import Image, ImageStat

# 截图 RGB 均值低于该阈值视为疑似深色模式
DARK_MEAN_THRESHOLD = 80


def check_item(item):
    """检查条目，返回警告列表。"""
    warnings = []

    if item.invoice_amount is not None and item.actual_amount > item.invoice_amount:
        warnings.append("实付金额高于发票金额，请核对")

    if item.payment_screenshot:
        try:
            with Image.open(item.payment_screenshot) as img:
                stat = ImageStat.Stat(img.convert("RGB"))
                mean = sum(stat.mean) / len(stat.mean)
            if mean < DARK_MEAN_THRESHOLD:
                warnings.append("截图疑似深色模式，打印效果差，请改用亮色模式重截")
        except Exception:
            pass  # 图片损坏等场景交由表单校验/上传报错处理

    return warnings
