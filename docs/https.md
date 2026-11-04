# 使用 HTTPS 部署

使用 `compose.yaml` 加 `compose.https.yaml` 启动 Caddy、API 和 Worker。此模式移除 API 的主机端口，只由 Caddy 提供网页与接口。需要 Docker Compose 2.24.4 或更新版本（包括后续主版本）。

## 公网域名

准备一个已解析到服务器的域名，允许服务器接收 80／443 端口连接，并将下例的 `mail.example.com` 替换为自己的域名：

```bash
MAIL_ASSISTANT_DOMAIN=mail.example.com docker compose \
  -f compose.yaml -f compose.https.yaml config --quiet

MAIL_ASSISTANT_DOMAIN=mail.example.com docker compose \
  -f compose.yaml -f compose.https.yaml build

MAIL_ASSISTANT_DOMAIN=mail.example.com docker compose \
  -f compose.yaml -f compose.https.yaml up --wait --wait-timeout 180
```

打开 `https://mail.example.com`，按正常向导初始化。首次凭证仍通过 `docker compose exec api mail-assistant bootstrap-token` 查看；读取凭证不会修改连接或发送消息。

本方案使用 Caddy 自动申请／续期证书，并将 HTTP 重定向到 HTTPS。`caddy_data` 保存证书状态，`caddy_config` 保存运行配置；普通升级和 `down` 保留这些卷。其配置依据 [Caddy 自动 HTTPS 文档](https://caddyserver.com/docs/automatic-https)。域名解析、公网可达性及公网 CA 申请结果需要在实际服务器确认。

每次运行相关 Compose 命令时使用相同域名和两份配置；也可将 `MAIL_ASSISTANT_DOMAIN` 写入自己维护的部署环境文件，通过 `docker compose --env-file /path/to/https.env ...` 读取。应用镜像仍可使用 `MAIL_ASSISTANT_IMAGE` 指定固定标签或摘要，见[维护说明](maintenance.md)。

## 代理、登录与任务进度

覆盖文件取消 API 的主机端口后，在项目内网接收 Caddy 请求，并让 Uvicorn 识别转发后的 HTTPS scheme。登录会话据此带 `Secure` 标记；Caddy 保留原始 Host，页面同源检查与接口地址保持一致。

`MAIL_ASSISTANT_FORWARDED_ALLOW_IPS=*` 仅用于上述 API 不发布端口、代理覆盖转发头的内网部署。单独运行 API 默认信任 `127.0.0.1`；其他代理拓扑应明确设置代理来源地址。不要只复制该通配配置而另外公开 API 端口。

任务事件使用 `text/event-stream`。Caddy 对此类型即时刷新响应，配置中不另设正文缓冲或短流超时，依据 [reverse_proxy 文档](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#streaming)。验证脚本会在 Worker 停止、任务尚未结束时读取首个 SSE 事件，检查是否及时到达。

## 本地证书验证

自动检查使用独立的 `localhost` 部署，由 Caddy 的本地 CA 签发证书。验证客户端只在本次检查中加载该公开根证书，并检查主机名；不安装到操作系统信任库，也不使用忽略证书校验的方式。此结果能验证本地 TLS、登录和代理链路，不能代替公网 CA 申请验收。

已构建应用镜像并启动 Docker 后，可运行：

```bash
.venv/bin/python deploy/verify_containers.py --https \
  --report runtime/deployment/container-checks.json
```

脚本创建随机命名的检查项目、独立卷和本机临时端口，结束后删除这些检查容器和卷；不操作现有项目。它使用无效测试邮箱、回环地址和规则样例，不连接真实邮箱、模型或飞书。

## 维护

对 HTTPS 项目进行维护时，停止与启动命令也应带两份 Compose 配置和相同域名。按[维护说明](maintenance.md)停止 API／Worker后备份业务卷；证书卷由 Caddy 独立维护。迁移服务器时可单独保留 Caddy 状态，或在新服务器重新申请证书。恢复业务备份不会自动复制公网 DNS、证书卷或外部认证。
