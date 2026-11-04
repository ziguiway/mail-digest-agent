# 多来源邮箱接入与现成 MCP 调研

调研日期：2026-11-03（Asia/Shanghai）。目标：为单用户自托管、多邮箱、Agently、网页配置向导和飞书摘要项目选择可复用的邮箱访问组件。

## 结论

优先对 **Wh1isper/mcp-email-server 1.14.0** 做 QQ／网易真实账号验证，通过后将它作为默认邮件访问后端。它已具备多账号管理、IMAP 解析、MCP 工具、网页账号管理、网易 ID 握手和 QQ 非 ASCII 搜索适配。基础协议检查及 Agently 工具注册已经在隔离环境通过。

这一结论是“优先验证复用”，不代表已经验证真实邮箱兼容性。前端向导、跨账号聚合、摘要、后台任务、业务缓存和飞书推送仍由我们的应用实现。MCP 提供工具协议，底层邮箱连接依然依赖 IMAP 等邮件协议。

## 候选对比

| 候选 | 已核实能力 | 与本项目的适配与缺口 | 判断 |
|---|---|---|---|
| [Wh1isper/mcp-email-server](https://github.com/Wh1isper/mcp-email-server) | Python 3.11+；多账号；网页管理；stdio／Streamable HTTP；账号管理 CLI；BSD-3-Clause | 读取工具按 account_name 操作，跨账号汇总需要应用编排；自带 UI 是账号管理，不包含我们的摘要工作台；需要配置只读权限并限制向模型暴露的工具 | 首选验证 |
| [KelpHect/read-email-mcp](https://github.com/KelpHect/read-email-mcp) | Node/TypeScript；只读；SQLite 邮件镜像、全文检索、增量同步、往来查询；MIT | 当前配置对象只包含一个账号；多账号可多进程运行，但还需应用聚合、生命周期管理；仓库并不等于已验证 QQ／网易 | 缓存架构参考或第二候选 |
| [LeeFlannery/imap-mcp](https://github.com/LeeFlannery/imap-mcp) | Python 3.12+；多账号合并查询；只读工具；TOML＋环境变量配置；MIT | 查询时连接 IMAP，未提供完整邮件镜像；配置读取有缓存；当前接入代码未发现显式网易 ID 握手；与原计划 Python 3.11 有版本差异 | 轻量查询备选 |
| [boutquin/mcp-server-email](https://github.com/boutquin/mcp-server-email) | Go；多账号；连接池、重试、OAuth2、MCP 邮件工具；MIT | 账号配置启动时加载，修改后需重启；应用层仍需开发向导和缓存；本次检索未发现明确的网易 ID 适配证据 | OAuth／多服务商扩展参考 |
| [TimeCyber/email-mcp](https://github.com/TimeCyber/email-mcp) | JavaScript；QQ／网易等服务商预设；读取与发送工具；MIT | 当前账号主要来自进程全局环境变量，运行中配置也会修改环境；网易预设默认选 POP3；并非开箱即用的多账号聚合与镜像后端 | 不作为当前首选 |
| [dove-yang/mcp-mail-server-enhanced](https://github.com/dove-yang/mcp-mail-server-enhanced) | TypeScript；显式网易 IMAP ID 补丁；MIT | 主要使用单套账号环境变量；文档中 Multi-Mailbox 指文件夹，不等同于多邮箱账号；package.json 与 README 安装名称存在差别，需按源码核对 | 协议兼容参考 |

表格依据 README、配置结构、邮件连接代码和测试源码；没有依据 star 数推断质量，也没有将通用 IMAP 声明当作服务商实测结果。

## 首选项目的关键证据

核实版本：[PyPI 1.14.0](https://pypi.org/project/mcp-email-server/1.14.0/)，对应 [GitHub 发布记录](https://github.com/Wh1isper/mcp-email-server/releases/tag/1.14.0) 与提交 `cb1191e4fc6d98a86aa35b4d0f1de72ff230a31d`。发布包确实包含网页管理模块。

- 网易适配：[`classic.py` 的 `_send_imap_id`](https://github.com/Wh1isper/mcp-email-server/blob/cb1191e4fc6d98a86aa35b4d0f1de72ff230a31d/mcp_email_server/emails/classic.py#L974) 发送紧凑格式 ID，并处理超时及异常。
- QQ 中文搜索：存在针对 `imap.qq.com` 的非 ASCII 搜索策略和[专门的模拟测试](https://github.com/Wh1isper/mcp-email-server/blob/cb1191e4fc6d98a86aa35b4d0f1de72ff230a31d/tests/test_imap_non_ascii_search.py#L156)。
- 邮件结构：返回 Message-ID；正文结果还返回 In-Reply-To 和 References，可供我们关联往来。见[响应模型](https://github.com/Wh1isper/mcp-email-server/blob/cb1191e4fc6d98a86aa35b4d0f1de72ff230a31d/mcp_email_server/emails/models.py)。
- 网页集成：管理 CLI 支持 JSON 输出、账号增删改、连接测试、配置版本和通过 stdin 写入密码，适合由应用后端封装。见[配置文档](https://github.com/Wh1isper/mcp-email-server/blob/cb1191e4fc6d98a86aa35b4d0f1de72ff230a31d/docs/configuration.md)。
- 只读边界：`allowed_mutations = []` 禁止邮件写操作，但工具目录仍可能包含写工具；应用仍应只暴露读取能力。见[传输与策略文档](https://github.com/Wh1isper/mcp-email-server/blob/cb1191e4fc6d98a86aa35b4d0f1de72ff230a31d/docs/transports.md)。

注意：当前 README 提到 Password & test，但详细文档明确说明 Web UI 不提供服务商连接测试，连接测试入口在管理 CLI。因此不能照搬 README，把“完整配置向导已经具备”当成事实。我们的网页需封装 `account test` 并展示结果。

其 SQLite 元数据投影也不等于完整邮件正文镜像。默认快照保留最近 1,000 个 UID，有明确覆盖条件和资源上限；工具的日期过滤依据服务器 INTERNALDATE，而返回的 date 是邮件 Date 头。应用需要单独保存同步状态、覆盖范围和业务缓存，不能直接把返回 date 当成可靠增量游标。见[工具与缓存说明](https://github.com/Wh1isper/mcp-email-server/blob/cb1191e4fc6d98a86aa35b4d0f1de72ff230a31d/docs/tools.md)。

## 实际完成的检查

所有依赖安装在临时虚拟环境，未改动原型项目的运行依赖。使用 Python 3.11.7，核实组合：

| 组件 | 版本 |
|---|---|
| Agently | 4.1.4.10 |
| mcp-email-server | 1.14.0 |
| FastMCP | 3.4.11 |
| MCP Python SDK | 1.30.0 |

检查结果：

1. 同一环境安装解析成功。
2. 实际启动邮件 MCP stdio 子进程，完成 initialize、tools/list，并调用 list_available_accounts，均成功；账号列表为空，因为没有配置真实邮箱。
3. 实际发现 21 个工具，包括 list_available_accounts、list_emails_metadata、get_emails_content、list_mailboxes，以及多个邮件写工具。
4. 使用 Agently 的 `await agent.async_use_mcp(...)` 成功注册该服务器。
5. 针对源码测试执行 `test_imap_starttls.py` 与 `test_imap_non_ascii_search.py` 中名称匹配 `send_imap_id or qq` 的测试：**16 passed，30 deselected**。这些是模拟测试，不是 QQ／网易在线测试。

发现的接入细节：Agently 基础依赖未自动包含 FastMCP，第一次注册报缺少 `fastmcp>=3`；补装后继续验证。当前 4.1.4.10 的 `use_mcp` 是同步桥接入口，直接 `await agent.use_mcp(...)` 会对返回的 Agent 报 TypeError；显式使用 `async_use_mcp` 可通过。官方 MCP 文档的示例需要结合锁定版本核对，见 [Agently MCP 文档](https://agently.cn/docs/en/actions/mcp.html)。

尚未验证：真实 QQ／网易登录与读取、真实模型选择工具、长期稳定性、大邮箱性能、Docker 下的完整运行与我们的网页交互。

## 对工程方案的调整建议

将邮箱访问抽象为可替换的 MailboxProvider，第一适配器连接上述邮件 MCP。由后台 Worker 管理 MCP 生命周期；单用户验证可使用 stdio，独立服务部署可使用内网 Streamable HTTP。

```text
网页配置向导 ── 应用后端 ── 邮件 MCP 的账号管理接口／CLI
                     │
定时任务 ───────── 邮箱访问适配层 ── 邮件 MCP ── QQ／网易
                     │
                业务缓存与执行记录
                     │
              Agently 分析与多轮查询
                     │
                 网页／飞书
```

- 普通用户只接触我们的配置向导，无需编写 MCP JSON、TOML 或运行终端命令。
- 账号与密码通过受支持的管理接口／CLI 写入 MCP 的账号目录，应用不直接修改上游 SQLite 表结构。应用保存账号引用和业务设置，避免两套密码配置各自生效。
- 跨邮箱请求先确定启用账号，再分别检索并保留来源后合并。支持多账号不等于每个工具都自动跨账号查询。
- 日常同步由 Worker 确定性执行，摘要水位、发送记录和失败恢复属于应用层。模型负责分析与查询编排，不承担判断所有后台同步是否应执行的职责。
- 模型侧只提供本项目需要的读取、检索与往来关联能力。上游没有现成的全套工作台，也不直接提供我们的摘要优先级与飞书闭环。
- 本地缓存统一查询可继续复用已有逻辑；跨邮件往来使用上游返回的引用头。需要完整增量标识或覆盖信息时应补充轻量适配，而不是假设现有 MCP 响应已经暴露全部内部状态。

## 下一步最小验证

在决定替换默认访问层前，用专门的 QQ、网易测试账号完成：

1. 同时配置两个账号并核对账号发现、分页和来源标识。
2. 读取中文主题、HTML、GB 系编码与带往来引用头的样例邮件。
3. 对比读取前后的已读状态。
4. 使用中文主题／发件人搜索及精确时间范围检索。
5. 核对两个邮箱中的会议更新，确保关联依据来自引用头及正文。
6. 模拟单账号失效、授权码更新、MCP 重启、超过元数据窗口和查询超时。
7. 验证新增邮件、删除／移动和 UID 状态变化后不会把缓存误当成完整覆盖。
8. 最后连接真实模型，验证搜索、正文读取、来源引用与多轮追问。

上述结果通过后，将 MCP 适配器设为默认；未通过时保留现有 IMAP 实现作为备选，不提前删除已验证的行为测试。

## 其他路线与安装名称核对

[EmailEngine](https://github.com/postalsys/emailengine/blob/master/README.md) 提供多账号 REST API、Webhook 与账号接入流程，工程功能更广，但当前官方明确为商业许可、含 14 天试用，并依赖 Redis。它可作为后续商业化基础设施候选，不作为这次课程项目的免费默认依赖。

PyPI 的 `imap-mcp 0.3.0` 实际指向 [bcrisp4/imap-mcp](https://github.com/bcrisp4/imap-mcp)，需要 Python 3.14；它不是上表的 LeeFlannery 仓库。安装时必须核对仓库、发行包、Python 要求和准确版本，不能仅依据同名包安装。

## 源码快照

以下为本次从 GitHub 源码归档中读取的提交标识，便于复查本次判断：

| 仓库 | 提交 |
|---|---|
| Wh1isper/mcp-email-server | `cb1191e4fc6d98a86aa35b4d0f1de72ff230a31d` |
| KelpHect/read-email-mcp | `f1ed57603a40027d2ebc2a96b5500520277d8a16` |
| LeeFlannery/imap-mcp | `51929b3c3ce19b8f5167965ac70eb43b04fa175c` |
| boutquin/mcp-server-email | `0ba695c9126fca2b89959620729f5d2e65690d75` |
| TimeCyber/email-mcp | `e41e149936fda3f3f5119880842e698d1dc3ea71` |
| dove-yang/mcp-mail-server-enhanced | `3847aa43832b942ec49c9b94cfa280f3abb3d639` |
