from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agently import TriggerFlow
from pydantic import TypeAdapter
from sqlalchemy import func, select

from ..agents import prompts
from ..domain.errors import AppError
from ..domain.models import Digest, DigestGroup, Mail, MailAnalysis, Receipt, Task, stamp
from ..domain.schemas import DigestInput, DigestItem
from ..infrastructure.database import record
from .mailboxes import active_folder_scope, normalize_time


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Digests:
    """A refresh checks a frozen range; a digest revision never consumes mail."""

    def __init__(self, workspace):
        self.w, self.db = workspace, workspace.db
        self.lock_dir = workspace.settings.data_dir / "digest-locks"
        self.lock_dir.mkdir(exist_ok=True, mode=0o700)

    @contextmanager
    def queue_lock(self, key):
        with (self.lock_dir / (key + ".queue")).open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @asynccontextmanager
    async def generation_lock(self, key):
        with (self.lock_dir / (key + ".generation")).open("a") as handle:
            try:
                while True:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        await asyncio.sleep(0.05)
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def freeze(self, payload, cutoff=None):
        selected = self.w.mailboxes.selected(
            payload.get("account_ids") or self.db.get("schedule", {}).get("account_ids") or None
        )
        if not selected:
            raise AppError("请先添加并启用邮箱", "no_accounts")
        end = cutoff or datetime.now(timezone.utc)
        end = end.astimezone(timezone.utc)
        zone = self.db.get("schedule", {}).get("timezone", "Asia/Shanghai")
        scope = sorted(
            [{"id": a["id"], "folder": a["folder"], "revision": a["revision"]} for a in selected],
            key=lambda a: a["id"],
        )
        group_id = fingerprint(
            {
                "day": end.astimezone(ZoneInfo(zone)).date().isoformat(),
                "timezone": zone,
                "scope": [{"id": a["id"], "folder": a["folder"]} for a in scope],
            }
        )
        return payload | {
            "account_ids": [a["id"] for a in scope],
            "scope": scope,
            "range_start": normalize_time((end - timedelta(hours=24)).isoformat()),
            "range_end": normalize_time(end.isoformat()),
            "timezone": zone,
            "day": end.astimezone(ZoneInfo(zone)).date().isoformat(),
            "group_id": group_id,
        }

    def ensure_group(self, session, payload):
        group = session.get(DigestGroup, payload["group_id"])
        if group is None:
            group = DigestGroup(
                id=payload["group_id"],
                day=payload["day"],
                timezone=payload["timezone"],
                scope=payload["scope"],
            )
            session.add(group)
            session.flush()
        return group

    def enqueue(self, form: DigestInput, *, cutoff=None, key=None, extra=None):
        # Submission is a short transaction; generation uses a separate lock.
        # This also serializes request-ID conflicts across different scopes.
        with self.queue_lock("submit"):
            return self._enqueue(form, cutoff=cutoff, key=key, extra=extra)

    def _enqueue(self, form, *, cutoff=None, key=None, extra=None):
        request_key = key or ("digest-request:" + form.request_id if form.request_id else None)
        alias_key = "digest-request-alias:" + fingerprint(request_key) if form.request_id else None

        def replay(existing, requested=None):
            requested = existing.payload if requested is None else requested
            if not key and (
                sorted(requested.get("requested_account_ids", requested.get("account_ids", [])))
                != sorted(form.account_ids)
                or bool(requested.get("force_reanalyze")) != form.force_reanalyze
            ):
                raise AppError("请求标识已用于其他刷新参数", "request_conflict", status=409)
            return record(existing)

        if request_key:
            with self.db.session() as session:
                alias = session.get(Receipt, alias_key) if alias_key else None
                if alias:
                    existing = session.get(Task, alias.data["task_id"])
                    if existing:
                        return replay(existing, alias.data)
                existing = session.scalar(select(Task).where(Task.idempotency_key == request_key))
                if existing:
                    return replay(existing)
        payload = self.freeze(
            form.model_dump() | {"requested_account_ids": form.account_ids} | (extra or {}), cutoff
        )
        with self.queue_lock(payload["group_id"]), self.db.session() as session:
            if request_key:
                existing = session.scalar(select(Task).where(Task.idempotency_key == request_key))
                if existing:
                    return replay(existing)
            group = self.ensure_group(session, payload)
            active = session.get(Task, group.active_task_id) if group.active_task_id else None
            # Delivery tasks must retain their own context and fixed schedule cutoff.
            if active and active.status in {"queued", "running"} and not key and not extra:
                if not active.payload.get("deliver") and (
                    not form.force_reanalyze or active.payload.get("force_reanalyze")
                ):
                    if alias_key:
                        session.add(
                            Receipt(
                                key=alias_key,
                                data={
                                    "task_id": active.id,
                                    "requested_account_ids": form.account_ids,
                                    "force_reanalyze": form.force_reanalyze,
                                },
                            )
                        )
                    return record(active)
            task = self.w.tasks.enqueue_in(session, "digest", payload, key=request_key)
            if alias_key:
                session.add(
                    Receipt(
                        key=alias_key,
                        data={
                            "task_id": task["id"],
                            "requested_account_ids": form.account_ids,
                            "force_reanalyze": form.force_reanalyze,
                        },
                    )
                )
            group.active_task_id = task["id"]
            return task

    def prepare(self, task):
        if task["payload"].get("range_end"):
            return task["payload"]
        payload = self.freeze(task["payload"], datetime.fromisoformat(task["created_at"]))
        with self.queue_lock(payload["group_id"]), self.db.session() as session:
            row = session.get(Task, task["id"])
            if row.payload.get("range_end"):
                return row.payload
            self.ensure_group(session, payload)
            row.payload = payload
        return payload

    def view(self, digest, session):
        value = record(digest)
        value.pop("sources", None)
        value.pop("fingerprint", None)
        group = session.get(DigestGroup, digest.group_id) if digest.group_id else None
        value["account_ids"] = [a["id"] for a in group.scope] if group else []
        if group and group.latest_digest_id == digest.id:
            value.update(
                checked_at=group.checked_at,
                checked_start=group.checked_start,
                checked_end=group.checked_end,
                refresh_error=group.last_error,
            )
        return value

    def list(self):
        with self.db.session() as session:
            return [
                self.view(d, session)
                for d in session.scalars(
                    select(Digest)
                    .order_by(Digest.created_at.desc(), Digest.revision.desc())
                    .limit(200)
                )
            ]

    def latest(self):
        values = self.list()
        return next(
            (d for d in values if not d["legacy"] and d["status"] == "generated"),
            next(
                (d for d in values if not d["legacy"]),
                next((d for d in values if d["items"]), None),
            ),
        )

    def analysis_version(self):
        profile = self.db.get("model", {})
        return fingerprint(
            {
                "mode": self.w.settings.mode,
                "base_url": profile.get("base_url"),
                "name": profile.get("name"),
                "revision": profile.get("revision", 0),
                "prompt": prompts.SUMMARY,
                "schema": 1,
            }
        )

    def source_key(self, mail):
        return fingerprint(
            {
                k: mail.get(k)
                for k in (
                    "id",
                    "folder",
                    "source_id",
                    "source_epoch",
                    "subject",
                    "sender",
                    "recipients",
                    "sent_at",
                    "received_at",
                    "body",
                    "body_truncated",
                )
            }
        )

    def cache_expiry(self, cutoff, item, zone):
        wording = " ".join(str(v) for v in item.values() if v is not None)
        if not item.get("deadline") and not any(
            word in wording for word in ("今天", "明天", "临近", "过期", "本周", "下周")
        ):
            return normalize_time((cutoff + timedelta(hours=24)).isoformat())
        end = cutoff + timedelta(minutes=15)
        local = cutoff.astimezone(ZoneInfo(zone))
        midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = min(end, midnight.astimezone(timezone.utc))
        try:
            deadline = datetime.fromisoformat(item.get("deadline") or "")
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=ZoneInfo(zone))
            for threshold in (deadline, deadline - timedelta(hours=24)):
                if threshold > cutoff:
                    end = min(end, threshold.astimezone(timezone.utc))
        except ValueError:
            pass
        return normalize_time(end.isoformat())

    async def generate(self, task):
        with self.db.session() as session:
            receipt = session.get(Receipt, "digest-task:" + task["id"])
            if receipt:
                return receipt.data
        payload = self.prepare(task)
        async with self.generation_lock(payload["group_id"]):
            with self.db.session() as session:
                receipt = session.get(Receipt, "digest-task:" + task["id"])
                if receipt:
                    return receipt.data
            try:
                return await self.run_flow(task, payload)
            except Exception as exc:
                with self.db.session() as session:
                    group = session.get(DigestGroup, payload["group_id"])
                    group.last_error = getattr(exc, "message", "本次刷新未完成，保留上次可用结果")
                raise

    async def run_flow(self, task, payload):
        cutoff = datetime.fromisoformat(payload["range_end"])
        model_version = self.analysis_version()
        profile = self.db.get("model", {})

        def validate_scope():
            current = self.w.mailboxes.selected(payload["account_ids"])
            if any(
                not any(
                    a["id"] == b["id"]
                    and a["folder"] == b["folder"]
                    and a["revision"] == b["revision"]
                    for b in current
                )
                for a in payload["scope"]
            ):
                raise AppError("刷新期间邮箱配置已更改，请重新刷新", "revision_conflict")
            return current

        async def collect(data):
            validate_scope()
            coverage = await self.w.mailboxes.sync(
                payload["account_ids"],
                lambda title: self.w.progress(task["id"], title),
                range_start=payload["range_start"],
            )
            if not coverage or all(not c["ok"] for c in coverage):
                raise AppError(
                    "所有邮箱同步均未成功，保留上次摘要，请检查连接后重试",
                    "all_accounts_failed",
                    retryable=True,
                )
            selected = validate_scope()
            with self.db.session() as session:
                statement = select(Mail).where(
                    active_folder_scope(selected),
                    Mail.observed_state == "current",
                    Mail.received_at >= payload["range_start"],
                    Mail.received_at < payload["range_end"],
                )
                total = session.scalar(select(func.count()).select_from(statement.subquery()))
                mails = [
                    record(m)
                    for m in session.scalars(
                        statement.order_by(Mail.received_at.desc(), Mail.id).limit(100)
                    )
                ]
                labels = {a["id"]: a["label"] for a in selected}
                mails = [m | {"account_label": labels[m["account_id"]]} for m in mails]
                unknown = session.scalar(
                    select(func.count())
                    .select_from(Mail)
                    .where(active_folder_scope(selected), Mail.received_at.is_(None))
                )
            truncated = sum(bool(m["body_truncated"]) for m in mails)
            if total > 100 or unknown or truncated:
                warnings = []
                if total > 100:
                    warnings.append(f"最近 24 小时共有 {total} 封，本次仅分析最近 100 封")
                if unknown:
                    warnings.append(f"{unknown} 封缓存缺少服务器收件时间，未纳入时间范围")
                if truncated:
                    warnings.append(f"{truncated} 封邮件正文仅有片段，摘要依据可能不完整")
                coverage.append(
                    {
                        "label": "摘要范围",
                        "ok": True,
                        "coverage_state": "partial",
                        "warnings": warnings,
                    }
                )
            return {"mails": mails, "coverage": coverage, "total": total}

        async def analyze(data):
            value = data.input
            items, keys, missing = {}, {}, []
            for mail in value["mails"]:
                key = fingerprint({"source": self.source_key(mail), "analysis": model_version})
                keys[mail["id"]] = key
                with self.db.session() as session:
                    cached = session.get(MailAnalysis, key)
                    if (
                        cached
                        and cached.valid_until > payload["range_end"]
                        and not payload.get("force_reanalyze")
                    ):
                        items[mail["id"]] = cached.item
                    else:
                        missing.append(mail)
            if missing:
                fresh = await self.w.intelligence.summarize(
                    missing,
                    lambda title: self.w.progress(task["id"], title),
                    profile=profile,
                    analysis_time=payload["range_end"],
                )
                fresh = TypeAdapter(list[DigestItem]).validate_python(
                    [item.model_dump() if hasattr(item, "model_dump") else item for item in fresh]
                )
                if len(fresh) != len(missing) or {i.mail_id for i in fresh} != {
                    m["id"] for m in missing
                }:
                    raise AppError("模型摘要的邮件来源不匹配，结果未保存", "invalid_sources")
                for item in fresh:
                    items[item.mail_id] = item.model_dump()
            return value | {
                "items": [items[m["id"]] for m in value["mails"]],
                "keys": keys,
                "analyzed": len(missing),
                "reused_analysis": len(items) - len(missing),
            }

        async def persist(data):
            validate_scope()
            if model_version != self.analysis_version():
                raise AppError("分析期间模型配置已更改，请重新刷新", "revision_conflict")
            await self.w.progress(task["id"], "正在核对摘要版本并保存检查结果")
            value = data.input
            partial = any(
                not c["ok"] or c.get("coverage_state") != "complete" for c in value["coverage"]
            )
            evidence = [
                {
                    k: c.get(k)
                    for k in ("account_id", "label", "ok", "coverage_state", "warnings", "error")
                }
                for c in value["coverage"]
            ]
            digest_hash = fingerprint(
                {
                    "sources": [self.source_key(m) for m in value["mails"]],
                    "items": value["items"],
                    "coverage": evidence,
                    "model": model_version,
                }
            )
            with self.db.session() as session:
                group = self.ensure_group(session, payload)
                previous = (
                    session.get(Digest, group.latest_digest_id) if group.latest_digest_id else None
                )
                before_ids = {i["mail_id"] for i in previous.items} if previous else set()
                current_ids = {i["mail_id"] for i in value["items"]}
                reused = bool(previous and previous.fingerprint == digest_hash)
                digest = previous if reused else None
                # An empty first check is a task result, never a fake generated report.
                if not reused and (value["items"] or (previous and not partial)):
                    revision = (
                        session.scalar(
                            select(func.max(Digest.revision)).where(Digest.group_id == group.id)
                        )
                        or 0
                    ) + 1
                    digest = Digest(
                        title=cutoff.astimezone(ZoneInfo(payload["timezone"])).strftime(
                            "%m月%d日 · 最近24小时摘要"
                        ),
                        group_id=group.id,
                        revision=revision,
                        legacy=False,
                        range_start=payload["range_start"],
                        range_end=payload["range_end"],
                        timezone=payload["timezone"],
                        source_count=value["total"],
                        items=value["items"],
                        coverage=value["coverage"],
                        status="partial" if partial else "generated",
                        fingerprint=digest_hash,
                        sources=value["mails"],
                    )
                    session.add(digest)
                    session.flush()
                preserve = bool(partial and previous and previous.status == "generated")
                if digest and not preserve:
                    group.latest_digest_id = digest.id
                if not partial:
                    group.checked_at, group.checked_start, group.checked_end = (
                        stamp(),
                        payload["range_start"],
                        payload["range_end"],
                    )
                    group.last_error = None
                else:
                    group.last_error = (
                        "部分邮箱或邮件范围尚未确认，保留上次完整结果"
                        if preserve
                        else "本次摘要覆盖尚未完整确认"
                    )
                for item in value["items"]:
                    key = value["keys"][item["mail_id"]]
                    cached = session.get(MailAnalysis, key)
                    if cached is None:
                        session.add(
                            MailAnalysis(
                                id=key,
                                mail_id=item["mail_id"],
                                item=item,
                                valid_until=self.cache_expiry(cutoff, item, payload["timezone"]),
                            )
                        )
                    elif value["analyzed"] and (
                        payload.get("force_reanalyze") or cached.valid_until <= payload["range_end"]
                    ):
                        cached.item = item
                        cached.valid_until = self.cache_expiry(cutoff, item, payload["timezone"])
                    mail = session.get(Mail, item["mail_id"])
                    mail.priority, mail.pending_digest = item["priority"], False
                shown = previous if preserve else digest
                result = {
                    "digest_id": shown.id if shown else None,
                    "group_id": group.id,
                    "partial_digest_id": digest.id if preserve and digest else None,
                    "reused": reused,
                    "empty": not value["items"] and not partial,
                    "partial": partial,
                    "mail_count": value["total"],
                    "added": len(current_ids - before_ids),
                    "removed": len(before_ids - current_ids),
                    "analyzed": value["analyzed"],
                    "reused_analysis": value["reused_analysis"],
                    "range_start": payload["range_start"],
                    "range_end": payload["range_end"],
                    "account_ids": payload["account_ids"],
                    "message": "覆盖尚未确认，保留上次可用结果"
                    if preserve
                    else "本次摘要覆盖尚未完整确认"
                    if partial
                    else "最近 24 小时没有邮件"
                    if not value["items"]
                    else "已是最新，内容未变化"
                    if reused
                    else f"摘要已更新，共 {value['total']} 封邮件",
                }
                session.add(Receipt(key="digest-task:" + task["id"], data=result))
                return result

        flow = TriggerFlow(name="mail-digest-" + task["id"])
        flow.to(collect).to(analyze).to(persist)
        # We explicitly drain/close the finite flow. Auto-close otherwise adds
        # the framework's default 10-second idle grace to every cache hit.
        execution = flow.create_execution(auto_close=False)
        try:
            await execution.async_start({})
        finally:
            await execution.async_close()
        with self.db.session() as session:
            receipt = session.get(Receipt, "digest-task:" + task["id"])
            if not receipt:
                raise AppError(
                    "摘要刷新未完成，请查看任务后重试", "flow_incomplete", retryable=True
                )
            return receipt.data
