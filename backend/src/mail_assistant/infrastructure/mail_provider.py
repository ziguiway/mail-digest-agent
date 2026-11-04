from __future__ import annotations

import asyncio
import json
import socket
import sys
from datetime import datetime
from typing import Protocol
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ..domain.errors import AppError
from ..domain.schemas import MailboxFolder
from pydantic import ValidationError


# mcp-email-server 1.14.0 can emit plain-text connectivity failures despite
# --json. Match only its fixed messages; never surface arbitrary CLI output.
_CONNECTIVITY_ERRORS = {
    "TLS or network connection failed; verify endpoint and TLS settings": "tls_or_connection_failed",
    "Authentication or provider policy rejected the connection or sender": "authentication_or_provider_rejected",
    "Connection test timed out; verify the endpoint and network, then retry": "timeout",
    "Credential is unavailable; save the account password again": "credential_unavailable",
    "Outgoing endpoint is unavailable; configure SMTP before testing": "endpoint_unavailable",
}
_PROVIDER_MESSAGES = {
    "tls_or_connection_failed": (
        "无法建立邮箱连接，请检查服务端网络、DNS、服务器端口和 TLS 证书",
        True,
    ),
    "authentication_or_provider_rejected": (
        "邮箱服务器拒绝登录，请检查授权码、IMAP 服务和客户端访问限制",
        False,
    ),
    "credential_unavailable": ("未能读取邮箱授权码，请重新保存邮箱凭据", False),
    "endpoint_unavailable": ("邮箱连接参数不可用，请检查服务器地址和端口", False),
    "timeout": ("邮箱连接超时，请检查服务端网络后重试", True),
}


class MailboxProvider(Protocol):
    async def list_accounts(self) -> list[dict]: ...
    async def list_folders(self, account: dict) -> list[dict]: ...
    async def metadata(self, account: dict, since: str, before: str, page: int = 1) -> dict: ...
    async def bodies(self, account: dict, ids: list[str]) -> list[dict]: ...
    async def close(self) -> None: ...


class McpManager:
    def __init__(self, settings):
        self.settings = settings
        self.lock = asyncio.Lock()

    async def command(self, *args, secret=None, timeout=40):
        try:
            process = await asyncio.create_subprocess_exec(
                self.settings.mcp_command,
                *args,
                "--json",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.settings.mcp_env(),
            )
        except OSError as exc:
            raise AppError("未找到邮件连接组件，请检查服务安装", "mcp_unavailable") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate((secret + "\n").encode() if secret is not None else None),
                timeout,
            )
        except asyncio.TimeoutError as exc:
            process.terminate()
            await process.wait()
            raise AppError("邮箱操作超时，请稍后重试", "provider_timeout", retryable=True) from exc
        envelope = None
        for candidate in (stdout.decode(errors="replace"), stderr.decode(errors="replace")):
            try:
                decoded = json.loads(candidate)
                if isinstance(decoded, dict):
                    envelope = decoded
                    break
            except ValueError:
                continue
        if process.returncode != 0 or not envelope or envelope.get("ok") is not True:
            error = (envelope or {}).get("error", {})
            code = str(error.get("code", "provider_error"))
            if not envelope:
                code = _CONNECTIVITY_ERRORS.get(
                    stderr.decode(errors="replace").strip().removeprefix("Error: "), code
                )
            if "revision" in code or "conflict" in code:
                raise AppError("配置已更新，请刷新页面后重新保存", "revision_conflict", status=409)
            message, retryable = _PROVIDER_MESSAGES.get(
                code,
                (
                    "邮箱操作未完成，请检查授权码、IMAP 服务和连接参数",
                    any(x in code for x in ("timeout", "transport", "connect"))
                    and "auth" not in code,
                ),
            )
            raise AppError(message, code, retryable=retryable)
        return envelope.get("data", {})

    async def ensure(self):
        async with self.lock:
            path = self.settings.mcp_config.parent
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)
            status = await self.command("config", "status")
            if status.get("mode") != "managed":
                await self.command("config", "init", "--database", str(path / "catalog.sqlite3"))
            policy = await self.command("config", "policy")
            data = policy.get("policy", policy)
            if data.get("allowed_mutations"):
                await self.command(
                    "config",
                    "update-policy",
                    "--expected-revision",
                    str(data.get("revision", 1)),
                    "--allowed-mutations",
                    "",
                )

    async def add(self, account, password):
        await self.ensure()
        return await self.command(
            "account",
            "add",
            account["upstream_name"],
            "--email",
            account["email"],
            "--full-name",
            account["label"],
            "--imap-host",
            account["host"],
            "--imap-port",
            str(account["port"]),
            "--imap-user",
            account["email"],
            "--allowed-mutations",
            "",
            "--password-stdin",
            secret=password,
        )

    async def show(self, name):
        data = await self.command("account", "show", name)
        return data.get("account", data)

    async def update(self, account, password=None):
        shown = await self.show(account["upstream_name"])
        revision = shown.get("revision", account["upstream_revision"])
        await self.command(
            "account",
            "update",
            account["upstream_name"],
            "--expected-revision",
            str(revision),
            "--full-name",
            account["label"],
            "--email",
            account["email"],
            "--imap-user",
            account["email"],
            "--imap-host",
            account["host"],
            "--imap-port",
            str(account["port"]),
            "--allowed-mutations",
            "",
        )
        if password:
            await self.command(
                "account",
                "set-secret",
                account["upstream_name"],
                "incoming",
                "--password-stdin",
                secret=password,
            )
        return await self.set_enabled(account["upstream_name"], account.get("enabled", True))

    async def set_enabled(self, name, enabled):
        shown = await self.show(name)
        if shown["enabled"] != enabled:
            await self.command(
                "account",
                "enable" if enabled else "disable",
                name,
                "--expected-revision",
                str(shown["revision"]),
            )
            shown = await self.show(name)
        return shown

    async def test(self, name):
        shown = await self.show(name)
        endpoint = shown.get("incoming") or {}
        if not endpoint.get("host") or not endpoint.get("port"):
            raise AppError(_PROVIDER_MESSAGES["endpoint_unavailable"][0], "endpoint_unavailable")
        try:
            await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    endpoint["host"], endpoint["port"], type=socket.SOCK_STREAM
                ),
                timeout=10,
            )
        except socket.gaierror as exc:
            raise AppError(
                "无法解析邮箱服务器地址，请检查服务端或容器的 DNS 配置后重试",
                "provider_dns_error",
                retryable=True,
            ) from exc
        except asyncio.TimeoutError as exc:
            raise AppError(
                "邮箱服务器地址解析超时，请检查服务端 DNS 后重试",
                "provider_dns_timeout",
                retryable=True,
            ) from exc
        return await self.command("account", "test", name, "incoming", timeout=65)

    async def remove(self, name):
        listed = await self.command("account", "list")
        if name not in {a["name"] for a in listed["accounts"]}:
            # A previous process may have committed upstream removal before its
            # local receipt. Never recreate or retarget that retired identity.
            return {"already_removed": True, "cleanup_required": None}
        shown = await self.show(name)
        return await self.command(
            "account",
            "remove",
            name,
            "--expected-revision",
            str(shown["revision"]),
            "--confirm",
            name,
        )


def unwrap(result):
    if result.isError:
        raise AppError(
            "邮箱检索未完成，结果可能超出服务范围或连接已失效，请缩小时间范围后重试",
            "mail_query_failed",
        )
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured.get("result") if set(structured) == {"result"} else structured
    text = "\n".join(block.text for block in result.content if hasattr(block, "text"))
    try:
        return json.loads(text)
    except ValueError as exc:
        raise AppError("邮件组件返回了无法识别的数据", "mcp_contract_error") from exc


class McpMailProvider:
    """A single actor owns the stdio session and its AnyIO cancellation scope."""

    READ_TOOLS = {
        "list_available_accounts",
        "list_mailboxes",
        "list_emails_metadata",
        "get_emails_content",
        "workspace_mailbox_snapshot",
        "workspace_mailbox_state",
    }

    def __init__(self, settings, generation=None):
        self.settings = settings
        self.generation = generation or (lambda: None)
        self.current_generation = None
        self.call_lock = asyncio.Lock()
        self.windows = {}
        self.queue = asyncio.Queue()
        self.actor = None
        self.ready = None
        self.start_error = None

    async def _run(self):
        current = None
        try:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mail_assistant.infrastructure.mcp_server"],
                env=self.settings.mcp_env(),
            )
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                if not self.READ_TOOLS.issubset({t.name for t in tools.tools}):
                    raise AppError("邮件组件版本不匹配，缺少读取工具", "mcp_contract_error")
                self.ready.set()
                while True:
                    name, arguments, future = await self.queue.get()
                    current = future
                    if future.cancelled():
                        continue
                    try:
                        result = await asyncio.wait_for(
                            session.call_tool(name, arguments),
                            180 if name == "workspace_mailbox_snapshot" else 90,
                        )
                        if not future.done():
                            future.set_result(unwrap(result))
                    except Exception as exc:
                        if not future.done():
                            future.set_exception(
                                exc
                                if isinstance(exc, AppError)
                                else AppError(
                                    "邮件组件连接中断，请重试",
                                    "mcp_transport_error",
                                    retryable=True,
                                )
                            )
                    finally:
                        current = None
        except asyncio.CancelledError:
            raise
        except Exception:
            self.start_error = AppError(
                "邮件组件未能启动，请先完成邮箱配置或重试连接", "mcp_start_failed", retryable=True
            )
        finally:
            self.ready.set()
            if current is not None and not current.done():
                current.set_exception(
                    AppError("邮件连接正在重建，请重试", "mcp_restarting", retryable=True)
                )
            while not self.queue.empty():
                _, _, future = self.queue.get_nowait()
                if not future.done():
                    future.set_exception(AppError("邮件连接已关闭", "mcp_closed", retryable=True))

    async def call(self, name, arguments):
        async with self.call_lock:
            generation = self.generation()
            if generation != self.current_generation:
                await self.close()
                self.current_generation = generation
            return await self._call(name, arguments)

    async def _call(self, name, arguments):
        if name not in self.READ_TOOLS:
            raise AppError("此操作不在邮件只读接口中", "unsupported_operation")
        if self.actor is None or self.actor.done():
            self.ready, self.start_error = asyncio.Event(), None
            self.actor = asyncio.create_task(self._run())
        await asyncio.wait_for(self.ready.wait(), timeout=25)
        if self.start_error:
            raise self.start_error
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((name, arguments, future))
        return await asyncio.wait_for(future, timeout=190)

    async def list_accounts(self):
        result = await self.call("list_available_accounts", {})
        return result.get("accounts", []) if isinstance(result, dict) else result

    async def list_folders(self, account):
        result = await self.call("list_mailboxes", {"account_name": account["upstream_name"]})
        if not isinstance(result, list):
            raise AppError("邮件组件返回的文件夹列表无效，请重试", "mcp_contract_error")
        try:
            folders = [MailboxFolder.model_validate(value) for value in result]
            if len({f.name for f in folders}) != len(folders):
                raise ValueError("duplicate mailbox")
            return [
                (
                    f.model_copy(
                        update={
                            "selectable": "\\noselect" not in {flag.lower() for flag in f.flags}
                        }
                    )
                ).model_dump()
                for f in folders
            ]
        except (ValidationError, ValueError) as exc:
            raise AppError("邮件组件返回的文件夹列表无效，请重试", "mcp_contract_error") from exc

    async def metadata(self, account, since, before, page=1):
        key = (account["id"], account["folder"], since, before)
        if page == 1:
            snapshot = await self.call(
                "workspace_mailbox_snapshot",
                {"account_name": account["upstream_name"], "mailbox": account["folder"]},
            )
            self.windows = {k: v for k, v in self.windows.items() if k[0] != account["id"]}
            start, end = datetime.fromisoformat(since), datetime.fromisoformat(before)
            rows = [
                row
                for row in snapshot["emails"]
                if not row.get("received_at")
                or start <= datetime.fromisoformat(row["received_at"]) < end
            ]
            self.windows[key] = snapshot | {
                "emails": sorted(rows, key=lambda row: int(row["email_id"])),
                "observations": snapshot["emails"],
            }
        if key not in self.windows:
            raise AppError("同步窗口已失效，请重新同步", "snapshot_expired", retryable=True)
        window = self.windows[key]
        return window | {
            "emails": window["emails"][(page - 1) * 100 : page * 100],
            "total": len(window["emails"]),
            "page": page,
        }

    async def verify_snapshot(self, account, state):
        current = await self.call(
            "workspace_mailbox_state",
            {"account_name": account["upstream_name"], "mailbox": account["folder"]},
        )
        if current != state:
            raise AppError(
                "读取正文期间邮箱状态发生变化，本次覆盖尚未确认，请重试",
                "mailbox_changed",
                retryable=True,
            )

    async def bodies(self, account, ids):
        result = await self.call(
            "get_emails_content",
            {
                "account_name": account["upstream_name"],
                "mailbox": account["folder"],
                "email_ids": ids,
            },
        )
        if isinstance(result, dict):
            if result.get("failed_ids") or result.get("content_omitted"):
                raise AppError(
                    "部分邮件正文未返回，请缩小读取批次后重试",
                    "partial_body_result",
                    retryable=True,
                )
            return result.get("emails", [])
        return result

    async def close(self):
        if self.actor and not self.actor.done():
            self.actor.cancel()
            await asyncio.gather(self.actor, return_exceptions=True)
        self.actor = None
