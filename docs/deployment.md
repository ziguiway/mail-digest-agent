# 自托管部署

已在 Colima 的 Linux arm64 环境完成实际镜像构建、API／Worker 启动、配置导入、新卷恢复、重启及本地 CA HTTPS 验证，见[部署验证记录](verification/2026-11-04-deployment.md)。一个网易 163 测试账号已通过真实登录与收信，缺少 UIDNEXT 时已通过[清单核对兼容同步验收](verification/2026-11-04-uid-inventory-sync.md)。一个 OpenRouter 模型已通过真实结构化输出和测试工具调用，见[模型联调记录](verification/2026-11-04-model-connection.md)。其他邮箱、邮件模型业务效果、飞书、公网证书、持续运行和远端 CI 仍待分别验收。

## Compose 入口

在安装 Docker Engine／Desktop 和 Compose v2 的机器，仓库根目录执行：

```bash
docker compose config --quiet
docker compose build
docker compose up --wait --wait-timeout 120
docker compose ps
```

浏览器打开 `http://127.0.0.1:8765`。API 与 Worker 使用同一镜像，以 UID 10001 运行，共享 `workspace` 持久卷；API 提供网页和接口，Worker 执行调度、任务与邮件 MCP。镜像包含 Python 后端、编译后的前端、邮件 MCP、Node 与固定版本的飞书 CLI。

首次初始化：

```bash
docker compose exec api mail-assistant bootstrap-token
```

将输出的一次性凭证填写到网页，设置管理密码，然后完成模型和邮箱配置。管理凭证只在该显式命令输出；普通日志只显示凭证文件位置。

## 状态与停止

```bash
docker compose logs --tail 100 api worker
docker compose restart
docker compose up --wait --wait-timeout 90
docker compose stop
```

API 健康检查访问 `/api/v1/health`；Worker 检查业务库中最近的心跳。两项分开检查，避免 API 正常但后台已停止的情况被掩盖。邮件账号和模型测试仍需要在网页单独通过。

`docker compose down` 保留命名卷；`docker compose down --volumes` 会删除项目持久数据，仅用于明确要清空数据的环境。重新构建镜像不会代替数据迁移与恢复验证。

默认端口只绑定主机回环地址。服务器远程访问可以先使用 SSH 转发：`ssh -L 8765:127.0.0.1:8765 用户@服务器`。导入、备份、恢复和升级回退操作见[维护说明](maintenance.md)，包括不受容器 UID 文件权限影响的归档流传输方式。

HTTPS 使用附加的 `compose.https.yaml`，配置见[HTTPS 部署](https.md)。API 端口会被移除，由 Caddy 提供同源网页和接口。

`MAIL_ASSISTANT_HTTP_PORT` 可调整本机 HTTP 端口，`MAIL_ASSISTANT_IMAGE` 可选择固定镜像标签／摘要。独立样例部署可设置 `MAIL_ASSISTANT_MODE=demo`，此时 API 和 Worker 仍分别运行；使用独立项目名和卷，不与真实数据共用。

## 容器无法解析邮箱域名

API／Worker 健康不代表可以访问外部邮箱。若容器内 `socket.getaddrinfo("imap.163.com", 993)` 报 `Temporary failure in name resolution`，先检查 Docker 主机或 Colima 的 DNS。2026-11-04 本地联调曾遇到 Colima `/etc/resolv.conf` 指向不存在的文件，导致容器没有上游 DNS；授权码和 TLS 证书均正常。

可使用项目内的可选覆盖文件，为 API 和 Worker 指定当前网络提供、且能从容器访问的 DNS 服务器。将地址写入自己的部署环境文件，例如 `MAIL_ASSISTANT_DNS=你的网络DNS地址`，然后在原部署命令中追加 `-f compose.dns.yaml`：

```bash
docker compose --env-file runtime/deployment/live.env \
  -f compose.yaml -f compose.dns.yaml -p mail-assistant-local-live \
  up -d --wait --wait-timeout 100
```

该命令保留原持久卷。后续更新也须带上相同的环境文件、覆盖文件和项目名；否则可能移除 DNS 覆盖或访问另一套数据。网络切换后需重新确认 DNS 地址。不要将邮箱服务器 IP 固定到 hosts，也不要关闭 TLS 证书校验。已修好 Docker 主机 DNS 的环境无需使用此覆盖文件。

## 待完成的部署验收

1. 完成网页真实服务配置闭环，关闭浏览器后确认任务继续执行，并补充至少 24 小时运行记录。
2. 将已通过的容器新卷恢复扩展到真实邮箱／飞书认证后端；当前证明的是测试配置、MCP 发现、应用密钥、样例邮件及会话恢复。
3. 在实际域名服务器验证公网 CA 签发，并演练已发布旧版本镜像的升级回退。
4. 在远端实际运行 CI；已有配置和本地脚本通过不计为远端通过。
