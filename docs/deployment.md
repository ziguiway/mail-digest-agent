# 自托管部署

## 直接运行预构建镜像

安装并启动 Docker，Docker Desktop 选择 Linux 容器模式。x86-64 主机使用 `linux-amd64` 镜像包，Apple Silicon 和 ARM64 主机使用 `linux-arm64` 镜像包。以 AMD64 为例，从镜像包所在目录导入并启动：

```sh
docker load -i mail-digest-agent-0.2.0-linux-amd64.tar.gz && docker run --pull=never -d --name mail-digest-agent --restart unless-stopped --stop-timeout 45 -p 127.0.0.1:8765:8765 --mount type=volume,source=mail-digest-agent-data,target=/var/lib/mail-assistant mail-digest-agent:0.2.0
```

两个架构的归档均导入为 `mail-digest-agent:0.2.0`。镜像默认同时运行 API 和 Worker，容器健康检查覆盖网页接口和后台心跳。命名卷由 Docker 创建，用于保存配置和业务数据。[Docker 镜像导入](https://docs.docker.com/reference/cli/docker/image/load/)

打开 `http://127.0.0.1:8765`。首次进入时读取一次性凭证，在网页设置管理密码，然后配置邮箱、模型和可选飞书。

```sh
docker exec mail-digest-agent mail-assistant bootstrap-token
```

可用 `docker restart mail-digest-agent` 重启。网络诊断使用 `docker exec mail-digest-agent python /app/deploy/check_network.py`。容器需要指定解析服务器时，在镜像名之前添加 `--dns` 参数和可达的 DNS 地址。

## 从源码一行部署

准备已启动的 Docker Engine 或 Docker Desktop（Linux 容器模式），以及支持 `up --wait` 的 Compose 插件和 Buildx。首次构建需要 Docker 能访问镜像仓库、Debian、npm、PyPI 和飞书 CLI 的下载源。在仓库根目录执行：

```sh
sh deploy/up.sh
```

构建完成后，脚本等待 API 和 Worker 通过健康检查，输出工作台地址和首次初始化凭证。浏览器打开默认地址 `http://127.0.0.1:8765`，在网页填写凭证并设置管理密码，随后配置邮箱、模型和可选飞书。已初始化的工作台会提示直接登录。Windows 可在 WSL 或 Git Bash 中使用同一命令。

API 与 Worker 复用同一次构建的镜像。API 先初始化工作空间，健康检查通过后 Worker 开始运行。两者均以 UID 10001 运行，使用 Docker 创建的 `workspace` 命名卷。镜像包含 Python、已构建的前端、邮件 MCP、Node 和飞书 CLI。

如 8765 已被占用，可指定其他空闲端口，例如 `MAIL_ASSISTANT_HTTP_PORT=8766 sh deploy/up.sh`。首次凭证也可通过 `docker compose exec -T api mail-assistant bootstrap-token` 显式读取。

源码部署从本地项目构建。已有镜像的部署可指定 `MAIL_ASSISTANT_IMAGE`，用 `docker compose up --no-build --wait` 启动。

## 状态与停止

```bash
docker compose logs --tail 100 api worker
docker compose restart
docker compose up --wait --wait-timeout 90
docker compose stop
```

API 健康检查访问 `/api/v1/health`；Worker 检查业务库中最近的心跳。两项分开检查，避免 API 正常但后台已停止的情况被掩盖。邮件账号和模型测试仍需要在网页单独通过。

`docker compose down` 保留命名卷；`docker compose down --volumes` 会删除项目持久数据，仅用于明确要清空数据的环境。重新构建镜像不会代替数据迁移与恢复验证。

默认端口只绑定主机回环地址。服务器远程访问可以先使用 SSH 转发：`ssh -L 8765:127.0.0.1:8765 用户@服务器`。导入、备份、恢复和升级回退操作见[维护说明](maintenance.md)，包括不受容器 UID 文件权限影响的归档流传输方式。

HTTPS 使用附加的 `compose.https.yaml`，配置见[HTTPS 部署](https.md)。API 端口会被移除，由 Caddy 提供同源网页和接口。

`MAIL_ASSISTANT_HTTP_PORT` 可调整本机 HTTP 端口，`MAIL_ASSISTANT_IMAGE` 可选择固定镜像标签／摘要。独立样例部署可设置 `MAIL_ASSISTANT_MODE=demo`，此时 API 和 Worker 仍分别运行；使用独立项目名和卷，不与真实数据共用。

## 检查容器外网

容器健康检查覆盖网页和 Worker 心跳。以下命令从 Worker 直接连接网易、QQ 的 IMAP 993 端口，以及 OpenRouter、飞书的 HTTPS 443 端口。检查分为 DNS、TCP 和 TLS 三步，使用系统信任的证书，不需要服务凭据。

```sh
docker compose exec -T worker python /app/deploy/check_network.py
```

如果显示 DNS 检查失败，先检查 Docker 主机或 Colima 的解析配置。曾遇到 Colima 的 `/etc/resolv.conf` 指向不存在的文件，导致容器访问外部服务时无法解析域名。可以用可选覆盖文件，为两个服务指定当前网络中从容器可达的 DNS。将示例地址替换成当前网络的 DNS，再执行一行部署：

```sh
MAIL_ASSISTANT_DNS=你的网络DNS地址 sh deploy/up.sh -f compose.yaml -f compose.dns.yaml
```

DNS 覆盖在容器运行时生效。构建阶段的仓库访问由 Docker Engine 和 BuildKit 的网络决定。需要代理时按 Docker 文档配置构建和运行时代理。[Docker 代理配置](https://docs.docker.com/engine/cli/proxy/)

使用覆盖文件时，后续部署与维护命令沿用相同的 Compose 文件、环境文件和项目名。TCP 失败时检查出口端口，TLS 失败时检查系统时间和信任链。邮箱授权、模型与飞书配置在网页中分别测试。

## 本地部署验收

两个预构建镜像包均已从归档重新导入，在源码目录之外直接用 Docker 启动。ARM64 使用原生架构，AMD64 使用 QEMU。两者均通过默认健康检查、首次初始化、替换容器后使用原卷登录、样例摘要生成、带来源查询和四个外部服务的直连测试。

2026-11-09 在独立 Colima 环境中完成 ARM64 构建，用无 `.env` 的源码副本和全新卷执行 `sh deploy/up.sh`，验证地址和凭证输出、网页初始化、UID 10001 读写、凭证文件 0600 权限、飞书 CLI 生成本地二维码和再次启动后的数据保留。Agently 在 UID 10001 下也成功在数据目录创建了工作区。

容器回归覆盖旧配置导入、离线备份、新卷恢复、MCP 账号发现、摘要复用、查询来源及本地 CA HTTPS。启动检查在故意配置错误 HTTP 代理时仍能访问容器回环地址。

AMD64 镜像在同一 ARM64 宿主机通过 QEMU 验证了构建、启动、初始化、导入、恢复、样例摘要和查询。首次交叉构建时 Node 在仿真环境中异常退出，现由构建机原生编译前端静态资源，运行时组件仍使用目标架构。[Docker 多平台构建](https://docs.docker.com/build/building/multi-platform/)

在 Colima 复现了 DNS 配置缺失，使用可达 DNS 后，API 和 Worker 都通过网易、QQ、OpenRouter 和飞书的 DNS、TCP、TLS 检查。本地回归命令也已接入 CI 工作流：

```sh
python3 deploy/verify_containers.py --build --https --image mail-digest-agent:container-check
```

## 待完成的部署验收

1. 完成网页真实服务配置闭环，关闭浏览器后确认任务继续执行，并补充至少 24 小时运行记录。
2. 将已通过的容器新卷恢复扩展到真实邮箱／飞书认证后端；当前证明的是测试配置、MCP 发现、应用密钥、样例邮件及会话恢复。
3. 在实际域名服务器验证公网 CA 签发，并演练已发布旧版本镜像的升级回退。
4. 在远端实际运行 CI；已有配置和本地脚本通过不计为远端通过。
