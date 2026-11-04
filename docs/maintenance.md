# 导入、备份与恢复

这些命令已经实现并通过本地检查；Docker 命令仍需在可用容器环境中验收。日常配置在网页完成，命令行用于部署维护。所有示例从项目根目录运行。

## 离线维护

先停止 API 和 Worker；使用 `run`／`demo` 启动时先退出对应进程，Compose 使用 `docker compose stop`。每个服务持有共享运行锁，维护命令申请独占锁；目录被使用时命令直接拒绝，不会默默复制写入中的数据。锁文件会保留，不需要手动删除。

普通文件路径模式下，成功返回 JSON 报告，失败退出码为 2。`backup --output -` 把归档写到 stdout，报告写到 stderr；`restore --input -` 从 stdin 读取归档，报告同样写到 stderr。不要将 stderr 合并进归档流。

以下示例默认 live 模式。样例工作空间需显式设置 `MAIL_ASSISTANT_MODE=demo`，恢复模式必须与归档相同。维护命令不会启动网页、调度或消息发送。

## 导入旧 TOML／.env

先预检，再执行。`--env-file` 可省略，此时读取 TOML 同目录下存在的 `.env`；同名进程环境变量优先。不执行 `.env` 中的 shell 语句或插值，也不修改当前进程环境。

```bash
uv run --package mail-assistant mail-assistant --data-dir runtime/live import-legacy \
  --config /path/to/old/config.toml --env-file /path/to/old/.env --dry-run

uv run --package mail-assistant mail-assistant --data-dir runtime/live import-legacy \
  --config /path/to/old/config.toml --env-file /path/to/old/.env
```

预检检查账号、服务器、模型、时间、时区与凭据变量，报告账号数量、非敏感配置和缺失变量名称，不输出凭据值，也不创建目标目录。字段不合法或缺少凭据时先修复再导入。

目标须没有已有邮箱／模型／计划配置；已设置的管理密码可保留。导入后：

- 原 TOML、.env 和原型邮件数据库保持原样。本命令只迁移配置，不导入原型邮件缓存。
- 工程版账号映射到 MCP 稳定名称。相同来源、相同配置再次执行返回 `already_imported`；中途失败后可按原命令继续，已提交账号不重复创建。
- 来源文件内容改变后不会覆盖已导入配置，请在网页编辑。来源路径用于识别同一导入，继续导入时保持原路径。
- 模型密钥加密保存，邮箱凭据经 stdin 交给 MCP 管理 CLI；导入后的连接处于待验证状态。每日摘要和后台同步均关闭，在网页验证后再开启。
- 旧飞书接收目标保存为提示，不接管已有全局 CLI profile；仍需走网页绑定、配对和测试。
- 原型条数／模型执行上限不直接映射，预检报告会说明工程版使用自己的执行策略。

Compose 中也可以挂载旧配置后运行同一命令；挂载的目录与文件须能被容器 UID 10001 读取：

```bash
docker compose stop
docker compose run --rm --no-deps -T \
  -v /path/to/old:/legacy:ro api import-legacy \
  --config /legacy/config.toml --env-file /legacy/.env --dry-run
```

核对预检报告后去掉 `--dry-run` 执行，再启动服务进入网页验证。

## 备份

原生运行可指定归档路径；文件必须在数据目录之外，已有文件名会被拒绝：

```bash
uv run --package mail-assistant mail-assistant --data-dir runtime/live backup \
  --output backups/workspace-before-upgrade.tar.gz
```

备份包含业务数据、应用密钥、初始化凭证、MCP 目录及工作空间内的飞书文件；SQLite 通过备份 API 生成独立快照，包含已提交 WAL 数据，归档不依赖 WAL／SHM 文件。清单记录格式、应用版本、文件大小和 SHA-256。锁及 SQLite 临时侧文件不归档；符号链接、外部 MCP 目录和不支持的布局会被拒绝。

普通路径生成的归档权限为 0600。归档本身包含凭据相关文件，未另外加密。流式输出的文件权限由接收端创建，以下 `umask` 将其限制为当前用户：

```bash
docker compose stop
mkdir -p backups
umask 077
set -C
docker compose run --rm --no-deps -T api backup --output - \
  > backups/workspace-before-upgrade.tar.gz
```

`set -C` 防止重定向覆盖同名归档。命令成功退出才表示备份写完，失败时留下的输出不可作为有效备份。

报告中的 `portability` 列出外部认证依赖。锁定版本的邮件 MCP 在 Linux／Windows 使用目录数据库存储凭据，macOS 默认使用系统钥匙串；归档不会导出系统钥匙串。飞书工作空间文件会包含在归档中，但外部钥匙串的可迁移性尚未验证。迁移机器后按报告重新填写邮箱授权码或重新绑定飞书，不把复制 profile 当成认证已通过。

## 恢复到新目录或新卷

```bash
uv run --package mail-assistant mail-assistant --data-dir runtime/restored restore \
  --input backups/workspace-before-upgrade.tar.gz
```

目标必须是新的空目录，已有工作空间会被拒绝。恢复先在临时目录验证全部文件校验、数据库完整性、已知迁移版本以及加密配置与应用密钥的匹配关系，通过后写入目标。归档格式版本为 1，解包总量上限为 2 GiB、文件数上限为 100,000。

MCP 的绝对目录引用通过上游公开 bootstrap 写入机制重建，不依赖旧目录存在，也不修改上游私有数据库表。恢复清除过时的 Worker 心跳，保留任务、摘要和发送回执；发送结果不确定的记录仍需核对，不因恢复自动重发。

Compose 使用新的项目名获得新卷，旧项目保持停止：

```bash
docker compose -p mail-assistant-restored run --rm --no-deps -T api restore --input - \
  < backups/workspace-before-upgrade.tar.gz
docker compose -p mail-assistant-restored up --wait api
```

先打开网页核对配置和恢复报告。如需重新授权，先关闭原有摘要／同步计划，再启动 Worker 进行连接测试与飞书引导：

```bash
docker compose -p mail-assistant-restored up --wait worker
```

沿用备份中的管理密码；确认模型、邮箱、飞书及计划状态后再启用所需计划。旧项目继续停止，避免两个工作空间同时消费同一飞书会话或执行相同计划。

## 升级与回退

升级前停止服务，创建并保存成功的归档，同时保留当时镜像的明确标签或摘要。部署新镜像后核对迁移、API／Worker 健康、连接测试和一轮业务任务。不要只保留会被覆盖的 `local` 标签。

回退时先停止新版，再使用升级前镜像把对应归档恢复到新空卷，按上节检查后启动。旧镜像不应直接打开新版本迁移后的数据库。当前本地已验证维护命令和新目录恢复；镜像升级／回退、Docker 新卷和真实凭据恢复仍待对应环境验收。
