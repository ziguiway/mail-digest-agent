# 工程版开发与验证

工程版位于 `backend/` 和 `apps/web/`；原型位于 `mail_agent/`，两者使用独立运行命令和数据目录。网页及飞书流程已实现并通过本地验证，真实服务与部署交付仍待验收，范围和状态以[主计划](plans/engineering-upgrade-plan.md)为准。

## 本地启动

需要 Python 3.11、uv、Node.js 22 与 pnpm 11.25.0。在仓库根目录安装：

```bash
uv sync --all-packages --group dev --frozen
cd apps/web
pnpm install --frozen-lockfile
pnpm build
cd ../..
```

无账号体验：

```bash
.venv/bin/python -m mail_assistant demo
```

打开 `http://127.0.0.1:8765`，点击“进入演示工作台”。默认使用 `runtime/demo/`，由独立样例邮箱和规则模型驱动；实际运行数据库、Worker、任务、API、TriggerFlow 与网页。可以查看首份摘要、打开邮件依据，再问“找导师的会议通知”和“后来有更新吗”。不会连接邮箱、模型或飞书。

真实配置开发模式：

```bash
# 同时管理 API 与 Worker，默认使用 runtime/live/。
.venv/bin/python -m mail_assistant run

# 首次初始化时，在另一个终端查看一次性凭证。
.venv/bin/python -m mail_assistant bootstrap-token
```

也可分别启动 `.venv/bin/python -m mail_assistant api` 和 `.venv/bin/python -m mail_assistant worker`。网页的“后台在线”来自 Worker 心跳，仅启动 API 不会执行任务。首次输入初始化凭证设置管理密码，再通过向导配置模型、邮箱和计划。凭证由用户自行填写，不写入代码或测试文件。

旧配置导入、离线备份和新目录恢复见[维护说明](maintenance.md)。维护命令有独占锁，须先停止 API 与 Worker；日常 `doctor`／`bootstrap-token` 使用共享锁，可以与服务并存。`MAIL_ASSISTANT_HOST`／`MAIL_ASSISTANT_PORT` 在未提供命令行参数时生效。

前端热更新：先启动上述后端，在 `apps/web/` 执行 `pnpm dev`，打开 `http://127.0.0.1:5173`。开发代理将 `/api` 转发到 8765。生产构建由 API 同源提供静态页面。

## 检查命令

```bash
# 仓库根目录：旧原型与新后端分别检查。
python3 -m unittest discover -s tests -v
.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests -q
.venv/bin/ruff check --config backend/pyproject.toml backend deploy
.venv/bin/ruff format --config backend/pyproject.toml --check backend deploy

# API 契约修改后重新生成。
.venv/bin/python -m mail_assistant schema --output backend/openapi.json
cd apps/web
pnpm generate:api
pnpm format:check
pnpm build
pnpm exec playwright install chromium
pnpm test:e2e
```

浏览器测试自动在 8771 启动临时样例服务，不复用日常服务或真实数据目录。测试覆盖摘要来源、多轮查询、窄屏导航、会话与向导恢复、非敏感模型草稿恢复，以及主要页面的 WCAG A／AA 自动规则检查。自动规则检查不能代替完整人工可访问性验收。

后端 MCP 契约测试实际运行已安装版本的账号管理 CLI 与 stdio 子进程，使用临时目录、无效测试邮箱和本地地址，不执行 IMAP 登录。同步测试覆盖收件时间与 Date 头分离、超过 1,000 封的覆盖限制、缺少收件时间、分页冻结、UID 变化和部分同步水位。它们不证明真实 QQ／网易或真实模型可用。

飞书说明见[网页绑定与恢复](feishu.md)。后端飞书测试使用可执行 CLI 替身，通过实际子进程管道验证授权链接、二维码文件、配对、消息事件、回复、分段回执和重启恢复；浏览器新增绑定流程与不确定发送的明确重试选择测试。实际 CLI 只进行了离线二维码生成检查，没有创建真实应用或发送真实消息。开发环境可用 `MAIL_ASSISTANT_LARK_COMMAND` 指定已安装 CLI 的完整可执行路径。

## 邮件访问边界

生产 Worker 启动 `mail_assistant.infrastructure.mcp_server`，复用锁定的上游 MCP 并补充两个只读工具：`workspace_mailbox_snapshot` 和 `workspace_mailbox_state`。账号配置仍由上游管理 CLI 维护；扩展通过上游的 `LocalMetadataBackend` 提供者接口读取，不打开其私有数据库表。

扩展保留上游有界快照限制。超过窗口、缺失收件时间或 UID 状态时明确记录部分／未知覆盖，不推进完整同步水位；首次摘要不会把未知收件时间当作最近 24 小时。读取正文后重新核对邮箱状态；变化时保留已缓存邮件并报告重试。此行为仍需在真实邮箱进行契约验收。

当前将完整观察清单与增量正文窗口分开，保存每封邮件的 `observed_state`、UID 代际和最近观察时间；完整稳定清单才允许推断未再观察到的缓存。文件夹发现使用后台 `account_folders` 任务，读取结果绑定账号配置版本。移除连接与停用分别实现，保留来源标识并允许只读打开旧往来。面向用户的具体含义见[邮箱行为说明](mailboxes.md)。

升级上游版本时，需要重跑真实 CLI／stdio 检查、扩展字段与覆盖测试，再进行 QQ／网易联调。禁止仅升级版本号而沿用旧兼容性结论。
