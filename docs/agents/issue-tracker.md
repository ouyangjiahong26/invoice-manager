# Issue tracker: GitHub

本仓库的 issue 和规格存放在 GitHub Issues 中。所有操作使用 `gh` CLI。

## 约定

- **创建 issue**：`gh issue create --title "..." --body "..."`。多行正文用 heredoc。
- **AI 贡献标记**：AI 提交的 issue 与 PR 标题以 `[AI Generated][<类型>]` 开头（类型是大写标签，如 `[FIX]`、`[DOCS]`）；AI 写的评论首行用 `> **[AI Generated]** 本评论由 AI 完成。` 或 `> **[AI Assisted]** 本评论由 AI 辅助完成。`
- **读取 issue**：`gh issue view <number> --comments`，用 `jq` 过滤评论，同时获取标签。
- **列出 issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，按需加 `--label` 和 `--state` 过滤。
- **评论 issue**：`gh issue comment <number> --body "..."`
- **添加 / 移除标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭**：`gh issue close <number> --comment "..."`

从 `git remote -v` 推导仓库，`gh` 在 clone 内运行时自动识别。

## Pull request 作为分诊渠道

**PR 作为请求渠道：否。** 个人报销工具，无外部贡献者；PR 仅由维护者（owner）本人使用。

## 当技能说"发布到 issue tracker"时

创建一个 GitHub issue。

## 当技能说"获取相关工单"时

运行 `gh issue view <number> --comments`。

## GitHub Project

**使用 Project：是。**（`/github-project`、`/triage`、`/open-pr`、`/merge-pr` 读取此配置。）

| 项 | 值 |
| --- | --- |
| Owner | `ouyangjiahong26` |
| Project 编号 | `7` |
| Project ID | `PVT_kwHOCpw4xM4Bk5os` |
| Status 字段 ID | `PVTSSF_lAHOCpw4xM4Bk5oszhjoepM` |

Status 选项 ID：

| 选项 | 选项 ID |
| --- | --- |
| `Inbox` | `7155babf` |
| `Backlog` | `6e922ada` |
| `Ready` | `2e0ea360` |
| `In Progress` | `804610fc` |
| `In review` | `d2078914` |
| `Done` | `eececa03` |
| `No action` | `92ca8ca3` |

Project 无 `Priority`、`Start Date` 等其他自定义字段；没记录的字段，技能不写。状态迁移规则见 `/github-project`。
