# UID 清单兼容同步验收

日期：2026-11-04（Asia/Shanghai）。已按用户确认实现同步前清单 A、配置范围内的正文读取／缓存复用、同步后清单 B 核对及进度提交。

## 实现

- 保留真实 UIDVALIDITY 与可空的 UIDNEXT；完整性不再强制要求 UIDNEXT 存在。
- MCP 快照返回规范化 UID 清单的 SHA-256 摘要与清单完整性。正文读取后的状态工具重新获取快照，比较集合摘要、有效期、数量及完整性，能识别同数量的成员替换。
- 元数据可在相同真实 UIDVALIDITY 内复用，但每次重新取得服务端编号清单；未访问上游私有数据库。
- 正文按账号、文件夹、UIDVALIDITY、UID 匹配缓存，并检查 Message-ID 冲突。同 Message-ID 的不同 UID 副本分别缓存。
- 清单变化或正文批次不完整最多重新核对三次；失败保留原同步进度。正文缓存可以先写入为待确认，完整观察、覆盖范围和检查点在核对成功后的同一事务内提交。
- 兼容模式回看配置日期范围，可以发现范围内保留旧 INTERNALDATE 的新放入邮件，已缓存正文无需重新下载。
- 设置页和工作台成功时显示“已同步，使用兼容同步方式”。UIDNEXT 缺失说明放在默认折叠的“同步诊断”中，不再作为错误。
- API 增加同步方式与诊断字段，OpenAPI 与前端类型已重新生成；不需要数据库结构迁移。

## 自动检查

| 检查 | 结果 |
|---|---|
| 完整后端回归 | 93 passed |
| 原型单元检查 | 19 passed |
| Chromium 浏览器流程 | 13 passed |
| Ruff 检查与格式 | 通过 |
| 前端 Prettier | 通过 |
| TypeScript／Vite 生产构建 | 通过 |
| OpenAPI 重新生成比对 | 一致 |

新增检查覆盖缺少 UIDNEXT、缺少 UIDVALIDITY、空清单、重复 UID、同数量替换、成功后缓存复用、旧 INTERNALDATE 的新增编号、有效期改变、部分结果、重试上限、同 Message-ID 副本，以及检查点提交失败后的缓存恢复。原来要求重读正文的测试明确清除缓存资格，继续验证真正缺失正文时不能推进进度。

本机 iCloud 文件占位导致依赖读取阻塞，后端在隔离 Linux 容器中执行；浏览器使用临时依赖目录和独立容器样例服务。未将真实账号数据用于样例浏览器测试。[界面截图](../screenshots/compatible-sync-desktop.png)使用模拟账号验证成功状态及诊断展开，不能用作服务商兼容证据。

## 实际部署与网易邮箱验证

镜像 `mail-digest-agent:0.2.0-uid-inventory`，索引 `sha256:62fc179b984f70e8c64670666bd04572da31aa65c27a733a60fc7c3fcc881eae`。更新原 `mail-assistant-local-live` 项目的 API／Worker，使用原持久卷和 DNS 覆盖，入口 `http://127.0.0.1:8877`。

部署后通过实际后台 Worker 连续执行两次同步：

| 轮次 | 后台任务 | 结果 | 新下载正文 | 复用正文 | 覆盖 |
|---|---|---|---|---|---|
| 1 | `eaeafe2f3d494a6fba9589fe3d76697d` | succeeded | 0 | 5 | complete |
| 2 | `90023cced9d345cf987eb354e6186b44` | succeeded | 0 | 5 | complete |

两轮均在第一次核对中成功，`warnings=[]`。最终账号 `status=verified`、`sync_complete=true`、`error=null`，业务缓存仍为 5 封。第二轮进度为 2026-11-04 15:47:22.095，核对与提交时间为 15:47:23.552（Asia/Shanghai）。

检查点记录 `sync_method=uid_inventory`、`uidvalidity=1`、`uidnext=null`、`message_count=5`、`inventory_complete=true`，保存实际清单摘要、配置范围及验证时间。没有制造 UIDNEXT，也没有通过隐藏警告来冒充完整同步。本次没有额外 SMTP 发信或触发摘要投递。

## 验证边界

完整覆盖指已核对的文件夹成员与配置正文日期范围。超过当前快照窗口、缺少 UIDVALIDITY／收件时间、响应不完整和持续变化时，仍保留部分或未知覆盖。

真实邮箱验证使用静态测试收件箱；并发增删及提交中断使用可控模拟检查，没有删除用户邮件或强制终止真实服务来做破坏性演练。超过窗口的大邮箱分页扫描、QQ 真实联调及两次轮询间到达后又被删除邮件的捕获不属于本次已证明范围。
