# 网易缺少 UIDNEXT 时的同步处理调研

日期：2026-11-04（Asia/Shanghai）。范围：当前 mcp-email-server 1.14.0、应用适配层与已配置的 163 专用测试账号。本文保留调研依据与方案；后续已按用户确认实现第一阶段的清单核对、缓存复用、有限重试与诊断展示，具体验证范围见[实现验收](../verification/2026-11-04-uid-inventory-sync.md)。

## 结论

缺少 UIDNEXT 不等于无法可靠同步。该账号有真实 UIDVALIDITY，也能列出当前邮件的 UID，因此建议保留现有 MCP，增加“UID 清单核对”策略。UIDNEXT 是服务端的分配进度信息，可以用于优化；不能将其缺失一概视为观察范围不完整。

必须同时修复完整性判断和读取后的复核，不能只删除警告或把 `max(uid)+1` 伪装成服务端 UIDNEXT。需要确认的是本轮观察到的邮件集合及配置范围，不承诺捕获两次轮询之间到达后又被删除的邮件。

## 真实只读验证

通过当前应用容器、已保存的凭据和启用证书校验的标准库 IMAP 连接检查；使用客户端 ID 握手与 EXAMINE，只读取能力、状态及编号，没有下载正文、发送邮件或更改邮件标记。

| 项目 | 实际结果 |
|---|---|
| 只读打开 INBOX | OK，5 封 |
| UIDVALIDITY | 1 |
| UIDNEXT | EXAMINE 未返回；STATUS 也未返回 |
| STATUS | `"INBOX" (MESSAGES 5 UIDVALIDITY 1)` |
| UID SEARCH ALL | OK，返回 5 个 UID |
| UID FETCH 1:* (UID) | 连续两次 OK，返回同一组 5 个 UID |
| 登录并发送 ID 后重新查询 CAPABILITY | 未声明 IDLE、CONDSTORE、QRESYNC；声明 UIDPLUS |

编号为 `1793772927` 至 `1793772931`。本次 SEARCH 正常；不据此断言其他网易账号或时间的 SEARCH 都正常。清单两次相同只验证了当前静态邮箱，不代替并发增删、崩溃恢复和大邮箱验收。

## 协议与现成组件依据

1. [RFC 3501 §2.3.1.1](https://www.rfc-editor.org/rfc/rfc3501.html#section-2.3.1.1) 规定，UID 在同一 UIDVALIDITY 内递增，删除后不能复用；UIDNEXT 不保证某封邮件实际使用该编号。因此应以账号、文件夹、UIDVALIDITY、UID 作为缓存位置身份。UIDNEXT 未返回时仍保留空值。
2. [RFC 4549 §4.3.1](https://www.rfc-editor.org/rfc/rfc4549.html#section-4.3.1) 描述按已见 UID 拉取新邮件、核对旧邮件的同步方法。UID 不要求连续，不能按编号差值推算邮件数量。正文可按需要分组下载，无需每次重读整个邮箱。
3. 当前已安装的 MCP 1.14.0 已在 `_snapshot_uid_list()` 中对缺失 UIDNEXT 的情况使用 `_fetch_uid_inventory()`，读取 `UID FETCH 1:* (UID)`。上游[实现](https://github.com/Wh1isper/mcp-email-server/blob/main/mcp_email_server/emails/classic.py)也明确保留缺失字段，并避免把最大 UID 加一当作服务端计数器。这说明优先修复应用接入条件即可，无需为此更换 MCP。上游 main 是调研时的参照，部署判断以本地锁定包为准。
4. [RFC 7162](https://www.rfc-editor.org/rfc/rfc7162.html) 的 CONDSTORE／QRESYNC 可用于更高效地同步，但需服务端声明支持；本账号本次未声明，当前方案不依赖它们。

## 当前应用的三个限制

- `backend/src/mail_assistant/infrastructure/mcp_server.py` 的 `snapshot_payload()` 在 UIDNEXT 为空时无条件增加警告，同时拒绝 `membership_complete=true`。因此已有完整清单也不能确认覆盖。
- `backend/src/mail_assistant/infrastructure/mail_provider.py` 的 `verify_snapshot()` 只对比 UIDVALIDITY、UIDNEXT、邮件数量。没有 UIDNEXT 时，“删一封、添一封”可能保持数量不变，不能用这种比较替代 UID 集合核对。
- `backend/src/mail_assistant/application/mailboxes.py` 的 `apply_observation()` 再次要求 UIDNEXT 存在，导致完整清单无法将已不在当前文件夹的旧缓存标记为历史。

现有 1,000 行元数据窗口和 10,000 候选 UID 限制也必须保留。仅观察最近 1,000 封不能代表已经覆盖整个大邮箱。

## 建议的实现顺序

### 第一阶段：让当前网易账号正确同步

1. 获取带真实 UIDVALIDITY 的完整 UID 清单 A；确认命令成功、编号唯一、数量与观察状态吻合，且没有触及窗口或响应限制。空邮箱要显式处理，不使用最大 UID。
2. 保持现有配置范围过滤和正文读取。第一次读取配置范围内的邮件；之后可按当前规则同步，后续再优化重复下载。
3. 正文读取完成后重新获取完整清单 B，要求两次 UIDVALIDITY 相同、A 与 B 的集合相同。只比较数量不合格。第二次可以通过 MCP 已有的公开 `mailbox_snapshot()` 能力取得；复用同 UIDVALIDITY 的元数据，避免访问上游私有数据库或依赖私有协议函数。
4. 快照 A／B 及配置版本全部核对通过后，在同一业务事务内记录观察证据、更新缓存状态并提交同步进度。清单改变则保留已取得的数据为待确认，有限重试，不推进完整水位。
5. UIDNEXT 保持空值，另存策略 `uid_inventory`、UIDVALIDITY、清单摘要与数量、观察时间及配置范围。`membership_complete` 的依据改为已验证清单；`coverage_state` 仍需满足范围内收件时间、正文读取和响应完整性条件。

最小落地方案允许第二次快照带来额外元数据请求；这是当前小型测试邮箱可接受的取舍。后续若需要提速，再扩展公开的轻量 UID 清单接口。

### 第二阶段：减少重复下载与扩大邮箱规模

- 在应用自己的检查点中保存 UID 清单或压缩区间，以差集识别新增与移出邮件；正文缓存按账号、文件夹、UIDVALIDITY、UID 去重。清单核对是全量编号扫描，正文读取是按需增量，二者分开。
- UIDVALIDITY 改变时废弃旧的 UID 位置映射并重新核对；保留原摘要与历史缓存。Message-ID 可辅助关联，不能独自作为一定唯一的身份。
- 继续区分“完整观察了文件夹成员”和“完整读取了配置日期范围内的正文”。通过 INTERNALDATE 过滤摘要时间，不把 Date 头作为同步进度；编号发现与业务时间范围分别管理。
- 大邮箱采用有界批次、可恢复进度和完整扫描结束标记。完整扫描结束前只能报告部分覆盖，不能凭提高窗口参数就宣称全部成功。
- IDLE 仅在声明支持时用于唤醒同步，唤醒后仍需核对；定时轮询作为基础路径。

## 状态展示

成功走清单策略：显示“已同步，使用兼容同步方式”，将“服务端未提供 UIDNEXT，已通过邮件清单核对”放在诊断详情中，不作为连接异常。

读取中发生变化：显示“邮件已读取，正在重新核对同步进度”。达到窗口／批次限制：显示“已同步部分邮件，完整范围仍在扫描”。缺失 UIDVALIDITY 或无法取得完整清单：保留范围尚未确认的提示，不复用旧 UID 映射。

## 必需验收

| 场景 | 预期 |
|---|---|
| 当前 5 封，UIDNEXT 缺失、UIDVALIDITY 正常 | 两次清单一致及正文读取完整后，可以确认本轮配置范围 |
| 正常提供 UIDNEXT 的服务商 | 现有同步路径保持可用 |
| UID 有删除间隙 | 不推算连续编号；按实际清单处理 |
| 同步期间删除一封并新增一封，数量不变 | 清单差异触发重新核对，不能误报完整 |
| 空邮箱 | 正常完成，无虚构 UID 或计数器 |
| UIDVALIDITY 改变／缺失 | 重建映射或降级，不复用旧编号 |
| 超过窗口、重复 UID、响应截断或部分 FETCH 失败 | 保留部分结果，不推进完整进度，不据此推断删除 |
| 正文已缓存但检查点提交前进程退出 | 重试去重，进度与观察状态不出现半提交 |
| 配置范围外的旧邮件、保留旧 INTERNALDATE 的新放入邮件 | 分别核对成员与业务读取范围，不混淆时间覆盖 |

真实邮箱的并发增删与强制终止恢复仍需单独验收；对应边界已通过模拟清单变化和事务中断检查。超过快照上限的大邮箱分批扫描属于第二阶段，不计入本次实现范围。
