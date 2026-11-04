# 来信 · 多邮箱智能工作台

面向个人自托管的智能体课程项目：通过中文网页配置多个 QQ／网易邮箱，使用 Agently 分析邮件、生成三级优先级摘要并支持带来源的多轮查询；飞书提供可选的推送与对话入口。

当前工程版已有网页、持久任务、邮件 MCP 适配器、飞书引导与投递、旧配置导入、离线备份恢复。本地样例与专项测试已通过；真实邮箱、模型、飞书和容器环境仍待验收。状态和交付门槛见[主计划 v2.2](docs/plans/engineering-upgrade-plan.md)，详细证据见[本地验证记录](docs/verification/2026-11-03-local-checks.md)。

## 本地体验

需要 Python 3.11、uv、Node.js 22、pnpm 11.25.0。仓库根目录执行：

```bash
uv sync --all-packages --group dev --frozen
cd apps/web
pnpm install --frozen-lockfile
pnpm build
cd ../..
.venv/bin/python -m mail_assistant demo
```

打开 `http://127.0.0.1:8765`，点击“进入演示工作台”。样例模式运行实际数据库、任务、Worker、TriggerFlow 和网页；邮箱与模型使用明确标识的模拟数据，不访问真实服务。可以生成摘要、打开邮件来源，再问“找导师的会议通知”和“后来有更新吗”。

## 配置真实服务

```bash
.venv/bin/python -m mail_assistant run
```

在另一个终端读取首次初始化凭证：

```bash
.venv/bin/python -m mail_assistant bootstrap-token
```

在网页设置管理密码，然后按向导配置模型、邮箱、摘要计划和可选飞书。模型须支持结构化输出与工具调用；邮箱填写开启 IMAP 后取得的客户端授权码。飞书按网页完成授权、权限／事件发布、本人配对和机器人测试。

初次缓存默认回溯 30 天，首次摘要覆盖最近 24 小时；默认每日 09:00、Asia/Shanghai，每 5 分钟后台同步。摘要和同步有独立开关，以网页保存的配置为准。关闭网页不停止后台任务；运行设备和 API／Worker 需保持在线。

## 功能与工程结构

网页包含概览、邮件、摘要、智能查询、连接与设置、任务记录六个入口。摘要保存事项、行动、截止时间、优先级和邮件依据；查询通过搜索、读正文、查往来三个应用级 Actions 取得依据。未知时间、部分同步和截断会明确显示。摘要生成与投递分别记录；重试沿用已保存摘要和分段回执，结果不确定时提供核对后的明确重试。

邮箱支持发现并选择文件夹，邮件区分最近观察到、历史缓存与尚未确认，详情可跳转相关往来。停用和移除连接分别操作，移除仍保留旧摘要依据。具体规则见[邮箱行为说明](docs/mailboxes.md)。

| 目录 | 职责 |
|---|---|
| `apps/web/` | React／TypeScript／Vite 中文工作台与浏览器检查 |
| `backend/src/mail_assistant/api/` | FastAPI、登录、配置与任务进度接口 |
| `backend/src/mail_assistant/application/` | 账号、同步、查询、摘要、飞书与导入服务 |
| `backend/src/mail_assistant/agents/` | Agently 调用、工具及提示词 |
| `backend/src/mail_assistant/infrastructure/` | MCP／CLI、SQLite、凭据与维护适配 |
| `backend/src/mail_assistant/migrations/` | Alembic 数据库迁移 |
| `deploy/`、`compose.yaml` | API／Worker 部署及健康检查 |
| `mail_agent/` | 保留的 CLI 原型与诊断参考 |

邮箱接入优先复用锁定版本的邮件 MCP，由 IMAP 连接邮箱；应用负责统一配置、跨账号聚合、业务缓存与调度。选型依据和兼容性限制见[邮件 MCP 调研](docs/research/email-mcp-survey.md)。本期为单用户只读使用，不包含自动回复／删除邮件、附件分析、个性化规则或安全研究专题。

## 部署与维护

Compose 使用相同镜像启动 API、Worker，共享持久卷，API 同源提供前端。默认只绑定本机 `127.0.0.1:8765`。已在 Linux arm64 容器完成构建、启动、导入、新卷恢复和本地 CA HTTPS 检查，见[部署验证记录](docs/verification/2026-11-04-deployment.md)；真实服务和公网部署仍待验收。

| 文档 | 内容 |
|---|---|
| [开发说明](docs/development.md) | 安装、开发、配置项与检查命令 |
| [部署说明](docs/deployment.md) | Compose、初始化、状态与停止 |
| [HTTPS 部署](docs/https.md) | 域名、Caddy、会话与任务事件 |
| [维护说明](docs/maintenance.md) | 旧 TOML／.env 导入、一致性备份、新目录恢复及升级回退 |
| [飞书指南](docs/feishu.md) | 应用授权、配对、测试与投递恢复 |
| [CLI 原型说明](docs/legacy-cli.md) | 原 `python3 -m mail_agent` 使用方法 |
| [课程演示](docs/demo.md) | 网页演示顺序、真实联调数据及交付清单 |

导入、备份和恢复前停止 API／Worker；维护锁会拒绝使用中的目录。恢复保留账号来源、应用密钥、任务和回执，并重建 MCP 目录引用。系统钥匙串等外部凭据的恢复限制会列入报告。

## 验证

```bash
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests -q
.venv/bin/ruff check --config backend/pyproject.toml backend deploy
.venv/bin/ruff format --config backend/pyproject.toml --check backend deploy
python3 -m unittest discover -s tests -v
```

前端构建和 Playwright 命令见开发说明。测试覆盖业务及适配契约，飞书使用可执行 CLI 替身，模型和邮箱内容使用隔离样例。真实服务联调、容器新卷恢复和连续运行记录单独验收。
