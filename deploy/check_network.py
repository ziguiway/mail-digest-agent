"""Check external TLS connectivity from the app container without credentials."""

import socket
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor

ENDPOINTS = (
    ("imap.163.com", 993),
    ("imap.qq.com", 993),
    ("openrouter.ai", 443),
    ("open.feishu.cn", 443),
)


def check(endpoint):
    host, port = endpoint
    stage = "DNS"
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        stage = "TCP"
        with socket.create_connection(endpoint, timeout=5) as stream:
            stage = "TLS"
            with ssl.create_default_context().wrap_socket(stream, server_hostname=host):
                return f"{host}:{port} DNS、TCP、TLS 均通过", True
    except OSError as error:
        return f"{host}:{port} {stage} 检查失败（{error}）", False


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=len(ENDPOINTS)) as pool:
        results = list(pool.map(check, ENDPOINTS))
    for message, _ in results:
        print(message)
    sys.exit(0 if all(success for _, success in results) else 1)
