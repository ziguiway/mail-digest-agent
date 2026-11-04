# 模型连接

网页需要填写 OpenAI 兼容接口的基础地址、准确模型标识和 API Key。保存后点击“测试连接与能力”，后台分别验证结构化输出与实际执行工具调用；两者通过才显示已验证。

## OpenRouter

- 服务地址：`https://openrouter.ai/api/v1`。
- 模型名称：使用 OpenRouter 提供的准确标识；本次真实通过的名称为 `~z-ai/glm-flash-latest`，保留开头的 `~`。
- API Key：只在网页填写；编辑时留空沿用已保存的密钥。

基础地址应包含 `/api/v1`，不填写 `/chat/completions`。应用保存配置时会将 OpenRouter 的域名首页或 `/api` 自动补为 `/api/v1`，其他服务商的自定义路径保持原样。接口地址依据 [OpenRouter 官方文档](https://openrouter.ai/docs/quickstart)。

## 运行目录与错误提示

Agently 在创建 Agent 时就会建立任务目录。应用将其放在业务数据目录内的 `agently/.agently/task_workspaces/`，各工作空间独立；容器的 `/app` 无需开放写权限。目录创建失败会明确提示检查服务端数据目录权限，测试状态记为失败。

连接测试分别报告密钥／访问权限、额度不足、接口地址或模型不存在、限流、超时及网络故障。错误提示不展示原始模型响应、请求正文或密钥。一次能力测试通过只证明该配置当时能完成测试，不代表所有业务问题或模型输出都正确。

2026-11-04 的真实验证范围见[联调记录](verification/2026-11-04-model-connection.md)。
