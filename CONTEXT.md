# 报销看板领域术语表

## 核心概念

- **报销条目（Item）**：学生提交的一条报销记录。字段：明细 `title`、类别 `category`、实付款 `actual_amount`、发票金额 `invoice_amount`、发票号码 `invoice_no`、支付订单号 `order_no`、票据 `invoice_file`、支付截图 `payment_screenshot`、状态 `status`。
- **付款人（owner）**：条目归属人，即学生；账号由管理员在后台开通，真实姓名存 `user.first_name`（建号时填写），展示时用 `get_full_name()` 回退 `username`。
- **类别（Category）**：报销大类（信息服务费/竞赛报名费/书籍），`order` 决定看板与导出中的排列顺序。
- **实付款（actual_amount）**：学生实际支付、作为报销基准的金额。必填。
- **发票金额（invoice_amount）**：发票票面金额，可为空（无票/未填）。
- **审核状态（status）**：`pending` 待审核 / `approved` 已通过 / `rejected` 已退回。**Excel 与 zip 导出只含 `approved`**。
- **小记 / 合计**：导出 Excel 中每类别的实付款小计与全部总计，格式对照人工维护的报销汇总表（全局连续序号，A 列标签、D 列 SUM 公式）。
- **自动预填（DeepSeek 识图）**：上传发票图片后经 DeepSeek 识图自动填充发票金额/发票号码，并取回购买方抬头/税号供校验比对；凭据未配置或识别失败时该功能静默降级为手填。
- **自动校验（check_item）**：保存时对条目跑的三条警告规则（实付>发票金额、截图深色模式、抬头/税号与期望不符），只警告不阻断。

## 用词约定

- 说"条目"不说"发票记录"；"票据"专指发票文件（invoice_file），"支付截图"是另一个文件字段。
- "实付款"永远指报销基准金额，与"发票金额"严格区分。
- 付款人=用户，不单独建模；不要引入 Person/Student 模型。
