# Repository Guidelines

## Project Overview
报销看板（发票报销管理）：账号由管理员在后台开通（用户名 = 姓名拼音+年级，如 ouyangjiahong22），学生登录后自助提交报销条目（发票文件 + 支付截图 + 支付订单号 + 实付金额），按类别共享看板展示，只能修改自己的条目；管理员在 Django admin 审核，通过后一键导出用户格式 Excel（按类别小记+合计）与票据 zip。中文界面（`zh-hans`），服务端渲染，无前端构建链。**开放注册已关闭（ADR-0003）**。

## Architecture & Data Flow
Django 5.2 项目，项目配置包 `config`，唯一 app `core`，全函数视图 + Django 模板。

```
浏览器 ──注册/登录──> core.views（函数视图）
  │                     ├─ board：按 Category.order 分组看板，?status= 过滤
  │                     ├─ item_create/item_update：ModelForm + 文件上传
  │                     ├─ file_serve：/items/<pk>/file/<kind>/ 鉴权下发（ADR-0002）
  │                     └─ export_excel/export_zip（staff only）：approved 条目导出
  └─ /admin/：条目审核（改 status）、类别管理
core.validation.check_item：两条警告规则（金额、深色截图），保存后共用，不阻断
```

关键约定：
- 媒体文件（发票/截图）**不经 nginx**，全部走 `file_serve` 鉴权路由（ADR-0002）。
- 导出只含 `status="approved"` 条目；Excel 格式对照人工汇总表（全局连续序号、A 列小记/合计标签、D 列 SUM 公式）。

## Key Directories
- `config/` — settings/urls/wsgi/asgi；settings 全环境变量驱动
- `core/` — models（Category、Item）、views、forms、validation、admin、templates/core/
- `docs/agents/` — 工程技能 harness 配置（issue tracker、分诊标签、领域文档约定）
- `docs/adr/` — 架构决策记录；`CONTEXT.md` — 领域术语表

## Development Commands
```bash
uv venv --python 3.12                 # 或 python3.12 -m venv .venv
uv pip install -r requirements.txt
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run python manage.py makemigrations core
uv run python manage.py test
```

## Code Conventions
- 函数视图 + `ModelForm`；权限用 `login_required` / `user_passes_test`，越权改他人条目抛 `PermissionDenied`（403）。
- 付款人 = `owner`（Django user），真实姓名存 `first_name`，账号开通时由管理员填写；不建 Person/Student 模型。
- 警告类业务规则唯一实现在 `core/validation.py: check_item(item)`，前端不重复实现规则本体。
- 中文 verbose_name/choices，英文标识符。

## Deployment
腾讯云（`ssh jump`）：`/home/ubuntu/baoxiao`（git pull 更新）+ `.venv` + systemd `baoxiao.service`（gunicorn 127.0.0.1:8100，`EnvironmentFile=.env`）+ PostgreSQL `baoxiao` 库 + nginx vhost `/etc/nginx/sites-enabled/baoxiao`（443 反代，80 仅 acme-challenge+301）+ acme.sh 证书（`/home/ubuntu/acme-invoice/`）。域名 `invoice.cislunarspace.cn`。

## Testing & QA
`python manage.py test`（Django TestCase）。涉及文件导出/上传的测试需构造临时 `MEDIA_ROOT`。

## 写作约定

- 任何输出（代码注释、文档、commit 信息、聊天回复）不使用「」符号；需要引用时用双引号 ""。

## Agent skills

- **Issue tracker**：GitHub Issues（`gh` CLI），配置见 `docs/agents/issue-tracker.md`；GitHub Project #7 同步工作状态（`/github-project`、`/triage`、`/open-pr`、`/merge-pr` 读取）。
- **分诊**：标签映射见 `docs/agents/triage-labels.md`（`/triage` 读取）。
- **领域文档**：约定见 `docs/agents/domain.md`（`/domain-modeling`、`/grill-with-docs` 读取）；根目录 `CONTEXT.md` 为术语表，`docs/adr/` 为决策记录——输出领域概念时按术语表用词，与 ADR 冲突时显式标注。
