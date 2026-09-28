# ADR-0001: 推倒旧发票管理，重建为学生自助报销看板

日期：2026-09-28　状态：已接受

## 背景

旧应用（`invoice_manager` + `manager`）面向单人使用：命令行批量 OCR 导入 PDF 发票 → Django admin 分类/标记已用/合并打印。实际工作流是多名学生各自收集发票与支付截图，由管理员手工汇总 Excel（序号/明细/类别/实付款/付款人/支付截图/票据，按类别小记+合计），旧应用与之脱节。

## 决策

整体删除旧代码，在原仓库内重建：

- 新项目骨架 `config` + 唯一 app `core`，Django 5.2 / Python 3.12。
- 学生注册登录自助提交条目（发票文件+支付截图+订单号+实付金额），共享看板按类别展示，只能改自己的条目。
- 管理员审核（Django admin），一键导出用户格式 Excel（openpyxl）与票据 zip。
- 仅复用旧代码中不存在等价物的部分：`huaweicloud_ocr_sdk/HWOcrClientToken.py`（OCR 预填）；删除 AK/SK 客户端、demo 脚本、`apig_sdk/`、旧 `import_invoice.py`（PDF 光栅化依赖 ImageMagick/Ghostscript，不再需要——发票图片走 OCR，PDF 手填）。

## 后果

- 旧 `manager.Invoice`（发票去重池）模型与其数据弃用；发票号码降级为条目上的普通文本字段。
- 服务器部署形态从无变为 gunicorn + systemd + nginx + PostgreSQL（见仓库 README）。
