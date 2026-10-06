# ADR-0009: 导出下载走独立子域，反代同一 Django

日期：2026-10-01　状态：已接受

## 背景

导出 zip（发票原文件打包）与页面交互共用 invoice.cislunarspace.cn。希望把“文件下载”类流量分离到 download.cislunarspace.cn。ADR-0002 已确立媒体必须经 Django 鉴权，download 域不能绕过它。

## 决策

- download.cislunarspace.cn 由 nginx 新增 server 块反代同一 gunicorn（127.0.0.1:8100），不配静态直发。鉴权与主域完全一致（ADR-0002 不受影响）。
- 承载范围仅“下载类”：看板导出 Excel/zip 按钮，以及 Excel 内附件超链接（`build_absolute_uri` 跟随请求域，自动落在 download 域）。侧边栏 iframe 预览留主域，`X_FRAME_OPTIONS=SAMEORIGIN` 拒绝跨域内嵌，不为子域放宽安全头。
- 按钮域名由 `DJANGO_DOWNLOAD_ORIGIN` 注入（`settings.DOWNLOAD_ORIGIN`），空值回退相对链接，本地开发零配置。
- `DJANGO_COOKIE_DOMAIN=.cislunarspace.cn` 把 session/CSRF cookie 挂到父域，两子域共享登录态。未配置维持 Django 单域默认。
- 证书用 acme.sh http-01 为 download 域单独签发。

## 后果

- 已登录用户需重新登录一次：旧 session cookie 绑定主域，新 cookie 才同时覆盖两子域。
- 未配置 `DJANGO_DOWNLOAD_ORIGIN` 的环境（本地、测试）行为不变。
- download 域上的登录跳转页可正常使用（/static/ 同样反代），无功能影响。
