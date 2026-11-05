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

摘要采用最近 24 小时的完整范围，分析缓存、内容版本、检查时间与发送记录分别保存。`test_digest_refresh.py` 覆盖连续刷新、新增与同数量替换、滚动边界、并发请求、缓存失效、失败／部分覆盖、定时任务独立性、来源快照和旧数据迁移；`digest-refresh.spec.ts` 覆盖页面对应状态与版本选择。具体行为及当前部署证据见[摘要刷新设计](plans/digest-refresh-redesign.md)与[验证记录](verification/2026-11-04-digest-refresh.md)。

真实查询与模型工具探测通过 `AgentlyIntelligence.bind_actions()` 统一绑定原生 Action，依据函数签名生成工具 Schema，并显式传入兼容提供者的 `request_options.tools`。查询限制四轮，保留实际读取来源校验。`test_query_recovery.py` 包含真实 Agently 与模拟 HTTP 流的协议回归，`query-recovery.spec.ts` 覆盖重试及恢复状态；详细证据见[智能查询修复验收](verification/2026-11-05-query-recovery.md)。

后端 MCP 契约测试实际运行已安装版本的账号管理 CLI 与 stdio 子进程，使用临时目录、无效测试邮箱和本地地址，不执行 IMAP 登录。同步测试覆盖收件时间与 Date 头分离、超过 1,000 封的覆盖限制、缺少收件时间、分页冻结、UID 变化和部分同步水位。它们不证明真实 QQ／网易或真实模型可用。

飞书说明见[网页绑定与恢复](feishu.md)。后端飞书测试使用可执行 CLI 替身，通过实际子进程管道验证授权链接、二维码文件、配对、消息事件、回复、分段回执和重启恢复；浏览器新增绑定流程与不确定发送的明确重试选择测试。实际 CLI 只进行了离线二维码生成检查，没有创建真实应用或发送真实消息。开发环境可用 `MAIL_ASSISTANT_LARK_COMMAND` 指定已安装 CLI 的完整可执行路径。

## 邮件访问边界

生产 Worker 启动 `mail_assistant.infrastructure.mcp_server`，复用锁定的上游 MCP 并补充两个只读工具：`workspace_mailbox_snapshot` 和 `workspace_mailbox_state`。账号配置仍由上游管理 CLI 维护；扩展通过上游的 `LocalMetadataBackend` 提供者接口读取，不打开其私有数据库表。

扩展保留上游有界快照限制。超过窗口、缺失收件时间或 UIDVALIDITY 时明确记录部分／未知覆盖，不推进完整同步水位；首次摘要不会把未知收件时间当作最近 24 小时。缺少 UIDNEXT 时采用完整 UID 清单核对，不将缺失字段伪造成最大 UID 加一。`workspace_mailbox_state` 在正文读取后重新获取快照并返回清单摘要、完整性与 UID 状态；成员集合变化时最多重试三次，未通过核对不推进进度。

正文缓存按账号、文件夹、UIDVALIDITY 和 UID 匹配；进度提交中断后仍可复用已读取的不可变正文。覆盖、观察状态和检查点在同一事务提交，检查点保存同步方式、清单摘要、核对时间及诊断说明。普通成功状态不显示协议字段警告，详情位于设置页折叠的同步诊断中。`test_uid_inventory.py` 覆盖缺失 UIDNEXT、同数量替换、有限重试、有效期变化、部分结果、同 Message-ID 副本与提交失败后的缓存恢复。

当前将完整观察清单与增量正文窗口分开，保存每封邮件的 `observed_state`、UID 代际和最近观察时间；完整稳定清单才允许推断未再观察到的缓存。文件夹发现使用后台 `account_folders` 任务，读取结果绑定账号配置版本。移除连接与停用分别实现，保留来源标识并允许只读打开旧往来。面向用户的具体含义见[邮箱行为说明](mailboxes.md)。

升级上游版本时，需要重跑真实 CLI／stdio 检查、扩展字段与覆盖测试，再进行 QQ／网易联调。禁止仅升级版本号而沿用旧兼容性结论。
