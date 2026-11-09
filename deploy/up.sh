#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

if ! docker compose version >/dev/null 2>&1; then
    printf '%s\n' '需要 Docker 和 Docker Compose 插件。请安装后重新运行。' >&2
    exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
    printf '%s\n' '需要 Docker Buildx 插件来构建镜像。请安装后重新运行。' >&2
    exit 1
fi

if ! docker info --format '{{.ServerVersion}}' >/dev/null; then
    printf '%s\n' '无法连接 Docker。请启动 Docker，并确认当前用户可以访问 Docker 服务。' >&2
    exit 1
fi

if ! docker compose "$@" up --build --wait --wait-timeout 180; then
    printf '%s\n' '部署未完成。请查看上方构建错误或运行 docker compose logs --tail 80 api worker。网络与端口排查见 docs/deployment.md。' >&2
    exit 1
fi

if address=$(docker compose "$@" port api 8765 2>/dev/null); then
    printf '工作台 http://%s\n' "$address"
fi

docker compose "$@" exec -T api python - <<'PY'
import json
import os
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

client = build_opener(ProxyHandler({}))
with client.open("http://127.0.0.1:8765/api/v1/auth/status", timeout=5) as response:
    initialized = json.load(response)["initialized"]

if initialized:
    print("工作台已初始化，请使用管理密码登录。")
else:
    token_file = Path(os.environ["MAIL_ASSISTANT_DATA_DIR"]) / "bootstrap.token"
    print("请在网页输入下方一次性凭证，然后设置管理密码。")
    print(token_file.read_text().strip())
PY
