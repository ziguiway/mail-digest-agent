from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, update, func, exists, or_
from sqlalchemy.exc import IntegrityError

from ..domain.errors import AppError
from ..domain.models import (
    Account,
    Delivery,
    Digest,
    DigestGroup,
    Mail,
    Message,
    Receipt,
    Setting,
    Task,
    TaskEvent,
    new_id,
    stamp,
)
from ..domain.schemas import QuestionInput, DigestInput
from ..infrastructure.database import record
from ..infrastructure.lark_cli import (
    LarkCli,
    READY_MARKER,
    authorization_urls,
    cli_error,
    json_documents,
)
from .feishu_format import (
    digest_notification,
    query_notification,
    reply_draft_notification,
    reply_draft_question,
)

TEST_MESSAGE = "来信连接测试：已成功绑定此会话。启用飞书后，你可以在这里查询邮件，并接收每日摘要。"
DRAFT_TTL_MINUTES = 30
DRAFT_EXIT = {"结束", "取消", "退出", "/end"}
DRAFT_SWITCH = {"换一封", "重新选", "换邮件"}
DRAFT_RETRY = {"重试", "再试一次"}
DRAFT_REQUEST = re.compile(
    r"^(?:帮我|请|麻烦)?(?:/reply|起草邮件回复|起草回复|回复草稿|回复)(?=\s|《|“|\"|\[|$)"
)
SUBJECT_PREFIXES = (
    "re:",
    "re：",
    "回复:",
    "回复：",
    "答复:",
    "答复：",
    "fwd:",
    "fw:",
    "转发:",
    "转发：",
)


def draft_key(state):
    identity = f"{state.get('binding_id', '')}:{state.get('chat_id', '')}"
    return "feishu_draft:" + hashlib.sha256(identity.encode()).hexdigest()[:32]


def normalized_subject(value):
    value = " ".join(str(value or "").split()).casefold()
    changed = True
    while changed:
        changed = False
        for prefix in SUBJECT_PREFIXES:
            if value.startswith(prefix):
                value = value[len(prefix) :].strip()
                changed = True
    return value


def split_draft_title(rest):
    for opener, closer in (("《", "》"), ("“", "”"), ('"', '"'), ("[", "]")):
        if not rest.startswith(opener):
            continue
        end = rest.find(closer, len(opener))
        if end < 0:
            return rest[len(opener) :].strip(), ""
        return (
            rest[len(opener) : end].strip(),
            rest[end + len(closer) :].lstrip("：:，,。;； \t\n"),
        )
    return None, ""


def draft_request(text):
    """Return ("request", title, intent), ("command", "", ""), or None."""
    match = DRAFT_REQUEST.match(text)
    if not match:
        return None
    rest = text[match.end() :].strip()
    if not rest:
        return "command", "", ""
    title, intent = split_draft_title(rest)
    if title is None:
        return None
    return "request", title, intent


def draft_time(value, timezone_name):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ZoneInfo(timezone_name)).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, KeyError):
        return ""


def future(minutes=10):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(
        timespec="milliseconds"
    )


def split_text(text, limit=2600):
    parts = []
    while text:
        end = min(limit, len(text))
        if end < len(text):
            boundary = text.rfind("\n", 0, end)
            if boundary > limit // 2:
                end = boundary + 1
        parts.append(text[:end])
        text = text[end:]
    return parts or ["本次没有新增事项。"]


class Feishu:
    def __init__(self, workspace):
        self.w = workspace
        self.cli = LarkCli(workspace.settings)

    def state(self):
        defaults = {
            "revision": 0,
            "binding_id": "",
            "status": "unconfigured",
            "enabled": False,
            "configured": False,
            "urls": [],
        }
        return defaults | self.w.db.get("feishu", {})

    def change(self, identity, *, expected_status=None, **values):
        for _ in range(8):
            current = self.state()
            if current.get("binding_id") != identity:
                raise AppError("飞书绑定已更新，请刷新页面", "feishu_binding_changed", status=409)
            if expected_status is not None:
                allowed = (
                    {expected_status} if isinstance(expected_status, str) else set(expected_status)
                )
                if current.get("status") not in allowed:
                    raise AppError(
                        "飞书绑定状态已改变，这次操作已停止", "feishu_binding_cancelled", status=409
                    )
            updated = current | values | {"revision": current.get("revision", 0) + 1}
            if self.w.db.compare_set("feishu", current.get("revision", 0), updated):
                return updated
        raise AppError("飞书配置正在更新，请稍后重试", "feishu_config_busy", retryable=True)

    def view(self):
        current = self.state()
        listener = self.w.db.get("feishu_listener", {})
        if listener.get("binding_id") != current.get("binding_id"):
            listener = {}
        alive = bool(listener.get("heartbeat_at")) and datetime.fromisoformat(
            listener["heartbeat_at"]
        ) > datetime.now(timezone.utc) - timedelta(seconds=25)
        pairing_code = None
        if (current.get("pairing_expires_at") or "") > stamp() and current.get(
            "encrypted_pairing_code"
        ):
            pairing_code = self.w.secrets.open(current["encrypted_pairing_code"])
        return {
            **{
                key: current.get(key)
                for key in (
                    "binding_id",
                    "revision",
                    "status",
                    "profile",
                    "app_id",
                    "configured",
                    "enabled",
                    "urls",
                    "auth_task_id",
                    "authorization_expires_at",
                    "owner_open_id",
                    "chat_id",
                    "paired_at",
                    "pairing_expires_at",
                    "last_test_at",
                    "error",
                    "error_code",
                )
            },
            "status": "verified"
            if current.get("enabled") and current.get("last_test_at")
            else current.get("status", "unconfigured"),
            "pairing_code": pairing_code,
            "listener": {
                "ready": bool(alive and listener.get("ready")),
                "heartbeat_at": listener.get("heartbeat_at"),
                "error": listener.get("error"),
                "warning": listener.get("warning"),
                "missing_scopes": listener.get("missing_scopes", []),
            },
            "test_message": TEST_MESSAGE,
            "simulated": self.w.settings.mode == "demo",
        }

    def request_binding(self, form):
        if self.w.settings.mode == "demo":
            raise AppError(
                "样例模式不创建或绑定真实飞书应用，请在真实配置环境操作", "demo_external_disabled"
            )
        current = self.state()
        if form.previous_binding_id != current.get("binding_id", ""):
            raise AppError("绑定状态已更新，请刷新后重试", "feishu_binding_changed", status=409)
        if form.mode == "existing" and (not form.app_id or not form.app_secret):
            raise AppError("请填写 App ID 与 App Secret", "feishu_missing_app")
        identity = new_id()
        with self.w.db.session() as session:
            task = self.w.tasks.enqueue_in(
                session,
                "feishu_authorize",
                {"binding_id": identity, "mode": form.mode, "app_id": form.app_id},
                lane="control",
                max_attempts=1,
            )
            value = {
                "revision": current.get("revision", 0) + 1,
                "binding_id": identity,
                "profile": "mail-digest-" + identity[:12],
                "status": "authorizing",
                "configured": False,
                "enabled": False,
                "brand": form.brand,
                "urls": [],
                "auth_task_id": task["id"],
                "authorization_expires_at": future(15),
                "listener_nonce": new_id(),
            }
            if session.get(Setting, "feishu") is not None:
                result = session.execute(
                    update(Setting)
                    .where(
                        Setting.key == "feishu",
                        func.coalesce(Setting.value["revision"].as_integer(), 0)
                        == current.get("revision", 0),
                    )
                    .values(value=value)
                )
                if result.rowcount != 1:
                    raise AppError(
                        "绑定状态已更新，请刷新后重试", "feishu_binding_changed", status=409
                    )
            else:
                session.add(Setting(key="feishu", value=value))
            if form.app_secret:
                session.add(
                    Setting(
                        key="feishu_secret:" + identity,
                        value={"encrypted": self.w.secrets.seal(form.app_secret)},
                    )
                )
        return task

    def disconnect(self):
        current = self.state()
        if not current.get("binding_id"):
            return self.view()
        self.change(
            current["binding_id"],
            status="disconnected",
            configured=False,
            enabled=False,
            urls=[],
            pairing_hash=None,
            encrypted_pairing_code=None,
            pairing_expires_at=None,
            owner_open_id=None,
            chat_id=None,
            last_test_at=None,
            listener_nonce=new_id(),
        )
        self.w.db.delete("feishu_secret:" + current["binding_id"])
        with self.w.db.session() as session:
            task = session.get(Task, current.get("auth_task_id", ""))
            if task and task.status in {"queued", "running"}:
                task.status, task.phase, task.finished_at = "cancelled", "已取消绑定", stamp()
                session.add(TaskEvent(task_id=task.id, kind="result", data={"status": "cancelled"}))
            for delivery in session.scalars(
                select(Delivery).where(
                    Delivery.binding_id == current["binding_id"],
                    Delivery.status.in_(["queued", "failed"]),
                )
            ):
                delivery.status, delivery.error = "paused", "飞书连接已解除，未继续发送"
        return self.view()

    def pair(self):
        state = self.state()
        if not state.get("configured") or not self.view()["listener"]["ready"]:
            raise AppError("请先完成应用配置并等待消息通道就绪", "feishu_listener_not_ready")
        code = "MAIL-" + secrets.token_hex(5).upper()
        self.change(
            state["binding_id"],
            expected_status=state["status"],
            binding_id=new_id(),
            status="pairing",
            enabled=False,
            pairing_hash=hashlib.sha256(code.encode()).hexdigest(),
            encrypted_pairing_code=self.w.secrets.seal(code),
            pairing_created_at=stamp(),
            pairing_expires_at=future(),
            owner_open_id=None,
            chat_id=None,
            paired_at=None,
            last_test_at=None,
        )
        self.listener_state(self.state()["binding_id"], ready=True)
        return self.view()

    def set_enabled(self, enabled, binding_id):
        state = self.state()
        if binding_id != state.get("binding_id"):
            raise AppError("绑定已改变，请刷新后操作", "feishu_binding_changed", status=409)
        if enabled and (
            not state.get("configured")
            or not state.get("paired_at")
            or not state.get("last_test_at")
            or not self.view()["listener"]["ready"]
        ):
            raise AppError("请先完成配对、发送测试并等待消息通道就绪", "feishu_not_verified")
        self.change(
            binding_id, expected_status=state["status"] if enabled else None, enabled=enabled
        )
        return self.view()

    def reconnect(self):
        state = self.state()
        if not state.get("configured"):
            raise AppError("请先绑定飞书应用", "feishu_not_configured")
        self.change(state["binding_id"], listener_nonce=new_id())
        return self.view()

    async def add_url(self, binding_id, url):
        if not url.startswith(("https://", "http://")) or any(c.isspace() for c in url):
            return
        state = self.state()
        if state.get("binding_id") != binding_id or state.get("status") in {
            "unconfigured",
            "disconnected",
            "cancelled",
            "expired",
        }:
            return
        if url in [item["url"] for item in state.get("urls", [])]:
            return
        qr_id = await self.cli.qrcode(state["profile"], url)
        state = self.state()
        if state.get("binding_id") == binding_id and state.get("status") not in {
            "unconfigured",
            "disconnected",
            "cancelled",
            "expired",
        }:
            self.change(
                binding_id, urls=(state.get("urls", []) + [{"url": url, "qr_id": qr_id}])[-5:]
            )

    async def authorize(self, task):
        identity = task["payload"]["binding_id"]
        state = self.state()
        if state.get("binding_id") != identity or state.get("status") not in {
            "authorizing",
            "error",
            "expired",
        }:
            raise AppError("这次飞书授权已取消或被替换", "feishu_binding_cancelled")
        profile = state["profile"]
        process = None
        readers = []
        output = []
        try:
            # A recovered task first inspects the same profile, preventing a
            # second registration after config was saved before a crash.
            existing = None
            self.change(
                identity,
                expected_status={"authorizing", "error", "expired"},
                status="authorizing",
                error=None,
                error_code=None,
            )
            with suppress(AppError):
                existing = await self.cli.run(profile, "config", "show", local=True)
            if not existing:
                args = [
                    "config",
                    "init",
                    "--name",
                    profile,
                    "--brand",
                    state.get("brand", "feishu"),
                    "--lang",
                    "zh",
                ]
                if task["payload"]["mode"] == "existing":
                    args += ["--app-id", task["payload"]["app_id"], "--app-secret-stdin"]
                else:
                    args += ["--new"]
                self.change(identity, urls=[], authorization_expires_at=future(15))
                await self.w.progress(task["id"], "等待在飞书中完成应用授权")
                process = await self.cli.spawn(profile, *args)
                if task["payload"]["mode"] == "existing":
                    secret = self.w.db.get("feishu_secret:" + identity, {})
                    if not secret.get("encrypted"):
                        raise AppError("App Secret 已清除，请重新绑定", "feishu_missing_app")
                    process.stdin.write((self.w.secrets.open(secret["encrypted"]) + "\n").encode())
                    await process.stdin.drain()
                    process.stdin.close()

                async def read(pipe):
                    while line := await pipe.readline():
                        text = line.decode(errors="replace")
                        output.append(text)
                        if len(output) > 500:
                            del output[:100]
                        for url in authorization_urls(text):
                            await self.add_url(identity, url)

                readers = [
                    asyncio.create_task(read(process.stdout)),
                    asyncio.create_task(read(process.stderr)),
                ]
                deadline = time.monotonic() + 900
                while process.returncode is None:
                    current = self.state()
                    if (
                        current.get("binding_id") != identity
                        or current.get("status") != "authorizing"
                    ):
                        raise AppError("飞书授权已取消", "feishu_binding_cancelled")
                    for reader in readers:
                        if reader.done() and reader.exception():
                            raise reader.exception()
                    if time.monotonic() >= deadline:
                        raise AppError(
                            "飞书授权已过期，请重新发起绑定", "feishu_authorization_expired"
                        )
                    await asyncio.sleep(0.2)
                await asyncio.gather(*readers)
                documents = list(json_documents("".join(output)))
                if process.returncode != 0 or any(doc.get("ok") is False for doc in documents):
                    raise cli_error(documents)
                existing = await self.cli.run(profile, "config", "show", local=True)
            app_id = existing.get("appId") or existing.get("app_id")
            if not app_id:
                raise AppError("CLI 未返回应用标识，请重新检查配置", "feishu_contract_error")
            self.change(
                identity,
                expected_status="authorizing",
                configured=True,
                app_id=app_id,
                status="permissions_required",
                urls=[],
                error=None,
                error_code=None,
                authorization_expires_at=None,
            )
            self.w.db.delete("feishu_secret:" + identity)
            return {"binding_id": identity, "app_id": app_id}
        except AppError as exc:
            if (
                self.state().get("binding_id") == identity
                and self.state().get("status") == "authorizing"
            ):
                with suppress(AppError):
                    self.change(identity, expected_status="authorizing", urls=[])
                for url in authorization_urls(json.dumps(getattr(exc, "details", {}))):
                    await self.add_url(identity, url)
                with suppress(AppError):
                    self.change(
                        identity,
                        expected_status="authorizing",
                        status="expired" if "expired" in exc.code else "error",
                        error=exc.message,
                        error_code=exc.code,
                    )
            raise
        finally:
            if process:
                await self.cli.finish(process)
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    def queue_delivery(self, kind, source_id, text, *, reply_to="", binding_id=None):
        state = self.state()
        if (
            not state.get("configured")
            or not state.get("paired_at")
            or (binding_id and state["binding_id"] != binding_id)
        ):
            raise AppError("飞书尚未配对，消息保留在网页中", "feishu_not_paired")
        if kind != "test" and not state.get("enabled"):
            raise AppError("飞书收发尚未启用", "feishu_disabled")
        try:
            with self.w.db.session() as session:
                delivery = session.scalar(
                    select(Delivery).where(
                        Delivery.binding_id == state["binding_id"],
                        Delivery.kind == kind,
                        Delivery.source_id == source_id,
                    )
                )
                if delivery is None:
                    delivery = Delivery(
                        binding_id=state["binding_id"],
                        kind=kind,
                        source_id=source_id,
                        recipient_chat_id=state["chat_id"],
                        reply_to=reply_to,
                        text=text,
                    )
                    session.add(delivery)
                    session.flush()
                task = self.w.tasks.enqueue_in(
                    session,
                    "feishu_send",
                    {"delivery_id": delivery.id},
                    lane="delivery",
                    key="delivery:" + delivery.id,
                )
                if kind == "digest":
                    digest = session.get(Digest, source_id)
                    digest.delivery_status = "pending" if delivery.status != "sent" else "sent"
                return task
        except IntegrityError:
            # Only a single Worker sends; a concurrent API test request can
            # race the unique source constraint, so reuse the durable row.
            with self.w.db.session() as session:
                existing = session.scalar(
                    select(Delivery).where(
                        Delivery.binding_id == state["binding_id"],
                        Delivery.kind == kind,
                        Delivery.source_id == source_id,
                    )
                )
                if existing is None:
                    raise
                return record(
                    session.scalar(
                        select(Task).where(Task.idempotency_key == "delivery:" + existing.id)
                    )
                )

    def queue_digest(
        self, digest_id, *, reply_to="", binding_id=None, range_start=None, range_end=None
    ):
        with self.w.db.session() as session:
            digest = session.get(Digest, digest_id)
            if not digest:
                raise AppError("摘要不存在", "digest_not_found", status=404)
            if not digest.items:
                raise AppError("摘要范围内没有邮件，无需推送", "empty_digest")
            group = session.get(DigestGroup, digest.group_id) if digest.group_id else None
            if group and group.latest_digest_id == digest.id and group.checked_at:
                range_start = range_start or group.checked_start
                range_end = range_end or group.checked_end
            range_start, range_end = (
                range_start or digest.range_start,
                range_end or digest.range_end,
            )
            sources = {m["id"]: m for m in digest.sources}
            for item in digest.items:
                if item["mail_id"] not in sources:
                    mail = session.get(Mail, item["mail_id"])
                    if mail:
                        account = session.get(Account, mail.account_id)
                        sources[mail.id] = record(mail) | {
                            "account_label": account.label if account else "邮箱"
                        }
            text = digest_notification(
                record(digest),
                [sources[item["mail_id"]] for item in digest.items if item["mail_id"] in sources],
                range_start=range_start,
                range_end=range_end,
            )
        return self.queue_delivery(
            "digest", digest_id, text, reply_to=reply_to, binding_id=binding_id
        )

    def queue_answer(self, message_id, context):
        with self.w.db.session() as session:
            message = session.get(Message, message_id)
            if not message:
                raise AppError("查询回答不存在", "message_not_found", status=404)
            text = query_notification(message.content, message.presentation, message.sources)
        return self.queue_delivery(
            "query",
            message_id,
            text,
            reply_to=context["message_id"],
            binding_id=context["binding_id"],
        )

    def queue_reply_draft(self, task_id, result, context):
        text = (
            reply_draft_question(result)
            if result.get("clarification") and not result.get("body")
            else reply_draft_notification(result)
        )
        return self.queue_delivery(
            "reply_draft",
            task_id,
            text,
            reply_to=context["message_id"],
            binding_id=context["binding_id"],
        )

    def queue_request_ack(self, task):
        context = task["payload"]["feishu"]
        if task["kind"] == "reply_draft":
            text = task["payload"].get("ack") or "正在起草回复，请稍候。"
        else:
            activity = (
                "同步邮件并生成摘要" if task["kind"] == "digest" else "检索相关邮件并生成回答"
            )
            text = f"已收到，正在{activity}，请稍候。"
        return self.queue_delivery(
            "request_ack",
            task["id"],
            text,
            reply_to=context["message_id"],
            binding_id=context["binding_id"],
        )

    def draft_session(self):
        state = self.state()
        if not state.get("paired_at") or not state.get("chat_id"):
            return None
        value = self.w.db.get(draft_key(state))
        if not isinstance(value, dict):
            return None
        if (value.get("expires_at") or "") <= stamp():
            self.w.db.delete(draft_key(state))
            return None
        return value

    def save_draft_session(self, value):
        saved = value | {
            "updated_at": stamp(),
            "expires_at": future(DRAFT_TTL_MINUTES),
        }
        self.w.db.set(draft_key(self.state()), saved)
        return saved

    def clear_draft_session(self):
        state = self.state()
        if state.get("binding_id"):
            self.w.db.delete(draft_key(state))

    def draft_reply(self, event, kind, text):
        return self.queue_delivery(
            kind,
            event["message_id"],
            text,
            reply_to=event["message_id"],
            binding_id=self.state()["binding_id"],
        )

    def refresh_draft(self, session):
        task_id = session.get("task_id")
        if not task_id:
            return session
        with self.w.db.session() as db:
            task = db.get(Task, task_id)
            if not task:
                return session | {"stage": "failed", "error": "任务记录已不可用"}
            status, result, error = task.status, task.result or {}, task.error
        if status == "succeeded":
            if result.get("body"):
                return session | {
                    "stage": "review",
                    "previous": {
                        "subject": result.get("subject", ""),
                        "body": result["body"],
                    },
                    "clarification": "",
                }
            if result.get("clarification"):
                return session | {
                    "stage": "intent",
                    "clarification": result["clarification"],
                    "previous": None,
                }
            return session | {"stage": "failed", "error": "草稿内容为空"}
        if status == "failed":
            return session | {"stage": "failed", "error": error or "服务暂时不可用"}
        return session

    def draft_candidate(self, mail):
        timezone_name = (self.w.db.get("schedule", {}) or {}).get("timezone", "Asia/Shanghai")
        return {
            "id": mail["id"],
            "subject": mail.get("subject") or "（无主题）",
            "sender": mail.get("sender") or "",
            "account_label": mail.get("account_label") or "",
            "time": draft_time(mail.get("received_at") or mail.get("sent_at"), timezone_name),
        }

    @staticmethod
    def candidate_index(text, size):
        match = re.fullmatch(r"第?\s*([1-9])\s*封?", text.strip())
        if not match:
            return None
        index = int(match.group(1)) - 1
        return index if index < size else None

    def receive_draft(self, event, key):
        with self.w.db.session() as db:
            if db.get(Receipt, key):
                return True
        handled = self._receive_draft(event, key)
        if handled:
            with self.w.db.session() as db:
                if db.get(Receipt, key) is None:
                    db.add(Receipt(key=key, data={"action": "draft"}))
        return handled

    def _receive_draft(self, event, key):
        text = event["content"].strip()
        session = self.draft_session()
        if session:
            session = self.refresh_draft(session)
        request = draft_request(text)
        if request and request[0] == "request":
            return self.start_draft(event, key, request[1], request[2])
        if not session:
            if request:
                self.save_draft_session(
                    {
                        "stage": "choose",
                        "candidates": [],
                        "mail": {},
                        "mail_id": "",
                        "intent": "",
                    }
                )
                self.draft_reply(
                    event,
                    "draft_status",
                    "要回复哪封邮件？发我邮件标题，或直接发「回复《邮件标题》：回复要点」。",
                )
                return True
            return False
        if text in DRAFT_EXIT:
            self.clear_draft_session()
            self.draft_reply(event, "draft_status", "已结束起草。")
            return True
        if text in DRAFT_SWITCH:
            self.save_draft_session(
                {"stage": "choose", "candidates": [], "mail": {}, "mail_id": "", "intent": ""}
            )
            self.draft_reply(event, "draft_status", "要回复哪封邮件？发我新的邮件标题。")
            return True
        stage = session.get("stage")
        if stage == "drafting":
            self.draft_reply(event, "draft_status", "草稿还在生成中，请稍候。")
            return True
        if stage == "failed":
            if text in DRAFT_RETRY:
                return self.enqueue_draft(event, key, session, session.get("intent", ""))
            self.draft_reply(
                event,
                "draft_status",
                f"上次草稿没有生成成功：{session.get('error') or '服务暂时不可用'}。"
                "回复「重试」再试一次，或回复「结束」退出。",
            )
            return True
        if stage == "choose":
            candidates = session.get("candidates") or []
            if not candidates:
                return self.start_draft(event, key, text.strip('《》“”"[] '), "")
            index = self.candidate_index(text, len(candidates))
            if index is None:
                self.draft_reply(
                    event,
                    "draft_status",
                    f"请回复 1–{len(candidates)} 的序号，或回复「取消」退出。",
                )
                return True
            return self.select_candidate(event, key, session, candidates[index])
        if stage == "intent":
            return self.submit_intent(event, key, session, text)
        if stage == "review":
            if text.startswith("/"):
                return False
            return self.enqueue_draft(
                event,
                key,
                session,
                session.get("intent", ""),
                revision=text,
                previous=session.get("previous"),
            )
        return False

    def start_draft(self, event, key, title, intent):
        title = " ".join(title.split())
        if len(title) < 2:
            self.draft_reply(
                event,
                "draft_status",
                "请用《》写清邮件标题，例如「回复《课题组会议时间更新》：确认参会」。",
            )
            return True
        if len(title) > 100:
            self.draft_reply(event, "draft_status", "标题关键词太长了，请控制在 100 字以内。")
            return True
        try:
            result = self.w.mailboxes.search(keywords=[title], limit=10, cache_state="all")
        except AppError as exc:
            self.draft_reply(event, "draft_status", f"读取邮件缓存失败：{exc.message}")
            return True
        wanted = normalized_subject(title)
        mails = [
            mail
            for mail in result["mails"]
            if wanted and wanted in normalized_subject(mail.get("subject"))
        ]
        if not mails:
            self.save_draft_session(
                {"stage": "choose", "candidates": [], "mail": {}, "mail_id": "", "intent": ""}
            )
            self.draft_reply(
                event,
                "draft_status",
                f"没有找到标题包含「{title}」的邮件。可以换一个更短的标题关键词，"
                "或先在网页同步邮箱。",
            )
            return True
        if len(mails) == 1:
            return self.select_candidate(
                event, key, {"intent": intent or ""}, self.draft_candidate(mails[0])
            )
        candidates = [self.draft_candidate(mail) for mail in mails[:3]]
        self.save_draft_session(
            {
                "stage": "choose",
                "candidates": candidates,
                "mail": {},
                "mail_id": "",
                "intent": intent or "",
            }
        )
        lines = [f"找到 {len(mails)} 封标题包含「{title}」的邮件"]
        if len(mails) > len(candidates):
            lines[0] += f"，先列出最近 {len(candidates)} 封"
        lines[0] += "，回复序号继续："
        for index, candidate in enumerate(candidates, 1):
            meta = " · ".join(
                value
                for value in (
                    candidate["sender"],
                    candidate["account_label"],
                    candidate["time"],
                )
                if value
            )
            lines.append(f"{index}. 《{candidate['subject']}》" + (f" · {meta}" if meta else ""))
        lines.append("回复「1」继续；回复「取消」退出。")
        self.draft_reply(event, "draft_choice", "\n".join(lines))
        return True

    def select_candidate(self, event, key, session, candidate):
        chosen = {
            "stage": "intent",
            "candidates": [],
            "mail": candidate,
            "mail_id": candidate["id"],
            "intent": session.get("intent", ""),
        }
        if chosen["intent"]:
            return self.enqueue_draft(event, key, chosen, chosen["intent"])
        self.save_draft_session(chosen)
        self.draft_reply(
            event,
            "draft_status",
            f"已选择《{candidate['subject']}》。想怎么回？直接发我回复要点，"
            "例如「确认参会，询问是否需要提前发 PPT」。\n回复「换一封」或「结束」退出。",
        )
        return True

    def submit_intent(self, event, key, session, text):
        text = " ".join(text.split())
        if not text:
            self.draft_reply(event, "draft_status", "请发我回复要点。")
            return True
        if session.get("clarification"):
            intent = (session.get("intent", "") + "\n补充说明：" + text).strip()
        else:
            intent = text
        return self.enqueue_draft(event, key, session, intent)

    def enqueue_draft(self, event, key, session, intent, *, revision="", previous=None):
        intent = intent.strip()
        if not intent:
            self.draft_reply(event, "draft_status", "请先发我回复要点。")
            return True
        if len(intent) > 2000:
            self.draft_reply(event, "draft_status", "回复要点太长了，请精简到 2000 字以内。")
            return True
        subject = (session.get("mail") or {}).get("subject") or "原邮件"
        ack = (
            "正在按你的修改要求重新起草，请稍候。"
            if revision
            else f"已找到《{subject}》，正在起草回复，请稍候。"
        )
        task = self.w.tasks.enqueue(
            "reply_draft",
            {
                "mail_id": session["mail_id"],
                "intent": intent,
                "revision": revision,
                "previous": previous,
                "ack": ack,
                "feishu": {
                    "binding_id": self.state()["binding_id"],
                    "message_id": event["message_id"],
                    "notify": True,
                },
            },
            lane="interactive",
            key=key,
        )
        self.save_draft_session(
            session
            | {
                "stage": "drafting",
                "task_id": task["id"],
                "intent": intent,
                "previous": previous,
                "clarification": "",
            }
        )
        self.queue_request_ack(task)
        return True

    def reconcile_notifications(self):
        """Recover acknowledgement/failure intents after crashes without duplicate replies."""
        state = self.state()
        if not state.get("enabled") or not state.get("paired_at"):
            return

        def has_delivery(kind):
            return exists(
                select(Delivery.id).where(
                    Delivery.source_id == Task.id,
                    Delivery.kind == kind,
                    Delivery.binding_id == state["binding_id"],
                )
            )

        with self.w.db.session() as session:
            tasks = [
                record(task)
                for task in session.scalars(
                    select(Task)
                    .where(
                        Task.kind.in_(["query", "digest", "reply_draft"]),
                        Task.payload["feishu"]["notify"].as_boolean().is_(True),
                        Task.payload["feishu"]["binding_id"].as_string() == state["binding_id"],
                        or_(
                            Task.status.in_(["queued", "running"]) & ~has_delivery("request_ack"),
                            (Task.status == "failed") & ~has_delivery("request_error"),
                        ),
                    )
                    .order_by(Task.created_at)
                    .limit(20)
                )
            ]
        for task in tasks:
            try:
                if task["status"] != "failed":
                    self.queue_request_ack(task)
                    continue
                context = task["payload"]["feishu"]
                title = {
                    "digest": "邮件摘要未完成",
                    "query": "邮件查询未完成",
                    "reply_draft": "回复草稿未完成",
                }[task["kind"]]
                hint = (
                    "回复「重试」可以再试一次，或在网页“任务记录”查看详情。"
                    if task["kind"] == "reply_draft"
                    else "可稍后重发这条问题，或在网页“任务记录”查看详情。"
                )
                self.queue_delivery(
                    "request_error",
                    task["id"],
                    f"{title}：{task['error'] or '服务暂时不可用，请稍后重试'}。\n" + hint,
                    reply_to=context["message_id"],
                    binding_id=context["binding_id"],
                )
            except AppError:
                # A disconnect/re-pair can race this scan. Keep the intent in
                # the task; its binding must never be replaced with a new one.
                continue

    def test_send(self, binding_id):
        if not self.view()["listener"]["ready"]:
            raise AppError("请等待消息通道就绪后测试", "feishu_listener_not_ready")
        return self.queue_delivery("test", new_id(), TEST_MESSAGE, binding_id=binding_id)

    def delivery_status(self, session, delivery, status, error=None):
        delivery.status, delivery.error, delivery.updated_at = status, error, stamp()
        if delivery.kind == "digest":
            digest = session.get(Digest, delivery.source_id)
            if digest:
                digest.delivery_status = status

    async def send(self, task):
        with self.w.db.session() as session:
            row = session.get(Delivery, task["payload"]["delivery_id"])
            if not row:
                raise AppError("发送记录不存在", "feishu_delivery_missing")
            delivery = record(row)
        state = self.state()
        if delivery["status"] == "sent":
            return {"delivery_id": delivery["id"], "status": "sent"}
        if (
            not state.get("configured")
            or not state.get("paired_at")
            or state.get("binding_id") != delivery["binding_id"]
            or state.get("chat_id") != delivery["recipient_chat_id"]
            or (delivery["kind"] != "test" and not state.get("enabled"))
        ):
            with self.w.db.session() as session:
                self.delivery_status(
                    session,
                    session.get(Delivery, delivery["id"]),
                    "paused",
                    "连接或接收对象已变化，未继续发送",
                )
            raise AppError("连接或接收对象已变化，发送已暂停", "feishu_delivery_paused")
        chunks = list(delivery["chunks"])
        if any(chunk.get("status") in {"sending", "uncertain"} for chunk in chunks):
            with self.w.db.session() as session:
                self.delivery_status(
                    session,
                    session.get(Delivery, delivery["id"]),
                    "uncertain",
                    "上一次请求可能已送达，请核对飞书后显式重试",
                )
            raise AppError(
                "上一次请求的发送结果不确定，请先核对飞书再重试", "feishu_send_uncertain"
            )
        parts = split_text(delivery["text"])
        for index, part in enumerate(parts):
            if index < len(chunks) and chunks[index].get("status") == "sent":
                continue
            current = self.state()
            if (
                current.get("binding_id") != delivery["binding_id"]
                or not current.get("configured")
                or not current.get("paired_at")
                or current.get("chat_id") != delivery["recipient_chat_id"]
                or (delivery["kind"] != "test" and not current.get("enabled"))
            ):
                with self.w.db.session() as session:
                    self.delivery_status(
                        session,
                        session.get(Delivery, delivery["id"]),
                        "paused",
                        "飞书连接已改变，后续分段未发送",
                    )
                raise AppError("飞书连接已改变，后续分段未发送", "feishu_delivery_paused")
            key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"mail-delivery:{delivery['id']}:{index}"))
            chunk = {"index": index, "key": key, "status": "sending"}
            if index == len(chunks):
                chunks.append(chunk)
            else:
                chunks[index] = chunk
            with self.w.db.session() as session:
                row = session.get(Delivery, delivery["id"])
                row.chunks = list(chunks)
                self.delivery_status(session, row, "sending")
            if len(parts) > 1:
                part = f"（{index + 1}/{len(parts)}）\n" + part
            content = json.dumps(
                {"zh_cn": {"content": [[{"tag": "text", "text": part}]]}}, ensure_ascii=False
            )
            args = (
                ["im", "+messages-reply", "--message-id", delivery["reply_to"]]
                if delivery["reply_to"]
                else ["im", "+messages-send", "--chat-id", delivery["recipient_chat_id"]]
            )
            args += [
                "--as",
                "bot",
                "--msg-type",
                "post",
                "--content",
                "-",
                "--idempotency-key",
                key,
                "--format",
                "json",
            ]
            try:
                receipt = await self.cli.run(
                    state["profile"], *args, input=content, sending=True, timeout=60
                )
                if not receipt.get("message_id"):
                    error = AppError("飞书未返回消息回执，请核对后再重试", "feishu_send_uncertain")
                    error.uncertain = True
                    raise error
                if receipt.get("chat_id") and receipt["chat_id"] != delivery["recipient_chat_id"]:
                    error = AppError(
                        "发送回执的会话与配对对象不一致，请核对后重试", "feishu_send_uncertain"
                    )
                    error.uncertain = True
                    raise error
                chunks[index] = chunk | {
                    "status": "sent",
                    "message_id": receipt["message_id"],
                    "sent_at": stamp(),
                }
                with self.w.db.session() as session:
                    session.get(Delivery, delivery["id"]).chunks = list(chunks)
            except AppError as exc:
                uncertain = getattr(exc, "uncertain", False)
                chunks[index] = chunk | {"status": "uncertain" if uncertain else "failed"}
                with self.w.db.session() as session:
                    row = session.get(Delivery, delivery["id"])
                    row.chunks = list(chunks)
                    self.delivery_status(
                        session, row, "uncertain" if uncertain else "failed", exc.message
                    )
                for url in authorization_urls(json.dumps(getattr(exc, "details", {}))):
                    await self.add_url(delivery["binding_id"], url)
                raise
        with self.w.db.session() as session:
            self.delivery_status(session, session.get(Delivery, delivery["id"]), "sent")
        current = self.state()
        if (
            delivery["kind"] == "test"
            and current.get("binding_id") == delivery["binding_id"]
            and current.get("configured")
            and current.get("chat_id") == delivery["recipient_chat_id"]
        ):
            self.change(
                delivery["binding_id"],
                expected_status={"paired", "verified"},
                last_test_at=stamp(),
                error=None,
                error_code=None,
            )
        return {"delivery_id": delivery["id"], "status": "sent"}

    def retry_delivery(self, delivery_id, allow_uncertain=False):
        with self.w.db.session() as session:
            delivery = session.get(Delivery, delivery_id)
            if not delivery or delivery.status not in {"failed", "uncertain", "paused"}:
                raise AppError("这条发送记录无需重试", "feishu_delivery_not_retryable")
            if delivery.status == "uncertain" and not allow_uncertain:
                raise AppError(
                    "请核对飞书后明确允许重试，重复发送仍有可能发生",
                    "feishu_uncertain_confirmation",
                )
            task = session.scalar(
                select(Task).where(Task.idempotency_key == "delivery:" + delivery_id)
            )
            if not task or task.status != "failed":
                raise AppError("发送任务仍在处理中，请等待自动重试", "feishu_delivery_busy")
            if allow_uncertain:
                delivery.chunks = [
                    chunk | {"status": "failed"}
                    if chunk.get("status") in {"sending", "uncertain"}
                    else chunk
                    for chunk in delivery.chunks
                ]
            self.delivery_status(session, delivery, "queued")
            task.status, task.phase, task.attempts, task.available_at = (
                "queued",
                "等待重试",
                0,
                stamp(),
            )
            task.error, task.error_code, task.finished_at = None, None, None
            session.add(TaskEvent(task_id=task.id, kind="stage", data={"phase": "等待重试"}))
            return record(task)

    def deliveries(self):
        with self.w.db.session() as session:
            rows = []
            for delivery in session.scalars(
                select(Delivery).order_by(Delivery.created_at.desc()).limit(100)
            ):
                task = session.scalar(
                    select(Task).where(Task.idempotency_key == "delivery:" + delivery.id)
                )
                rows.append(
                    record(delivery)
                    | {
                        "task_id": task.id if task else None,
                        "task_status": task.status if task else None,
                    }
                )
            return rows

    def receive(self, event):
        state = self.state()
        if (
            not state.get("configured")
            or event.get("sender_type") != "user"
            or event.get("chat_type") != "p2p"
            or event.get("message_type") not in {"text", "post"}
        ):
            return
        if not all(
            isinstance(event.get(key), str) and event[key]
            for key in ("sender_id", "chat_id", "message_id", "content")
        ):
            return
        text = event["content"].strip()
        if not text or len(text) > 4000:
            return
        if state.get("pairing_hash") and (state.get("pairing_expires_at") or "") > stamp():
            code_hash = hashlib.sha256(text.upper().encode()).hexdigest()
            try:
                sent_at = datetime.fromtimestamp(
                    int(event.get("create_time", "0")) / 1000, timezone.utc
                )
            except (TypeError, ValueError, OverflowError):
                return
            if hmac.compare_digest(
                state["pairing_hash"], code_hash
            ) and sent_at >= datetime.fromisoformat(state["pairing_created_at"]) - timedelta(
                seconds=1
            ):
                self.change(
                    state["binding_id"],
                    expected_status="pairing",
                    status="paired",
                    owner_open_id=event["sender_id"],
                    chat_id=event["chat_id"],
                    paired_at=stamp(),
                    pairing_hash=None,
                    encrypted_pairing_code=None,
                    pairing_expires_at=None,
                )
                return
        if (
            not state.get("enabled")
            or event["sender_id"] != state.get("owner_open_id")
            or event["chat_id"] != state.get("chat_id")
        ):
            return
        key = f"feishu-event:{state['binding_id']}:{event['message_id']}"
        if text in {"/digest", "现在汇总一下", "立即汇总"}:
            task = self.w.digests.enqueue(
                DigestInput(),
                extra={
                    "deliver": True,
                    "feishu": {
                        "binding_id": state["binding_id"],
                        "message_id": event["message_id"],
                        "notify": True,
                    },
                },
                key=key,
            )
            self.queue_request_ack(task)
            return
        conversation_key = f"feishu-conversation:{state['binding_id']}:{event['chat_id']}"
        if text in {"/new", "新建对话"}:
            with self.w.db.session() as session:
                if session.get(Receipt, key):
                    return
                row = session.get(Setting, conversation_key)
                if row:
                    session.delete(row)
                session.add(Receipt(key=key, data={"action": "new_conversation"}))
            self.clear_draft_session()
            return
        if self.receive_draft(event, key):
            return
        task = self.w.questions.submit(
            QuestionInput(question=text),
            channel="feishu",
            request_key=key,
            conversation_key=conversation_key,
            context={
                "feishu": {
                    "binding_id": state["binding_id"],
                    "message_id": event["message_id"],
                    "notify": True,
                }
            },
        )
        self.queue_request_ack(task)

    def listener_state(self, binding_id, **values):
        self.w.db.set(
            "feishu_listener", {"binding_id": binding_id, "heartbeat_at": stamp(), **values}
        )

    async def listen_once(self, state, stop):
        identity, profile = state["binding_id"], state["profile"]
        process = await self.cli.spawn(
            profile, "event", "consume", "im.message.receive_v1", "--as", "bot"
        )
        queue = asyncio.Queue(maxsize=200)
        stderr, buffered = [], []
        ready, started, heartbeat = False, time.monotonic(), 0
        warning = None

        async def read(pipe, source):
            while line := await pipe.readline():
                await queue.put((source, line.decode(errors="replace").strip()))

        readers = [
            asyncio.create_task(read(process.stdout, "stdout")),
            asyncio.create_task(read(process.stderr, "stderr")),
        ]
        try:
            self.listener_state(identity, ready=False)
            while not stop.is_set():
                current = self.state()
                if (
                    current.get("profile") != profile
                    or not current.get("configured")
                    or current.get("listener_nonce") != state.get("listener_nonce")
                ):
                    return
                if current["binding_id"] != identity:
                    identity, heartbeat = current["binding_id"], 0
                if not ready and time.monotonic() - started > 20:
                    raise AppError(
                        "消息通道未在 20 秒内就绪，请检查机器人权限、事件订阅和应用发布",
                        "feishu_listener_timeout",
                        retryable=True,
                    )
                for reader in readers:
                    if reader.done() and reader.exception():
                        raise reader.exception()
                try:
                    source, text = await asyncio.wait_for(queue.get(), 0.5)
                except TimeoutError:
                    if process.returncode is not None:
                        raise cli_error(list(json_documents("\n".join(stderr))))
                    source, text = "", ""
                if source == "stderr":
                    stderr.append(text)
                    stderr = stderr[-100:]
                    if text.startswith(READY_MARKER):
                        ready = True
                        self.listener_state(identity, ready=True)
                        if current.get("status") == "permissions_required":
                            self.change(
                                identity, expected_status="permissions_required", status="ready"
                            )
                        for event in buffered:
                            self.receive(event)
                        buffered.clear()
                    elif "WARN" in text:
                        warning = "消息通道返回完整性提示，连接恢复后请核对并重发未得到回答的问题"
                        self.listener_state(
                            identity,
                            ready=ready,
                            warning="消息通道返回完整性提示，连接恢复后请核对并重发未得到回答的问题",
                        )
                if source == "stdout":
                    try:
                        event = json.loads(text)
                    except ValueError:
                        raise AppError(
                            "消息通道返回了无法识别的事件，请重连", "feishu_event_invalid"
                        ) from None
                    if ready:
                        self.receive(event)
                    elif len(buffered) < 100:
                        buffered.append(event)
                    else:
                        raise AppError(
                            "通道就绪前积压过多消息，请重连并重发问题", "feishu_event_overflow"
                        )
                if ready and time.monotonic() - heartbeat > 5:
                    self.listener_state(identity, ready=True, warning=warning)
                    heartbeat = time.monotonic()
        finally:
            await self.cli.finish(process)
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            self.listener_state(identity, ready=False)
            # Scope is the dedicated config directory and explicit profile.
            # Do not use --all or --force (which can kill a shared bus).
            with suppress(AppError):
                await self.cli.run(profile, "event", "stop", "--json", local=True, timeout=10)

    async def supervise(self, stop):
        backoff = 1
        while not stop.is_set():
            state = self.state()
            if not state.get("configured") or self.w.settings.mode == "demo":
                try:
                    await asyncio.wait_for(stop.wait(), 0.5)
                except TimeoutError:
                    pass
                continue
            try:
                await self.listen_once(state, stop)
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                exc = (
                    error
                    if isinstance(error, AppError)
                    else AppError(
                        "飞书通道中断，请重连后重发未得到回答的问题",
                        "feishu_listener_error",
                        retryable=True,
                    )
                )
                details = getattr(exc, "details", {})
                for url in authorization_urls(json.dumps(details)):
                    with suppress(AppError):
                        await self.add_url(state["binding_id"], url)
                self.listener_state(
                    state["binding_id"],
                    ready=False,
                    error=exc.message,
                    missing_scopes=details.get("missing_scopes", []),
                )
                deadline = time.monotonic() + backoff
                while not stop.is_set() and time.monotonic() < deadline:
                    if (
                        self.state().get("listener_nonce") != state.get("listener_nonce")
                        or self.state().get("binding_id") != state["binding_id"]
                    ):
                        break
                    await asyncio.sleep(0.2)
                backoff = min(backoff * 2, 30)
