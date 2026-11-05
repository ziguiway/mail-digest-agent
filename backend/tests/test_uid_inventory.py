from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from mcp_email_server.application.metadata import MailboxMetadataSnapshot, MailboxState
from mcp_email_server.emails.models import MailboxInfo
from sqlalchemy import select

from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.models import Coverage, Mail
from mail_assistant.infrastructure.demo import fixtures
from mail_assistant.infrastructure.mail_provider import McpMailProvider
from mail_assistant.infrastructure.mcp_server import snapshot_payload


def snapshot(rows, *, epoch=1, uidnext=None, complete=True, count=None):
    return MailboxMetadataSnapshot(
        state=MailboxState(
            uidvalidity=epoch, uidnext=uidnext, message_count=len(rows) if count is None else count
        ),
        mailbox=MailboxInfo(name="INBOX", delimiter="/", flags=[]),
        emails=tuple(
            row | {"_internal_date": datetime.fromisoformat(row["received_at"])} for row in rows
        ),
        complete=complete,
        observed_at=datetime.now(timezone.utc),
    )


@pytest.fixture
async def mailbox(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    await w.seed_demo()
    account = w.accounts.get("demo_qq")
    rows = fixtures(account)
    provider = McpMailProvider(w.settings)
    w.mailboxes.provider = provider
    payload = {"rows": rows, "epoch": 1, "complete": True, "count": None}
    downloaded = []

    async def call(name, args):
        data = snapshot_payload(
            snapshot(
                payload["rows"],
                epoch=payload["epoch"],
                complete=payload["complete"],
                count=payload["count"],
            )
        )
        if name == "workspace_mailbox_snapshot":
            return data
        if name == "workspace_mailbox_state":
            return data["mailbox_state"]
        if name == "get_emails_content":
            downloaded.extend(args["email_ids"])
            return [row for row in payload["rows"] if row["email_id"] in args["email_ids"]]
        raise AssertionError(name)

    provider.call = call
    yield w, provider, payload, downloaded
    await provider.close()
    await w.close()


def test_inventory_without_uidnext_can_qualify_but_without_epoch_cannot():
    data = snapshot_payload(snapshot([]))
    assert data["membership_complete"] and data["coverage_state"] == "complete"
    assert data["mailbox_state"]["uidnext"] is None and not data["warnings"]
    assert data["sync_method"] == "uid_inventory" and "UIDNEXT" in data["diagnostics"][0]
    unknown = snapshot_payload(snapshot([], epoch=None))
    assert not unknown["membership_complete"] and unknown["coverage_state"] == "unknown"


def test_duplicate_uids_never_qualify():
    row = {"email_id": "7", "received_at": datetime.now(timezone.utc).isoformat()}
    data = snapshot_payload(snapshot([row, row]))
    assert not data["membership_complete"] and data["warnings"]


async def test_same_count_replacement_is_detected_by_real_provider_contract(mailbox):
    w, provider, payload, _ = mailbox
    now = datetime.now(timezone.utc)
    account = w.accounts.get("demo_qq")
    first = await provider.metadata(
        account, (now - timedelta(days=30)).isoformat(), now.isoformat()
    )
    payload["rows"][-1] = payload["rows"][-1] | {"email_id": "9", "message_id": "<new@test>"}
    with pytest.raises(AppError, match="邮箱状态发生变化"):
        await provider.verify_snapshot(account, first["mailbox_state"])


async def test_successful_compatible_sync_commits_and_reuses_bodies(mailbox):
    w, _, _, downloaded = mailbox
    before = w.accounts.get("demo_qq")["last_sync_at"]
    result = (await w.mailboxes.sync(["demo_qq"]))[0]
    assert result["ok"] and result["fetched"] == 0 and result["reused"] == 3
    assert not downloaded
    account = next(a for a in w.accounts.list() if a["id"] == "demo_qq")
    assert account["sync_complete"] and not account["error"]
    assert account["last_sync_at"] > before and account["sync_method"] == "uid_inventory"
    assert "UIDNEXT" in account["sync_diagnostics"][0]
    with w.db.session() as session:
        cursor = session.get(Coverage, "demo_qq").cursor
        assert cursor["mailbox_state"]["uidnext"] is None
        assert cursor["mailbox_state"]["inventory_complete"]
        assert cursor["mailbox_state"]["uid_digest"] and cursor["verified_at"]
    assert (await w.mailboxes.sync(["demo_qq"]))[0]["reused"] == 3
    assert not downloaded


async def test_new_uid_with_old_internaldate_in_configured_range_is_downloaded(mailbox):
    w, _, payload, downloaded = mailbox
    payload["rows"].append(
        payload["rows"][0]
        | {
            "email_id": "9",
            "message_id": "<imported@test>",
            "received_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        }
    )
    result = (await w.mailboxes.sync(["demo_qq"]))[0]
    assert result["ok"] and result["fetched"] == 1 and result["reused"] == 3
    assert downloaded == ["9"]


async def test_same_count_change_retries_before_committing_and_reconciles_absence(mailbox):
    w, provider, payload, downloaded = mailbox
    before = w.accounts.get("demo_qq")["last_sync_at"]
    original = provider.call
    changed = False

    async def call(name, args):
        nonlocal changed
        if name == "workspace_mailbox_state" and not changed:
            assert w.accounts.get("demo_qq")["last_sync_at"] == before
            payload["rows"][-1] = payload["rows"][-1] | {
                "email_id": "9",
                "message_id": "<new@test>",
            }
            changed = True
        return await original(name, args)

    provider.call = call
    progress = AsyncMock()
    result = (await w.mailboxes.sync(["demo_qq"], progress))[0]
    assert result["ok"] and result["attempts"] == 2 and downloaded == ["9"]
    assert any("重新核对" in args.args[0] for args in progress.await_args_list)
    with w.db.session() as session:
        rows = {
            row.source_id: row
            for row in session.scalars(select(Mail).where(Mail.account_id == "demo_qq"))
        }
        assert rows["5"].observed_state == "historical" and rows["9"].observed_state == "current"
    assert w.accounts.get("demo_qq")["last_sync_at"] > before


async def test_continuous_changes_stop_after_three_attempts_without_advancing(mailbox):
    w, provider, _, downloaded = mailbox
    before = w.accounts.get("demo_qq")["last_sync_at"]
    original = provider.call
    verifications = 0

    async def call(name, args):
        nonlocal verifications
        data = await original(name, args)
        if name == "workspace_mailbox_state":
            verifications += 1
            return data | {"uid_digest": "different-uid-set"}
        return data

    provider.call = call
    result = (await w.mailboxes.sync(["demo_qq"]))[0]
    assert not result["ok"] and result["attempts"] == 3 and verifications == 3
    assert result["code"] == "mailbox_changed" and not downloaded
    assert w.accounts.get("demo_qq")["last_sync_at"] == before
    assert not w.accounts.get("demo_qq")["sync_complete"]


async def test_epoch_change_refetches_bodies_then_reuses_new_epoch(mailbox):
    w, _, payload, downloaded = mailbox
    payload["epoch"] = 77
    result = (await w.mailboxes.sync(["demo_qq"]))[0]
    assert result["ok"] and result["fetched"] == 3 and result["reused"] == 0
    downloaded.clear()
    assert (await w.mailboxes.sync(["demo_qq"]))[0]["reused"] == 3
    assert not downloaded


async def test_partial_inventory_never_commits_full_progress(mailbox):
    w, _, payload, _ = mailbox
    before = w.accounts.get("demo_qq")["last_sync_at"]
    payload.update(complete=False, count=1001)
    result = (await w.mailboxes.sync(["demo_qq"]))[0]
    assert result["ok"] and result["coverage_state"] == "partial"
    assert w.accounts.get("demo_qq")["last_sync_at"] == before
    assert not w.accounts.get("demo_qq")["sync_complete"]


async def test_checkpoint_failure_retries_from_cached_body_without_duplicate_download(mailbox):
    w, _, payload, downloaded = mailbox
    before = w.accounts.get("demo_qq")["last_sync_at"]
    payload["rows"].append(
        payload["rows"][0] | {"email_id": "9", "message_id": "<checkpoint@test>"}
    )
    original = w.mailboxes.apply_observation

    def interrupted(*args, **kwargs):
        raise AppError("检查点提交中断", "checkpoint_failed")

    w.mailboxes.apply_observation = interrupted
    result = (await w.mailboxes.sync(["demo_qq"]))[0]
    assert not result["ok"] and downloaded == ["9"]
    assert w.accounts.get("demo_qq")["last_sync_at"] == before
    w.mailboxes.apply_observation = original
    downloaded.clear()
    result = (await w.mailboxes.sync(["demo_qq"]))[0]
    assert result["ok"] and result["reused"] == 4 and not downloaded


async def test_copied_message_ids_keep_distinct_uid_caches(mailbox):
    w, _, payload, downloaded = mailbox
    payload["rows"].append(payload["rows"][0] | {"email_id": "9"})
    result = (await w.mailboxes.sync(["demo_qq"]))[0]
    assert result["ok"] and downloaded == ["9"]
    with w.db.session() as session:
        copies = session.scalars(
            select(Mail).where(
                Mail.account_id == "demo_qq", Mail.message_id == payload["rows"][0]["message_id"]
            )
        ).all()
        assert len(copies) == 2 and {row.source_id for row in copies} == {"1", "9"}
    downloaded.clear()
    assert (await w.mailboxes.sync(["demo_qq"]))[0]["reused"] == 4
    assert not downloaded
    payload["rows"] = [row for row in payload["rows"] if row["email_id"] != "9"]
    assert (await w.mailboxes.sync(["demo_qq"]))[0]["ok"]
    with w.db.session() as session:
        copies = session.scalars(
            select(Mail).where(
                Mail.account_id == "demo_qq", Mail.message_id == payload["rows"][0]["message_id"]
            )
        ).all()
        assert {row.source_id: row.observed_state for row in copies} == {
            "1": "current",
            "9": "historical",
        }
