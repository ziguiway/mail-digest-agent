# 来信 · Qoder Sites 演示应用

这是比赛评委体验用的独立全栈版本，和原来的 FastAPI/IMAP/飞书生产服务分开部署。

## 功能

- 展示虚构校园邮件，按优先处理、需要关注和信息通知分类。
- 打开邮件查看摘要、建议下一步和虚构原文。
- 通过同源 Sites Function 将自然语言问题交给 Qoder Cloud Agent，并展示邮件依据。
- 通过 Qoder Cloud Agent 起草邮件回复，支持编辑和复制；没有邮件发送能力。
- 前端明确标示样例数据，不连接用户邮箱，不推送飞书。

## 目录

- `web/`：独立响应式静态前端。
- `functions/index.ts`：Qoder Sites Edge Function 入口。
- `functions/routes.mjs`：健康检查、样例邮件、查询和草稿 API。
- `functions/cloud-agents.mjs`：Qoder Cloud Agent API 客户端及可恢复的资源初始化逻辑。
- `schema.sql`：Agent 与 Environment 初始化锁和资源绑定表。

## 部署架构

浏览器 → `/functions/v1/app/api/*` → Sites Function → Qoder Cloud Agents API。邮箱数据是 Function 中的虚构样例；`QODER_PAT` 由 Qoder Sites 的 Qoder 后端能力在服务端托管，不进入网页包。

首次发布需要给 Site 开通 `database`、`functions`、`qoder` 后端能力，在 Function 专用数据库中应用 `schema.sql`，然后用 `databaseAccess: read_write` 发布 Function。每次查询/生成草稿创建独立的 Cloud Agent Session，预算上限为 0.10 Qoder credits。

## 本地检查

当前仓库的正式后端仍在 `apps/api`，此目录只用于 Qoder Sites。Qoder Sites 会注入 `_qoder/backend.mjs`，该平台模块不放入源码包。纯前端可直接用静态服务器预览；Function 需要 Qoder Sites 运行环境和 `QODER_PAT`。
