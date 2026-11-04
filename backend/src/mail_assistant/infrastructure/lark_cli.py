from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from contextlib import suppress
from ..domain.errors import AppError

READY_MARKER = "[event] ready event_key=im.message.receive_v1"


def json_documents(text):
    """Accept both pretty JSON and NDJSON mixed with CLI status lines."""
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, length = decoder.raw_decode(text[start:])
            index = start + length
            if isinstance(value, dict):
                yield value
        except ValueError:
            index = start + 1


def cli_error(documents, *, uncertain=False):
    error = next(
        (doc.get("error", {}) for doc in reversed(documents) if doc.get("ok") is False), {}
    )
    category, subtype = error.get("type", ""), error.get("subtype", "")
    code = "feishu_" + str(subtype or category or "cli_failed")
    message = "飞书操作未完成，请检查应用配置、网络与权限"
    if subtype == "missing_scope":
        message = "飞书应用缺少权限，请在开发者后台开通并发布后重试"
    elif subtype in {"invalid_client", "not_configured"}:
        message = "飞书应用配置不可用，请重新绑定或检查 App ID 与 Secret"
    elif category == "network":
        message = "飞书网络连接失败，请检查服务端网络后重试"
    exc = AppError(message, code, retryable=category in {"network", "internal"} and not uncertain)
    exc.details = {key: error[key] for key in ("console_url", "missing_scopes") if key in error}
    exc.uncertain = uncertain
    return exc


async def terminate(process):
    if process.returncode is not None:
        return
    if process.stdin and not process.stdin.is_closing():
        process.stdin.close()
    try:
        await asyncio.wait_for(process.wait(), 1)
        return
    except TimeoutError:
        pass
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError as exc:
        raise AppError("飞书子进程未能退出，请查看服务进程状态", "feishu_process_stuck") from exc


class LarkCli:
    def __init__(self, settings):
        self.settings = settings
        self.directory = settings.feishu_dir
        self.processes = set()

    def prepare(self):
        for directory in (self.directory, self.directory / "cli", self.directory / "qr"):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)

    async def spawn(self, profile, *args):
        self.prepare()
        try:
            process = await asyncio.create_subprocess_exec(
                self.settings.lark_command,
                "--profile",
                profile,
                *args,
                cwd=self.directory,
                env=self.settings.lark_env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=1_048_576,
            )
        except OSError as exc:
            raise AppError(
                "未找到飞书 CLI，请检查服务安装并重启后台", "feishu_cli_unavailable"
            ) from exc
        self.processes.add(process)
        return process

    async def finish(self, process):
        try:
            await terminate(process)
        finally:
            if process.returncode is not None:
                self.processes.discard(process)

    async def run(self, profile, *args, input=None, timeout=45, local=False, sending=False):
        process = await self.spawn(profile, *args)
        try:
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input.encode() if input is not None else None), timeout
                )
            except TimeoutError as exc:
                error = AppError(
                    "飞书操作超时；发送请求的结果可能不确定" if sending else "飞书操作超时，请重试",
                    "feishu_send_uncertain" if sending else "feishu_timeout",
                    retryable=not sending,
                )
                error.uncertain = sending
                raise error from exc
            documents = list(json_documents(stdout.decode(errors="replace"))) + list(
                json_documents(stderr.decode(errors="replace"))
            )
            if process.returncode != 0 or any(doc.get("ok") is False for doc in documents):
                raise cli_error(
                    documents, uncertain=sending and process.returncode in {4, 5, -signal.SIGTERM}
                )
            result = next((doc for doc in reversed(documents) if doc.get("ok") is True), None)
            if result:
                return result.get("data", {})
            if local and documents:
                return documents[-1]
            error = AppError(
                "飞书 CLI 没有返回可核实的成功结果",
                "feishu_send_uncertain" if sending else "feishu_contract_error",
            )
            error.uncertain = sending
            raise error
        finally:
            await self.finish(process)

    async def qrcode(self, profile, url):
        import hashlib

        identity = hashlib.sha256(url.encode()).hexdigest()
        path = self.directory / "qr" / (identity + ".png")
        if not path.exists():
            process = await self.spawn(
                profile, "auth", "qrcode", url, "--output", "qr/" + path.name
            )
            try:
                await asyncio.wait_for(process.communicate(), 15)
                if (
                    process.returncode != 0
                    or not path.is_file()
                    or path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n"
                ):
                    raise AppError("授权二维码生成失败，请重试或先打开原始链接", "feishu_qr_failed")
                path.chmod(0o600)
            finally:
                await self.finish(process)
        return identity

    async def close(self):
        for process in list(self.processes):
            await self.finish(process)


def authorization_urls(text):
    # The pinned CLI writes its verification URL on its own stderr line.
    # Strip whitespace/terminal escapes only; preserve the URL's actual bytes.
    clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).strip()
    if clean.startswith(("https://", "http://")) and not any(c.isspace() for c in clean):
        return [clean]
    return [
        doc[key]
        for doc in json_documents(clean)
        for key in ("verification_url", "verification_uri_complete", "console_url")
        if isinstance(doc.get(key), str)
    ]
