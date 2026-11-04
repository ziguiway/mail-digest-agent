# 网易测试邮箱连接排查（2026-11-04）

## 已验证结果

- 独立标准库脚本通过 SMTP 465 认证，按用户指定的收件人仅发送了一封测试邮件；用户已确认收到。
- 同一授权码通过 IMAP 993 登录、客户端 ID 握手和只读打开 INBOX；服务器返回 5 封邮件。
- 实际应用容器中，已保存账号的后台连接测试成功，状态为 `verified`，`simulated=false`。
- 实际 Worker 同步任务成功读取并缓存 5 封邮件；未在此过程中触发摘要生成或额外 SMTP 发信。

授权码未写入诊断脚本或本记录。诊断脚本为 `deploy/check_mail_connection.py`，默认只检查 IMAP，显式指定 `--send-test --recipient` 才发信。

## 原因与修复

原应用容器中解析 `imap.163.com` 返回 `socket.gaierror: Temporary failure in name resolution`。容器的 `/etc/resolv.conf` 没有上游 DNS；Colima 虚拟机的 `/etc/resolv.conf` 是指向不存在文件的符号链接。故障发生在授权码校验之前。

临时诊断容器指定当前网络的 DNS 后，域名解析和 TLS 校验均通过。修复采用可选 `compose.dns.yaml`，在本机忽略的部署环境文件中设置当前网络 DNS，为 API／Worker 同时应用；未修改 macOS 系统 DNS，未固定邮箱服务器 IP，未关闭证书校验。网络切换后需要重新确认该 DNS 可达。

另外，本机独立 Python 的默认根证书集合为空；独立脚本通过 `--ca-file` 指定已有可信 CA 集合后验证成功。应用镜像本身已包含根证书，应用的故障是 DNS。

原 MCP 1.14.0 连接失败路径有时在 `--json` 下仍输出固定纯文本错误，导致应用显示统一的授权码提示。应用现在识别这些固定错误类别，不将任意 CLI 原始输出展示给用户；连接测试增加 DNS 预检查，分别提示 DNS、网络／TLS、服务器拒绝登录、凭据不可用及超时。

## 部署与检查

- 镜像：`mail-digest-agent:0.2.0-mailbox-fix`。
- 镜像索引：`sha256:b80de8c551a4c98443264be8f84f76959771e4db8e4ac018644f5e661c783e96`。
- 实际项目：`mail-assistant-local-live`；HTTP `http://127.0.0.1:8877`。
- 使用原命名卷，重建 API／Worker 后两者健康，HTTP 健康接口返回 live 模式及 Worker 存活。
- 目标测试与现有真实 CLI 生命周期测试：10 项通过；Ruff 检查通过；Compose 配置检查与镜像构建通过。
- 后台复测任务：`account_test` 和 `sync` 均为 `succeeded`；账号状态 `verified`，缓存数量 5。

本机后续更新需保留原 Docker 环境和 DNS 覆盖文件：

```bash
DOCKER_CONFIG="$PWD/runtime/deployment/docker-cli" \
DOCKER_HOST=unix:///Users/zhengshuang/.colima/mail-assistant/docker.sock \
docker compose --env-file runtime/deployment/live.env \
  -f compose.yaml -f compose.dns.yaml -p mail-assistant-local-live \
  up -d --wait --wait-timeout 100
```

## 当前限制

该账号的实际 STATUS 响应为 `"INBOX" (MESSAGES 5 UIDVALIDITY 1)`，只读选择后也没有 `UIDNEXT`。应用保留 `coverage_state=unknown`、`sync_complete=false` 和覆盖提示，不凭读取数量推断可恢复的完整同步水位。已通过登录及正文读取，不将此限制归因于授权码；后续应单独设计没有 UIDNEXT 时的同步策略与证明条件。

本次不证明 QQ、其他网易账号、模型分析或飞书投递已经通过真实验收。
