# ADR-0002: 媒体文件经 Django 鉴权下发，不走 nginx 静态路由

日期：2026-09-28　状态：已接受

## 背景

条目的发票文件与支付截图含个人支付信息，存于 `MEDIA_ROOT`。nginx 若直接 `alias /media/`，任何未登录者拿到 URL 即可下载。

## 决策

- nginx 只直供 `/static/`（collectstatic 产物），不配置 `/media/`。
- 看板上的文件访问走 `core.views.file_serve`（`/items/<pk>/file/<kind>/`）：`login_required` + `FileResponse` 流式返回，kind ∈ invoice|payment。
- 生产环境额外挂 `re_path(r"^media/(?P<path>.*)$")` 的登录保护 serve（admin 里文件链接可用且仍需登录）。
- 导出 Excel 中「支付截图/票据」单元格的超链接指向 `file_serve` 路由。

## 后果

- 文件下载经 Django，无 nginx sendfile 加速——个人工具流量下可忽略。
- 本地与生产行为一致（同一鉴权路径）。
