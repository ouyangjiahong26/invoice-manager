# 报销看板（invoice-manager）

![CI](https://github.com/ouyangjiahong26/invoice-manager/actions/workflows/ci.yml/badge.svg)
![release](https://img.shields.io/github/v/release/ouyangjiahong26/invoice-manager)
![license](https://img.shields.io/badge/license-MIT-blue.svg)
![python](https://img.shields.io/badge/python-3.12-blue.svg)
![django](https://img.shields.io/badge/Django-5.2-green.svg)

学生登录后自助提交报销条目（发票 + 支付截图 + 订单号 + 实付金额），按类别看板展示；管理员在后台审核，一键导出报销 Excel 与票据 zip。账号仅由管理员开通，开放注册已关闭。

线上环境：[invoice.cislunarspace.cn](https://invoice.cislunarspace.cn)

## 功能

- 条目提交与编辑（每人只能改自己的）
- 看板按类别分组，`?status=` 状态过滤
- 两条自动校验警告（金额、深色截图），只提示不阻断
- 管理员审核 + 导出 Excel（按类别小记合计）与票据 zip
- 媒体文件全部走 Django 鉴权下发，不经 nginx（ADR-0002）

## 界面预览

完整组件预览见 [`docs/preview.html`](docs/preview.html)。

![看板](docs/images/preview-board.png)

![提交表单](docs/images/preview-form.png)

## 快速开始

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
uv run python manage.py migrate
uv run python manage.py runserver   # http://127.0.0.1:8000/
```

## 配置

环境变量均可缺省：

| 变量 | 作用 | 缺省 |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | 签名密钥 | `dev-only-insecure-key` |
| `DEBUG` | 调试模式 | `false` |
| `DJANGO_ALLOWED_HOSTS` | 允许的 Host | `127.0.0.1,localhost` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | 可信 CSRF 来源 | 空 |
| `DATABASE_URL` | 数据库连接串 | sqlite |

## 部署

腾讯云 `ssh jump`，代码在 `/home/ubuntu/baoxiao`：gunicorn `127.0.0.1:8100` + systemd `baoxiao.service` + PostgreSQL + nginx 443 反代（域名 `invoice.cislunarspace.cn`），媒体走 Django 鉴权路由。拓扑细节见 `AGENTS.md`。

```bash
ssh jump "cd /home/ubuntu/baoxiao && git pull && sudo systemctl restart baoxiao"
```

## 账号约定

仅管理员在 `/admin/auth/user/` 添加账号（ADR-0003）：用户名 = 姓名拼音 + 两位年级（如 `ouyangjiahong22`），`first_name` 为真实姓名（导出表付款人），初始密码 `123456`，首登自改。

## 版本与发布

语义化版本，当前 **v1.0.0**。合并到 main 后 `git tag vX.Y.Z && git push origin vX.Y.Z`，Release 工作流自动创建 GitHub Release。

## CI

push main 与 PR 运行 `manage.py check` + `makemigrations --check --dry-run`（防模型与迁移漂移），check 为分支保护必需项。

## 工程约定

仓库规范见 [`AGENTS.md`](AGENTS.md)，术语表 [`CONTEXT.md`](CONTEXT.md)，决策记录 [`docs/adr/`](docs/adr/)，issue/分诊配置 [`docs/agents/`](docs/agents/)。

## License

[MIT](LICENSE)
