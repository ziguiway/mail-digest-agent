from datetime import datetime, timedelta, timezone

import pytest
from mcp_email_server.application.metadata import MailboxMetadataSnapshot, MailboxState
from mcp_email_server.emails.models import MailboxInfo

from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.models import Coverage, Mail
from mail_assistant.infrastructure.mail_provider import McpMailProvider
from mail_assistant.infrastructure.mcp_server import snapshot_payload


def make_snapshot(count=3, complete=True, missing_date=False):
    now = datetime.now(timezone.utc)
    return MailboxMetadataSnapshot(
        state=MailboxState(uidvalidity=12, uidnext=count + 1, message_count=count),
        mailbox=MailboxInfo(name="INBOX", delimiter="/", flags=[]),
        emails=tuple(
            {
                "email_id": str(i + 1),
                "message_id": f"<{i}@test>",
                "date": now - timedelta(days=20),
                "_internal_date": None if missing_date else now - timedelta(minutes=i),
            }
            for i in range(min(count, 1000))
        ),
        complete=complete,
        observed_at=now,
    )


def test_snapshot_serialization_keeps_receive_time_distinct_from_date_header():
    snapshot = make_snapshot()
    data = snapshot_payload(snapshot)
    assert data["coverage_state"] == "complete"
    assert data["mailbox_state"]["uidvalidity"] == 12
    for source, row in zip(snapshot.emails, data["emails"]):
        assert datetime.fromisoformat(row["received_at"]) == source["_internal_date"]
        assert datetime.fromisoformat(row["received_at"]) != source["date"]


def test_over_one_thousand_and_missing_receive_time_never_claim_complete():
    partial = snapshot_payload(make_snapshot(1001, complete=False))
    assert len(partial["emails"]) == 1000
    assert partial["coverage_state"] == "partial" and partial["warnings"]
    unknown = snapshot_payload(make_snapshot(missing_date=True))
    assert unknown["coverage_state"] == "unknown"
    assert all(row["received_at"] is None for row in unknown["emails"])


async def test_provider_freezes_pages_filters_on_receive_time_and_verifies_epoch(tmp_path):
    p = McpMailProvider(Settings(tmp_path))
    data = snapshot_payload(make_snapshot(205))
    names = []

    async def call(name, arguments):
        names.append(name)
        return data if name == "workspace_mailbox_snapshot" else data["mailbox_state"]

    p.call = call
    account = {"id": "test", "upstream_name": "a", "folder": "INBOX"}
    before = datetime.now(timezone.utc).isoformat()
    since = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    first = await p.metadata(account, since, before)
    second = await p.metadata(account, since, before, 2)
    assert first["total"] == 120 and len(first["emails"]) == 100 and len(second["emails"]) == 20
    assert names == ["workspace_mailbox_snapshot"]
    await p.verify_snapshot(account, first["mailbox_state"])
    with pytest.raises(AppError, match="邮箱状态发生变化"):
        await p.verify_snapshot(account, first["mailbox_state"] | {"uidvalidity": 99})


async def test_partial_sync_saves_rows_but_not_watermark_and_unknown_dates_are_not_recent(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    try:
        await w.seed_demo()
        account = w.accounts.get("demo_qq")
        original = w.provider.metadata

        async def partial(*args):
            return await original(*args) | {
                "coverage_state": "partial",
                "warnings": ["快照达到窗口限制"],
            }

        w.provider.metadata = partial
        result = await w.mailboxes.sync([account["id"]])
        assert result[0]["ok"] and result[0]["coverage_state"] == "partial"
        assert w.accounts.get(account["id"])["last_sync_at"] == account["last_sync_at"]
        assert not w.accounts.get(account["id"])["sync_complete"]
        with w.db.session() as session:
            assert session.get(Coverage, account["id"]).warnings == ["快照达到窗口限制"]
        mail_id = w.mailboxes.upsert(
            account,
            {
                "email_id": "9999",
                "message_id": "<no-date@test>",
                "date": datetime.now(timezone.utc).isoformat(),
                "subject": "未知收件时间",
                "body": "这可能是历史邮件",
            },
            initial=True,
        )
        with w.db.session() as session:
            assert not session.get(Mail, mail_id).pending_digest
    finally:
        await w.close()


async def test_epoch_change_restarts_historical_scan_and_preserves_cached_identity(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    try:
        await w.seed_demo()
        original = w.provider.metadata
        seen_windows = []

        async def changed(account, since, before, page=1):
            seen_windows.append((since, before))
            data = await original(account, since, before, page)
            return data | {"mailbox_state": data["mailbox_state"] | {"uidvalidity": 77}}

        w.provider.metadata = changed
        await w.mailboxes.sync(["demo_163"])
        assert len(seen_windows) == 2
        assert datetime.fromisoformat(seen_windows[1][0]) < datetime.fromisoformat(
            seen_windows[0][0]
        ) - timedelta(days=20)
        assert len(w.mailboxes.search(keywords=["会议"])["mails"]) == 2
        with w.db.session() as session:
            assert session.get(Coverage, "demo_163").cursor["mailbox_state"]["uidvalidity"] == 77
    finally:
        await w.close()
