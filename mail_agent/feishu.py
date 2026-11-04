from __future__ import annotations

import json
import logging
import subprocess
import threading
import uuid
from contextlib import contextmanager

from .config import FeishuConfig

LOG = logging.getLogger(__name__)


class FeishuError(RuntimeError):
    pass


def split_text(text: str, limit=2800) -> list[str]:
    """Bound both CLI input and Feishu message size, including Chinese text."""
    chunks = []
    while text:
        end = min(limit, len(text))
        if end < len(text):
            boundary = text.rfind("\n", 0, end)
            if boundary > limit // 2:
                end = boundary + 1
        chunks.append(text[:end])
        text = text[end:]
    return chunks or ["（空内容）"]


class Feishu:
    def __init__(self, config: FeishuConfig, *, run=None, store=None):
        self.config = config
        self.run = run or subprocess.run
        self.store = store

    def deliver(self, text: str, key: str, *, reply_to=""):
        parts = split_text(text)
        for index, part in enumerate(parts):
            delivery_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{key}:{index}"))
            if self.store and self.store.get("sent_chunk:" + delivery_key):
                continue
            if len(parts) > 1:
                part = f"（{index + 1}/{len(parts)}）\n" + part
            # Exact native post JSON preserves the source text and does not ask
            # the CLI Markdown converter to fetch remote images.
            content = {"zh_cn": {"content": [[{"tag": "text", "text": part}]]}}
            command = [self.config.cli, "im", "+messages-reply" if reply_to else "+messages-send"]
            if reply_to:
                command += ["--message-id", reply_to]
            else:
                command += ["--" + self.config.receive_id_type.replace("_", "-"), self.config.receive_id]
            command += ["--as", "bot", "--msg-type", "post", "--content", "-", "--idempotency-key", delivery_key, "--format", "json"]
            try:
                result = self.run(command, input=json.dumps(content, ensure_ascii=False), text=True, capture_output=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise FeishuError("飞书 CLI 不可用或请求超时；摘要发送进度已保留") from error
            try:
                envelope = json.loads(result.stdout if result.returncode == 0 else result.stderr)
            except (json.JSONDecodeError, TypeError):
                envelope = {}
            if result.returncode != 0 or envelope.get("ok") is not True:
                error_info = envelope.get("error", {})
                category = "/".join(str(error_info.get(k, "")) for k in ("type", "subtype")).strip("/")
                raise FeishuError("飞书发送失败" + (f"（{category}）" if category else "") + "；请检查机器人授权、接收对象与权限")
            if self.store:
                self.store.set("sent_chunk:" + delivery_key, True)

    def accepts(self, event: dict) -> bool:
        owner = self.config.owner_open_id or (self.config.receive_id if self.config.receive_id_type == "user_id" else "")
        if not owner or event.get("sender_id") != owner:
            return False
        if event.get("sender_type") != "user" or event.get("message_type") not in {"text", "post"}:
            return False
        if not event.get("message_id") or not isinstance(event.get("content"), str) or not event["content"].strip():
            return False
        if self.config.receive_id_type == "chat_id":
            return event.get("chat_id") == self.config.receive_id
        return event.get("chat_type") == "p2p"

    @contextmanager
    def listen(self):
        command = [self.config.cli, "event", "consume", "im.message.receive_v1", "--as", "bot"]
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        except OSError as error:
            raise FeishuError("无法启动飞书事件监听，请检查 lark-cli 是否安装") from error
        ready = threading.Event()

        def drain_stderr():
            for line in process.stderr:
                if line.startswith("[event] ready event_key=im.message.receive_v1"):
                    ready.set()
                elif line.startswith("[event]") or "WARN" in line:
                    LOG.warning("飞书事件状态：%s", line.strip())
                else:
                    # CLI authorization diagnostics may carry URLs. Keep its
                    # raw output out of the application's normal logs.
                    LOG.warning("飞书事件监听返回诊断信息；可单独运行 event consume 检查配置")

        reader = threading.Thread(target=drain_stderr, daemon=True)
        reader.start()
        try:
            # Wait on the documented marker, never assume readiness after sleep.
            for _ in range(200):
                if ready.wait(0.1):
                    break
                if process.poll() is not None:
                    raise FeishuError("飞书事件监听启动失败；请检查机器人配置、权限与事件订阅")
            if not ready.is_set():
                raise FeishuError("飞书事件监听未在 20 秒内就绪")
            yield process.stdout
            code = process.poll()
            if code is not None and code != 0:
                raise FeishuError(f"飞书事件监听中断，退出码 {code}")
        finally:
            # Keep stdin open during consumption: EOF stops an unbounded CLI
            # consumer. Close it only when shutting down, then use SIGTERM.
            if process.stdin:
                process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    LOG.warning("飞书事件进程尚未退出，请检查该进程")
            reader.join(timeout=2)
            process.stdout.close()
            if not reader.is_alive():
                process.stderr.close()
