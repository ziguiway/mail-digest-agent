from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import func, or_, and_, false, select
from ..domain.errors import AppError
from ..domain.models import Account, Coverage, Mail, stamp
from ..domain.schemas import MailView, ScheduleInput
from ..infrastructure.database import record


def normalize_time(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def active_folder_scope(accounts):
    return (
        or_(*[and_(Mail.account_id == a["id"], Mail.folder == a["folder"]) for a in accounts])
        if accounts
        else false()
    )


class Mailboxes:
    def __init__(self, db, provider, accounts):
        self.db, self.provider, self.accounts = db, provider, accounts
        self.locks = {}

    async def folders(self, account_id, revision):
        account = self.accounts.get(account_id)
        if not account["enabled"]:
            raise AppError("请先启用邮箱，再读取文件夹", "account_disabled")
        if account["revision"] != revision:
            raise AppError("账号配置已改变，请重新读取文件夹", "revision_conflict")
        folders = await self.provider.list_folders(account)
        if self.accounts.get(account_id)["revision"] != revision:
            raise AppError("读取期间账号配置已改变，请重新读取文件夹", "revision_conflict")
        return {
            "account_id": account_id,
            "revision": revision,
            "folders": folders,
            "observed_at": stamp(),
        }

    def selected(self, ids=None):
        with self.db.session() as session:
            statement = select(Account).where(
                Account.enabled.is_(True), Account.removed_at.is_(None)
            )
            if ids:
                statement = statement.where(Account.id.in_(ids))
            values = [record(a) for a in session.scalars(statement)]
        if ids and set(ids) - {a["id"] for a in values}:
            raise AppError("所选邮箱不存在或已停用，请重新选择", "invalid_account_selection")
        return values

    def present(self, mail, account_label="", current_folder=None, account_enabled=True):
        data = record(mail) if isinstance(mail, Mail) else mail
        if not account_enabled or (current_folder is not None and data["folder"] != current_folder):
            data = data | {"observed_state": "historical"}
        return MailView.model_validate(data | {"account_label": account_label}).model_dump()

    def cached_mail(self, mail_id):
        with self.db.session() as session:
            mail = session.get(Mail, mail_id)
            if mail is None:
                raise AppError("邮件不存在", "mail_not_found", status=404)
            account = session.get(Account, mail.account_id)
            return self.present(mail, account.label, account.folder, account.enabled)

    def read(self, mail_ids, account_ids=None):
        selected = {a["id"]: a for a in self.selected(account_ids)}
        with self.db.session() as session:
            return [
                self.present(
                    mail, selected[mail.account_id]["label"], selected[mail.account_id]["folder"]
                )
                for mail in session.scalars(
                    select(Mail).where(Mail.id.in_(mail_ids), Mail.account_id.in_(selected))
                )
            ]

    def search(
        self,
        *,
        account_ids=None,
        keywords=None,
        sender="",
        since="",
        before="",
        limit=20,
        offset=0,
        cache_state="all",
    ):
        selected = {a["id"]: a for a in self.selected(account_ids)}
        statement = select(Mail).where(Mail.account_id.in_(selected))
        active = active_folder_scope(list(selected.values()))
        if cache_state == "current":
            statement = statement.where(active, Mail.observed_state == "current")
        elif cache_state == "historical":
            statement = statement.where(or_(~active, Mail.observed_state == "historical"))
        elif cache_state == "unknown":
            statement = statement.where(active, Mail.observed_state == "unknown")
        elif cache_state != "all":
            raise AppError("缓存状态筛选无效", "invalid_query")
        warnings = [
            f"{a['label']}：{a['error'] or '同步覆盖尚未确认'}"
            for a in selected.values()
            if not a["sync_complete"]
        ]
        keywords = keywords or []
        if len(keywords) > 10 or any(not isinstance(k, str) or len(k) > 200 for k in keywords):
            raise AppError("请使用较短的搜索关键词", "invalid_query")
        for term in keywords:
            statement = statement.where(
                or_(
                    func.instr(func.lower(Mail.subject), term.lower()) > 0,
                    func.instr(func.lower(Mail.body), term.lower()) > 0,
                    func.instr(func.lower(Mail.sender), term.lower()) > 0,
                )
            )
        if sender:
            statement = statement.where(func.instr(func.lower(Mail.sender), sender.lower()) > 0)
        schedule = ScheduleInput(**self.db.get("schedule", {}))

        def bound(value):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(schedule.timezone))
            return normalize_time(parsed.isoformat())

        if since:
            statement = statement.where(Mail.received_at >= bound(since))
        if before:
            statement = statement.where(Mail.received_at < bound(before))
        with self.db.session() as session:
            total = session.scalar(select(func.count()).select_from(statement.subquery()))
            rows = session.scalars(
                statement.order_by(func.coalesce(Mail.received_at, Mail.sent_at).desc(), Mail.id)
                .offset(max(0, offset))
                .limit(min(50, max(1, limit)))
            ).all()
            unknown = session.scalar(
                select(func.count())
                .select_from(Mail)
                .where(Mail.account_id.in_(selected), Mail.received_at.is_(None))
            )
            if unknown:
                warnings.append(
                    "部分缓存尚未提供服务器收件时间，不纳入近期摘要；时间过滤结果可能不完整"
                )
            mails = [
                self.present(m, selected[m.account_id]["label"], selected[m.account_id]["folder"])
                for m in rows
            ]
            if any(m["observed_state"] == "historical" for m in mails):
                warnings.append("结果包含历史缓存，不能据此证明邮件仍在当前同步文件夹中")
            if any(m["observed_state"] == "unknown" for m in mails):
                warnings.append("部分邮件的当前观察状态尚未确认，请同步后核对")
            return {
                "mails": mails,
                "total": total,
                "has_more": offset + len(rows) < total,
                "warnings": warnings,
            }

    def thread(self, mail_id, account_ids=None, *, include_inactive=False):
        selected = {
            a["id"]: a
            for a in (
                self.accounts.list(include_removed=True)
                if include_inactive
                else self.selected(account_ids)
            )
            if not account_ids or a["id"] in account_ids
        }
        with self.db.session() as session:
            seed = session.get(Mail, mail_id)
            if not seed or seed.account_id not in selected:
                raise AppError("找不到这封邮件，或来源邮箱已停用", "mail_not_found", status=404)
            all_rows = session.scalars(select(Mail).where(Mail.account_id.in_(selected))).all()
            found, linked = {seed.id}, {seed.message_id, *seed.references} - {""}
            changed = True
            while changed:
                changed = False
                for mail in all_rows:
                    if mail.id not in found and linked.intersection(
                        {mail.message_id, *mail.references} - {""}
                    ):
                        found.add(mail.id)
                        linked.update({mail.message_id, *mail.references} - {""})
                        changed = True

            def subject(value):
                return (
                    re.sub(r"^(?:(?:re|fw|fwd|回复|答复|转发)\s*[:：]\s*)+", "", value, flags=re.I)
                    .strip()
                    .casefold()
                )

            exact = sorted([m for m in all_rows if m.id in found], key=lambda m: m.sent_at)
            candidates = [
                m
                for m in all_rows
                if m.id not in found and subject(m.subject) == subject(seed.subject)
            ]
            return {
                "linked": [
                    self.present(
                        m,
                        selected[m.account_id]["label"],
                        selected[m.account_id]["folder"],
                        selected[m.account_id]["enabled"],
                    )
                    for m in exact[-30:]
                ],
                "subject_candidates": [
                    self.present(
                        m,
                        selected[m.account_id]["label"],
                        selected[m.account_id]["folder"],
                        selected[m.account_id]["enabled"],
                    )
                    for m in candidates[:10]
                ],
                "truncated": len(exact) > 30 or len(candidates) > 10,
                "note": "linked 依据引用头关联；同标题候选须核对正文",
            }

    def upsert(self, account, message, *, initial=False, digest_start=None):
        source_id = str(message["email_id"])
        message_id = message.get("message_id") or ""
        sent_at = normalize_time(message.get("date")) or stamp()
        body = str(message.get("body", ""))
        identity = message_id or hashlib.sha256((source_id + sent_at + body).encode()).hexdigest()
        mail_id = (
            "m_"
            + hashlib.sha256(
                (account["id"] + ":" + account["folder"] + ":" + identity).encode()
            ).hexdigest()[:24]
        )
        received = normalize_time(message.get("received_at"))
        epoch = message.get("source_epoch")
        refs = re.findall(
            r"<[^<>\s]+>",
            str(message.get("references", "")) + " " + str(message.get("in_reply_to", "")),
        )
        with self.db.session() as session:
            row = session.get(Mail, mail_id)
            # A copied message may keep its Message-ID but receive another UID.
            if row and epoch and row.source_epoch == epoch and row.source_id != source_id:
                mail_id = (
                    "m_"
                    + hashlib.sha256(
                        f"{account['id']}:{account['folder']}:{epoch}:{source_id}".encode()
                    ).hexdigest()[:24]
                )
                row = session.get(Mail, mail_id)
            if row is None:
                row = Mail(
                    id=mail_id,
                    account_id=account["id"],
                    folder=account["folder"],
                    source_id=source_id,
                    subject=message.get("subject") or "（无主题）",
                    sender=message.get("sender") or "",
                    recipients=message.get("recipients", []),
                    message_id=message_id,
                    references=list(dict.fromkeys(refs)),
                    sent_at=sent_at,
                    received_at=received,
                    body=body[:40000],
                    body_truncated=len(body) > 40000 or bool(message.get("body_truncated")),
                    pending_digest=bool(
                        received and (not initial or not digest_start or received >= digest_start)
                    ),
                    source_epoch=epoch,
                )
                session.add(row)
            else:
                row.observed_state = "unknown"
                row.source_id, row.body, row.body_truncated = (
                    source_id,
                    body[:40000],
                    len(body) > 40000 or bool(message.get("body_truncated")),
                )
                if row.received_at is None and received and row.priority is None:
                    row.pending_digest = bool(
                        not initial or not digest_start or received >= digest_start
                    )
                row.received_at = received or row.received_at
                row.source_epoch = epoch or row.source_epoch
        return mail_id

    def cached_sources(self, account, rows, state):
        """Only reuse immutable bodies at the same folder/UIDVALIDITY/UID."""
        epoch = (state or {}).get("uidvalidity")
        if epoch is None:
            return {}
        by_uid = {str(row["email_id"]): row for row in rows}
        found = {}
        with self.db.session() as session:
            for mail in session.scalars(
                select(Mail).where(
                    Mail.account_id == account["id"],
                    Mail.folder == account["folder"],
                    Mail.source_epoch == str(epoch),
                    Mail.source_id.in_(by_uid),
                )
            ):
                source = by_uid[mail.source_id]
                if source.get("message_id") and mail.message_id != source["message_id"]:
                    raise AppError(
                        "相同 UID 的邮件身份发生变化，请重新同步核对",
                        "snapshot_identity_conflict",
                    )
                if mail.source_id in found:
                    raise AppError("缓存中的邮件编号重复，请重新核对", "snapshot_identity_conflict")
                found[mail.source_id] = mail.id
        return found

    def apply_observation(
        self, session, account, state, observations, fetched_ids, membership_complete
    ):
        """Positive observations survive partial windows; absence needs a full inventory.

        Called only after body/state verification and account revision validation,
        in the same transaction that commits coverage and its watermark.
        """
        state = state or {}
        epoch = str(state["uidvalidity"]) if state.get("uidvalidity") is not None else None
        by_uid, by_mid = {}, {}
        for item in observations:
            uid = str(item["email_id"])
            if uid in by_uid:
                raise AppError("快照中的 UID 重复，观察范围尚未确认", "snapshot_identity_conflict")
            by_uid[uid] = item
            if item.get("message_id"):
                by_mid.setdefault(item["message_id"], []).append(item)
        full = bool(
            membership_complete is True and epoch and state.get("message_count") == len(by_uid)
        )
        observed_at = stamp()
        rows = session.scalars(
            select(Mail).where(Mail.account_id == account["id"], Mail.folder == account["folder"])
        )
        for row in rows:
            if epoch is None:
                row.observed_state = "unknown"
                continue
            item = by_uid.get(row.source_id)
            same_epoch = row.source_epoch == epoch
            if (
                same_epoch
                and item
                and item.get("message_id")
                and row.message_id
                and item["message_id"] != row.message_id
            ):
                raise AppError(
                    "相同 UID 的邮件身份发生变化，请重新同步核对",
                    "snapshot_identity_conflict",
                    retryable=True,
                )
            if row.id in fetched_ids:
                if not item:
                    raise AppError(
                        "读取的邮件未在快照中确认，请重新同步",
                        "snapshot_identity_conflict",
                        retryable=True,
                    )
            elif row.message_id:
                matches = by_mid.get(row.message_id, [])
                item = next(
                    (m for m in matches if str(m["email_id"]) == row.source_id),
                    matches[0] if not same_epoch and len(matches) == 1 else None,
                )
                if not item and same_epoch:
                    candidate = by_uid.get(row.source_id)
                    item = candidate if candidate and not candidate.get("message_id") else None
            elif not same_epoch:
                item = None
            if item:
                row.source_id, row.source_epoch = str(item["email_id"]), epoch
                row.observed_state, row.last_observed_at = "current", observed_at
            elif full:
                row.observed_state = "historical"
            elif not same_epoch:
                row.observed_state = "unknown"

    async def sync(self, account_ids=None, progress=None, *, range_start=None):
        selected = [account["id"] for account in self.selected(account_ids)]
        if not selected:
            return []
        pending, outcomes = selected, {}
        for attempt in range(1, 4):
            results = await self._sync_once(pending, progress, range_start=range_start)
            for result in results:
                outcomes[result["account_id"]] = result | {"attempts": attempt}
            pending = [
                result["account_id"]
                for result in results
                if not result["ok"]
                and result.get("code") in {"mailbox_changed", "incomplete_batch"}
            ]
            if not pending or attempt == 3:
                break
            if progress:
                await progress(f"邮件已读取，正在重新核对同步进度（{attempt + 1}/3）")
        return [outcomes[account_id] for account_id in selected if account_id in outcomes]

    async def _sync_once(self, account_ids, progress, *, range_start=None):
        results = []
        schedule = ScheduleInput(**self.db.get("schedule", {}))
        for account in self.selected(account_ids):
            lock = self.locks.setdefault(account["id"], asyncio.Lock())
            async with lock:
                # Re-read after the lock: an earlier sync may have advanced it.
                try:
                    account = self.accounts.get(account["id"])
                except AppError as exc:
                    if exc.code != "account_not_found":
                        raise
                    results.append(
                        {
                            "account_id": account["id"],
                            "ok": False,
                            "code": "account_removed",
                            "error": "连接已移除，本次跳过此邮箱",
                        }
                    )
                    continue
                if not account["enabled"]:
                    continue
                if progress:
                    await progress(f"正在同步 {account['label']}")
                now = datetime.now(timezone.utc)
                initial = account["last_sync_at"] is None
                since = (
                    (now - timedelta(days=schedule.initial_days))
                    if initial
                    else datetime.fromisoformat(account["last_sync_at"]) - timedelta(minutes=10)
                )
                if range_start:
                    since = min(since, datetime.fromisoformat(range_start))
                end_at = normalize_time(now.isoformat())
                since_at = normalize_time(since.isoformat())
                digest_start = normalize_time(
                    (now - timedelta(hours=schedule.first_digest_hours)).isoformat()
                )
                fetched, reused, expected, seen = 0, 0, None, set()
                fetched_ids, observations, membership_complete = set(), [], False
                coverage_state, warnings, observed_state = "unknown", [], None
                sync_method, diagnostics = "uid_state", []
                with self.db.session() as session:
                    previous = session.get(Coverage, account["id"])
                    previous_state = (
                        (previous.cursor.get("mailbox_state") or {}) if previous else {}
                    )
                try:
                    for page in range(1, 201):
                        metadata = await self.provider.metadata(account, since_at, end_at, page)
                        if page == 1:
                            observed_state = metadata.get("mailbox_state")
                            old_epoch = previous_state.get("uidvalidity")
                            new_epoch = (observed_state or {}).get("uidvalidity")
                            if old_epoch and new_epoch and old_epoch != new_epoch:
                                initial = True
                                since_at = normalize_time(
                                    (now - timedelta(days=schedule.initial_days)).isoformat()
                                )
                                metadata = await self.provider.metadata(
                                    account, since_at, end_at, page
                                )
                                observed_state = metadata.get("mailbox_state")
                            # Revisit the configured range in compatibility mode;
                            # cached bodies make this a metadata-only scan for old UIDs.
                            lookback = normalize_time(
                                (now - timedelta(days=schedule.initial_days)).isoformat()
                            )
                            if (
                                observed_state
                                and observed_state.get("uidnext") is None
                                and since_at > lookback
                            ):
                                since_at = lookback
                                metadata = await self.provider.metadata(
                                    account, since_at, end_at, page
                                )
                                observed_state = metadata.get("mailbox_state")
                            coverage_state = metadata.get("coverage_state", "unknown")
                            warnings = list(metadata.get("warnings", []))
                            sync_method = metadata.get("sync_method", "uid_state")
                            diagnostics = list(metadata.get("diagnostics", []))
                            observations = metadata.get("observations", [])
                            membership_complete = metadata.get("membership_complete", False)
                            if coverage_state != "complete" and not warnings:
                                warnings.append("邮箱组件尚未证明本次同步的完整覆盖")
                        rows = metadata.get("emails", [])
                        total = int(metadata.get("total", len(rows)))
                        if expected is not None and total != expected:
                            raise AppError(
                                "同步期间邮箱发生变化，请重试以核对完整范围",
                                "mailbox_changed",
                                retryable=True,
                            )
                        expected = total
                        ids = [str(m["email_id"]) for m in rows]
                        if len(set(ids)) != len(ids) or set(ids).intersection(seen):
                            raise AppError(
                                "邮件分页发生重复，本次覆盖尚未确认",
                                "unstable_pagination",
                                retryable=True,
                            )
                        seen.update(ids)
                        by_id = {str(m["email_id"]): m for m in rows}
                        if "observations" not in metadata:
                            observations.extend(rows)
                        cached = self.cached_sources(account, rows, observed_state)
                        fetched_ids.update(cached.values())
                        reused += len(cached)
                        needed = [uid for uid in ids if uid not in cached]
                        for start in range(0, len(needed), 8):
                            batch = await self.provider.bodies(account, needed[start : start + 8])
                            if len(batch) != len(needed[start : start + 8]) or {
                                str(m["email_id"]) for m in batch
                            } != set(needed[start : start + 8]):
                                raise AppError(
                                    "部分邮件正文未读取成功，请重试",
                                    "incomplete_batch",
                                    retryable=True,
                                )
                            for message in batch:
                                source = by_id[str(message["email_id"])]
                                if source.get("message_id") and source["message_id"] != message.get(
                                    "message_id"
                                ):
                                    raise AppError(
                                        "邮件标识在读取期间发生变化，请重试同步",
                                        "mailbox_changed",
                                        retryable=True,
                                    )
                                message = message | {
                                    "received_at": source.get("received_at")
                                    or message.get("received_at"),
                                    "source_epoch": str(observed_state["uidvalidity"])
                                    if (observed_state or {}).get("uidvalidity") is not None
                                    else None,
                                }
                                mail_id = self.upsert(
                                    account, message, initial=initial, digest_start=digest_start
                                )
                                fetched_ids.add(mail_id)
                                fetched += 1
                        if len(seen) >= expected:
                            break
                        if not rows:
                            raise AppError(
                                "邮件分页未返回完整结果", "incomplete_pagination", retryable=True
                            )
                    else:
                        raise AppError("本次同步达到分页上限，请缩小初次回溯范围", "sync_limit")
                    if hasattr(self.provider, "verify_snapshot"):
                        await self.provider.verify_snapshot(account, observed_state)
                    complete = coverage_state == "complete"
                    with self.db.session() as session:
                        row = session.get(Account, account["id"])
                        if row.revision != account["revision"]:
                            raise AppError(
                                "同步期间账号配置已更新，请重新同步", "revision_conflict"
                            )
                        self.apply_observation(
                            session,
                            account,
                            observed_state,
                            observations,
                            fetched_ids,
                            membership_complete,
                        )
                        if complete:
                            row.last_sync_at = end_at
                        row.sync_complete, row.status, row.error = (
                            complete,
                            "verified",
                            "；".join(warnings) or None,
                        )
                        coverage = session.get(Coverage, account["id"])
                        if coverage is None:
                            coverage = Coverage(
                                account_id=account["id"], start_at=since_at, end_at=end_at
                            )
                            session.add(coverage)
                        coverage.end_at, coverage.complete = end_at, complete
                        coverage.cursor = {
                            "type": "provider_snapshot",
                            "since": since_at,
                            "before": end_at,
                            "fetched": fetched,
                            "mailbox_state": observed_state,
                            "coverage_state": coverage_state,
                            "folder": account["folder"],
                            "membership_complete": membership_complete,
                            "sync_method": sync_method,
                            "diagnostics": diagnostics,
                            "verified_at": stamp(),
                            "reused": reused,
                        }
                        coverage.warnings = warnings
                    results.append(
                        {
                            "account_id": account["id"],
                            "label": account["label"],
                            "ok": True,
                            "coverage_state": coverage_state,
                            "warnings": warnings,
                            "fetched": fetched,
                            "reused": reused,
                            "sync_method": sync_method,
                            "since": since_at,
                            "before": end_at,
                        }
                    )
                except (AppError, KeyError, TypeError, ValueError) as error:
                    if not isinstance(error, AppError):
                        error = AppError(
                            "邮件组件返回的数据无法核对，请重试此邮箱",
                            "mcp_contract_error",
                            retryable=True,
                        )
                    with self.db.session() as session:
                        row = session.get(Account, account["id"])
                        row.error, row.sync_complete = error.message, False
                        if "auth" in error.code or "credential" in error.code:
                            row.status = "error"
                    results.append(
                        {
                            "account_id": account["id"],
                            "label": account["label"],
                            "ok": False,
                            "fetched": fetched,
                            "error": error.message,
                            "code": error.code,
                        }
                    )
        return results
