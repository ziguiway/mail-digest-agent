# 容器与 HTTPS 验证记录

日期：2026-11-04，Asia/Shanghai。使用项目本地源码构建，未发布镜像或源码到远端。

## 环境与产物

| 项目 | 本次环境 |
|---|---|
| 宿主机 | Apple Silicon，macOS 26.5.1 |
| 运行环境 | Colima 0.10.3，独立 `mail-assistant` profile，2 CPU／4 GiB |
| 容器服务端 | Docker 29.5.2，Linux arm64 |
| 客户端 | Docker 29.9.2，Compose 5.6.1，Buildx 0.38.1 |
| 应用镜像 | `mail-digest-agent:0.2.0-deployment-check`（本地标签） |
| 镜像标识 | `sha256:ccbf3a354edb7e8cac5de1a092751328e3a8b8e6825c9b5e6b323da8aed12c66` |
| HTTPS 代理 | 官方 `caddy:2.11.7-alpine`，本地 CA |

Colima 没有设置开机启动，也没有切换用户默认 Docker context。检查使用 `runtime/deployment/docker-cli` 私有客户端配置。镜像内 API／Worker 以 UID 10001 运行，飞书 CLI 固定为 1.0.97。

## 已执行

执行实际 `docker compose build`，再运行：

```bash
python3 deploy/verify_containers.py --https \
  --report docs/verification/2026-11-04-container-checks.json
```

脚本通过独立随机项目和卷执行下表流程。实际检查时使用宿主虚拟环境 Python 的 `-S` 选项启动此纯标准库脚本，绕过本机偶发的 site 初始化文件读取超时；容器中的应用正常启动，无此选项。机器结果见[JSON 报告](2026-11-04-container-checks.json)。

| 检查 | 结果与边界 |
|---|---|
| 镜像构建、首次启动 | 通过，API／Worker 分开运行并通过各自健康检查；静态网页可访问 |
| 初始化与会话 | 通过，一次性凭证初始化、密码登录、错误 Origin 拒绝；不输出测试口令或凭证 |
| 运行身份与工具 | API UID 10001，镜像内实际 `lark-cli --version` 为 1.0.97 |
| 旧配置导入 | 通过，实际 CLI 导入两个无效测试账号及模型设置；目标为回环地址，不进行邮箱／模型登录；计划和同步保持关闭 |
| 运行中备份 | 正确返回 `workspace_busy`，未产生归档流 |
| 新卷恢复与重启 | 通过，实际归档 stdin／stdout；新卷中账号引用、MCP 发现、模型密钥解密、管理密码及重启状态一致 |
| 容器业务闭环 | 样例模式的 API／Worker 完成实际任务及 TriggerFlow；首份摘要 5 项，再次汇总为空；样例问题得到来源和会议更新 |
| 业务数据恢复 | 样例 6 封邮件、摘要、会话及来源在另一个新卷恢复后保留 |
| HTTPS 会话 | 通过本次客户端加载的 Caddy 公开根证书验证主机名，登录 Cookie 带 Secure；未修改宿主系统信任库 |
| 代理与 SSE | HTTPS 模式 API 不发布主机端口；Worker 停止、任务仍 queued 时收到 stage 事件，证明首个事件未被等待到任务结束 |
| 检查资源清理 | 随机检查项目和卷在结束后清理，未选择现有用户部署 |

## 验证中修复

第一次构建在飞书 CLI 安装时失败：其下载脚本需要 `curl`，Node 精简镜像未包含。已在安装阶段加入 curl 和证书包，重新构建成功。

第一次恢复到新 Docker 卷时失败：恢复暂存目录位于 `/var/lib`，运行用户只拥有应用卷。已改为卷内暂存，保留维护锁 inode；补充“父目录只读、卷可写”回归并通过 9 项维护专项检查，随后实际容器恢复成功。

Compose 样例模式使用独立 API 和 Worker，修正 API 命令在 demo mode 下重复启动内嵌 Worker 的行为；只有 `demo` 单进程命令内嵌 Worker。Docker 构建将锁定依赖与源码安装分开，后续改源码可复用依赖层。

本轮原生后端全量为 **62 passed**（270.72 秒），新增维护检查后对应专项为 9 项通过。一次原生全量检查曾在 SQLAlchemy 可重建字节码的文件读取中阻塞；经进程采样和打开文件定位后中断该次检查、重建对应缓存，再运行全量通过。中断不计为通过。容器检查与此宿主缓存独立。前端本轮无代码变化，沿用上一轮 12 项 Chromium 检查记录。

## 当前运行入口

- `http://127.0.0.1:8765`：原生样例，正文与模型由规则模拟。
- `http://127.0.0.1:8877`：本次准备的真实配置入口，项目名 `mail-assistant-local-live`，独立业务卷；等待用户在网页配置专用凭据。

真实入口使用 `runtime/deployment/live.env` 和本地固定镜像标签。一次性初始化凭证写入 `runtime/deployment/live.bootstrap.token`，权限 0600，未写入报告或日志。该目录已被 Git／构建上下文忽略。

## 未验收项

实际 QQ／网易兼容、真实模型输出与工具调用、飞书授权／收发、真实凭据恢复、连续至少 24 小时且跨过 09:00 的真实配置运行，仍需用户配置后进行。公网 CA 的实际签发、远端 GitHub Actions 及已发布旧版本镜像的升级回退尚未验证；本次不将本地 CA、样例或检查配置的存在算作这些通过。

CI 已改为调用相同容器检查脚本，并上传报告；远端运行记录尚无。原生环境的旧格式数据库迁移与本次新卷恢复提供各自证据，不能合并成未经执行的发布版本升级结论。
