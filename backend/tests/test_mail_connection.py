import asyncio
import json
import socket
from unittest.mock import AsyncMock

import pytest

from mail_assistant.config import Settings
from mail_assistant.domain.errors import AppError
from mail_assistant.infrastructure.mail_provider import McpManager


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize(
    ("detail", "code", "retryable", "message"),
    [
        (
            "TLS or network connection failed; verify endpoint and TLS settings",
            "tls_or_connection_failed",
            True,
            "TLS",
        ),
        (
            "Authentication or provider policy rejected the connection or sender",
            "authentication_or_provider_rejected",
            False,
            "拒绝登录",
        ),
        (
            "Connection test timed out; verify the endpoint and network, then retry",
            "timeout",
            True,
            "超时",
        ),
    ],
)
async def test_connection_errors_preserve_category_without_exposing_cli_details(
    tmp_path, monkeypatch, structured, detail, code, retryable, message
):
    raw = (
        json.dumps({"ok": False, "error": {"code": code, "message": "secret-canary"}}).encode()
        if structured
        else f"Error: {detail}\n".encode()
    )
    process = AsyncMock(returncode=1)
    process.communicate.return_value = (raw, b"") if structured else (b"", raw)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(AppError) as caught:
        await McpManager(Settings(tmp_path)).command("account", "test", "fixture", "incoming")
    assert caught.value.code == code and caught.value.retryable is retryable
    assert message in caught.value.message
    assert "secret-canary" not in caught.value.message


async def test_unknown_plaintext_is_never_displayed(tmp_path, monkeypatch):
    process = AsyncMock(returncode=1)
    process.communicate.return_value = (b"", b"Error: server echoed secret-canary\n")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(AppError) as caught:
        await McpManager(Settings(tmp_path)).command("account", "test", "fixture", "incoming")
    assert caught.value.code == "provider_error"
    assert "secret-canary" not in caught.value.message


async def test_dns_failure_does_not_attempt_authentication(tmp_path, monkeypatch):
    manager = McpManager(Settings(tmp_path))
    manager.show = AsyncMock(
        return_value={"incoming": {"host": "imap.example.invalid", "port": 993}}
    )
    manager.command = AsyncMock()
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(side_effect=socket.gaierror(socket.EAI_AGAIN, "Temporary failure")),
    )
    with pytest.raises(AppError) as caught:
        await manager.test("fixture")
    assert caught.value.code == "provider_dns_error" and caught.value.retryable
    assert "DNS" in caught.value.message and "授权码" not in caught.value.message
    manager.command.assert_not_awaited()


async def test_real_cli_closed_local_port_reports_connection_failure(tmp_path):
    manager = McpManager(Settings(tmp_path))
    # Reserve a local TCP port without listening; no external service is needed.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        await manager.add(
            {
                "upstream_name": "connection_contract",
                "email": "test@example.invalid",
                "label": "offline fixture",
                "host": "127.0.0.1",
                "port": reserved.getsockname()[1],
            },
            "offline-test-code",
        )
        with pytest.raises(AppError) as caught:
            await manager.test("connection_contract")
    # The pinned async IMAP client can report a timeout for a refused socket.
    assert caught.value.code in {"tls_or_connection_failed", "timeout"}
    assert caught.value.retryable
    assert "授权码" not in caught.value.message
