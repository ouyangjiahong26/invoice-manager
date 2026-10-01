# AGENTS.md

## Project Overview
报销管理（发票报销管理）：账号由管理员在后台开通（用户名 = 姓名拼音+年级，如 ouyangjiahong22），学生登录后自助提交报销条目（发票/支付记录/退款记录附件 + AI 识别回填 + 实付金额），在共享表格视图中查看与筛选（批次切换、行内编辑；学生限改己行），点行打开侧边栏承载新建/编辑/附件/留痕；管理员在表格内审核（改状态/付款人）、拖拽调序、按报销批次一键导出用户格式 Excel（按类别小记+合计）与票据 zip。中文界面（`zh-hans`），服务端渲染，无前端构建链。**开放注册已关闭（ADR-0003）**。

## Architecture & Data Flow
Django 6.1 项目，项目配置包 `config`，唯一 app `core`，全函数视图 + Django 模板。

```
浏览器 ──注册/登录──> core.views（函数视图）
  │                     ├─ board：按批次展示表格视图（序号=导出序号；?batch=&status=&category=&payer=&q= 筛选）
  │                     ├─ item_create/item_update/item_panel：侧边栏新建/编辑（ItemPanelForm + 多附件上传 invoices/payments/refunds）
  │                     ├─ item_field_update/item_reorder/batch_add：表格行内单字段保存 / staff 类别内拖拽调序 / staff 建批
  │                     ├─ attachment_update/attachment_delete：已保存附件行内编辑/删除
  │                     ├─ file_serve：/items/attachments/<pk>/file/ 鉴权下发（ADR-0002）
  │                     ├─ prefill：附件文件 → DeepSeek 识别 → JSON 回填（无状态）
  │                     ├─ 批量配对 /items/batch/：stage 暂存上传 → detect 逐张识别 → pair 规则配对 → agent-round 配对智能体多轮定向重读（ADR-0008）→ submit 按组建条目；单边发票组可提交（实付 0.00）
  │                     └─ export_excel/export_zip（staff only）：按批次导出 approved 条目；zip 附件盖票据标注（每页导出序号、第一张发票附件首页写平台单号、图片转 PDF，见 core/stamping.py 与 CONTEXT.md）
  └─ /admin/：条目审核（改 status）、类别/批次管理（含 description 参考说明）
core.validation.check_item：五条警告规则（附件合计、实付>发票、平台单号、深色支付记录、抬头/税号），保存后共用，不阻断
```

关键约定：
- 条目下可有多个 `Attachment`（kind ∈ invoice/payment/refund），金额与号码（平台单号/商户单号/发票号码）挂在附件上，见 ADR-0005。
- 条目必填归属报销批次（Batch），学生提交自动归最新批，导出按批；条目顺序 `position` 持久化，导出序号 = 按（类别顺序、position、id）排当前批次全量条目后的 1..N；staff 拖拽调序限类别内，行内改类别自动归尾，调序与 position 变化不留痕（ADR-0007）。
- 实付款 = 支付记录合计 − 退款记录合计；发票金额 = 发票附件合计；页面自动回填、服务端 `check_item` 核对一致性。
- 媒体文件（发票/支付记录）**不经 nginx**，全部走 `file_serve` 鉴权路由（ADR-0002）。
- 导出只含 `status="approved"` 条目；Excel 格式对照人工汇总表（全局连续序号、A 列小记/合计标签、D 列 SUM 公式）。
- DeepSeek 凭据与期望抬头/税号走环境变量，缺省静默降级，不报错。
- 附件变化经 `core.audit.mark_attachments` 注入快照，仍由 Item 信号统一落 `AuditLog`。

## Key Directories
- `config/` — settings/urls/wsgi/asgi；settings 全环境变量驱动
- `core/` — models（Batch、Category、Item、Attachment、AuditLog）、views、forms、validation、vision、pairing、staging（配对会话暂存）、audit、attachments（附件构造/解析共享工具）、suggest（LLM 配对/建议共享工具）、admin、management/commands（import_batch）、templates/core/
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
uv run python manage.py import_batch <目录> [--batch 名称] [--dry-run]   # AI 导入历史批次目录（纯 AI，需 DEEPSEEK_API_KEY）
```

## Code Conventions
- 函数视图 + `ModelForm`；权限用 `login_required` / `user_passes_test`，越权改他人条目抛 `PermissionDenied`（403）。
- 付款人 = `owner`（Django user），真实姓名存 `first_name`，账号开通时由管理员填写；不建 Person/Student 模型。
- 警告类业务规则唯一实现在 `core/validation.py: check_item(item)`，前端不重复实现规则本体。
- 中文 verbose_name/choices，英文标识符。

## Deployment
腾讯云（`ssh jump`）：`/home/ubuntu/baoxiao`（git pull 更新）+ `.venv` + systemd `baoxiao.service`（gunicorn 127.0.0.1:8100，`EnvironmentFile=.env`）+ PostgreSQL `baoxiao` 库 + nginx vhost `/etc/nginx/sites-enabled/baoxiao`（443 反代，80 仅 acme-challenge+301）+ acme.sh 证书（`/home/ubuntu/acme-invoice/`）。域名见服务器 nginx vhost 配置。

## Testing & QA
`python manage.py test`（Django TestCase）。涉及文件导出/上传的测试需构造临时 `MEDIA_ROOT`。

验收标准：真实报销批次按人名分文件夹整理，某人登录后把自己文件夹里的文件整批丢进批量提交，应全部正确识别并配对成组（对照人工最终整理版本判定）。验收数据私密，只放共享盘本地跑，路径与内容不入仓库、不上生产。

## 写作约定

- 任何输出（代码注释、文档、commit 信息、聊天回复）不使用「」符号；需要引用时用双引号 ""。

## Agent skills

- **Issue tracker**：GitHub Issues（`gh` CLI），配置见 `docs/agents/issue-tracker.md`；GitHub Project #7 同步工作状态（`/github-project`、`/triage`、`/open-pr`、`/merge-pr` 读取）。
- **分诊**：标签映射见 `docs/agents/triage-labels.md`（`/triage` 读取）。
- **领域文档**：约定见 `docs/agents/domain.md`（`/domain-modeling`、`/grill-with-docs` 读取）；根目录 `CONTEXT.md` 为术语表，`docs/adr/` 为决策记录——输出领域概念时按术语表用词，与 ADR 冲突时显式标注。

## gh CLI 已知问题

- `gh project item-list --format json` 的 `fieldValues` 可能为 `null`（本仓库 Project #7 全部 item 即如此）：字段全空时无法区分"无值"与"返回缺失"，需对照 GitHub 网页或其他途径确认。
- 顶层 GraphQL root 没有 `projectV2` 字段，需走 `organization(login:...){projectV2(...)}`。
- GraphQL 查询中对 `fieldValues` 或内联片段使用别名会报 `Expected NAME, actual: (none)` 解析错误；省略别名即可。

## 交流语言

始终使用中文与用户交流。代码、commit message、PR 描述等技术输出也用中文。

## 写作要求

所有面向人读的文本（注释、CONTEXT.md、ADR、issue 评论、PR 描述、agent brief、triage notes、Sphinx 文档、Agent 回复）应当：

- 准确、清楚、简洁；先理解材料，再提炼结论。
- 按逻辑组织，区分相近概念；不用空泛、夸大的修饰语。
- 面向实际读者，从已知事实推到陌生结论；用分析说服，不装腔或堆砌。

## 编码准则

- **先理解再改动**：完整阅读目标文件、相似实现和相关测试；不确定 API 或惯例时查源码或文档，不猜。
- **明确目标与决策**：需求或验收条件不明确时先澄清；架构选择、假设和关键取舍要说明。
- **保持简单**：只实现当前需求。复用已有模式；不为单一用例过早抽象、配置化或引入依赖。
- **精准修改**：只改与任务直接相关的代码，贴合既有风格；删掉本次修改产生的废弃代码，不重格式化无关内容。
- **完整迁移**：变更接口或行为时更新所有调用方、测试和文档；不保留无需求的兼容层。
- **按根因修复**：先复现并读完整错误信息；一次处理一个原因，不用吞异常或特判掩盖问题。
- **验证行为**：按影响范围运行相关检查；测试可观察行为、边界和错误路径，不测试实现细节。无法测试时说明原因并做可行的烟雾验证。
- **审慎依赖**：优先现有依赖和标准库；新增依赖前确认必要性、维护状态和成本，并说明理由。
- **清楚沟通**：说明做了什么、为什么、验证结果和已知风险；对不确定性给出具体事实，提交信息描述实际改动。
