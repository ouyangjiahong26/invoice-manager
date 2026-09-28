# 报销看板（invoice-manager）

学生注册登录后自助提交报销材料（发票文件 + 支付截图 + 支付订单号 + 实付金额），按类别共享看板展示；每人只能修改自己的条目。管理员在 Django 后台审核，通过后一键导出报销 Excel（序号/明细/类别/实付款/付款人，按类别小记+合计）与票据 zip。

## 开发

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
uv run python manage.py migrate
uv run python manage.py runserver   # http://127.0.0.1:8000/
```

## 配置（环境变量，均可缺省）

- `DJANGO_SECRET_KEY`、`DEBUG`（默认 false）、`DJANGO_ALLOWED_HOSTS`、`DATABASE_URL`（默认 sqlite）
- `OCR_USERNAME` / `OCR_PASSWORD` / `OCR_DOMAIN` / `OCR_REGION`：华为云增值税发票 OCR 预填；缺省时上传页自动降级为手填
- `EXPECTED_INVOICE_TITLE` / `EXPECTED_INVOICE_TAX_ID`：期望发票抬头/税号校验；缺省时跳过

## 部署

gunicorn 监听 127.0.0.1:8100，systemd 单元 `baoxiao.service`，nginx 反代 + `/static/` 直供，媒体文件经 Django 鉴权路由下发。
