# OpenRouter 模型连接排查（2026-11-04）

## 原因

用户填写 `https://openrouter.ai/api`、模型 `~z-ai/glm-flash-latest` 后，后台 `model_test` 任务失败，错误类别为 `internal_error`，网页仍显示待验证。

实际失败发生在请求之前：Agently 4.1.4.10 在构造 Agent 时尝试建立 `/app/.agently/task_workspaces/…`，运行用户 UID 10001 无权写入 `/app`，触发 `PermissionError`。Agent 构造又在原测试异常处理范围之外，因此状态和提示没有记录具体原因。

此外，OpenRouter 的 OpenAI 兼容基础地址应为 `https://openrouter.ai/api/v1`，见[官方说明](https://openrouter.ai/docs/quickstart)。用户提供的模型名称带 `~`，该别名存在于[官方模型目录](https://openrouter.ai/api/v1/models)，不应删除前缀。

## 验证与修复

- 使用已保存、加密存储的密钥发起不含邮件内容的最小独立请求，正确接口返回 HTTP 200 和模型响应。
- Agent 创建前以独立的继承设置指定业务数据目录内的可写任务父目录。各工作空间独立，不修改 Agently 的全局设置，不向 `/app` 开放写权限。
- Agent 创建及工具配置移入测试异常处理范围。目录权限错误会明确提示并写入模型错误状态。
- 连接测试区分认证／权限、余额、404 接口或模型、429 限流、超时、网络及服务端故障，不将原始响应、请求内容或密钥展示在页面。
- 保存 OpenRouter 域名首页或 `/api` 时自动补成 `/api/v1`，其他服务商的自定义路径保持原样。现有配置已通过应用保存服务修正地址并保留原密钥与模型标识。
- 候选修复在实际容器中通过结构化输出与实际工具执行验证，返回 `structured_output=true`、`tool_calling=true`、`simulated=false`。工具需要接收到指定测试值才算通过。
- 13 项目标检查通过，包括真实 Agently 目录隔离、异常状态写入、服务地址处理、错误分类及模型配置并发修改回归；Ruff 检查通过。

## 部署

镜像 `mail-digest-agent:0.2.0-model-fix`，镜像索引 `sha256:eef30f40b98fbdd9aa2e4b925d096a7070b1ae0d354dc1112cb3f11d827a7366`。使用原 `mail-assistant-local-live` 项目、原持久卷、原 DNS 覆盖文件和 HTTP 8877 入口。

部署后由实际 Worker 执行 `model_test`，任务 `908e6a3c3914471da394413a803b8508` 为 `succeeded`。模型配置 revision 3 状态 `verified`、`error=null`，能力标记为结构化输出与工具调用均可用、`simulated=false`。API／Worker 健康检查通过，原邮箱配置及业务数据保留。

本次只验证连接、结构化结果和测试工具执行；不据此声称真实邮件摘要准确率、三个邮件检索工具或飞书投递均已验收。密钥未写入代码、测试或本文。
