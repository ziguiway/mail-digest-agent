import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.models import Coverage, Digest, Mail
from mail_assistant.domain.schemas import AccountInput
from mail_assistant.infrastructure.mail_provider import McpMailProvider


@pytest.fixture
async def workspace(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    await w.seed_demo()
    yield w
    await w.close()


def cached(w, account_id="demo_qq"):
    with w.db.session() as session:
        return {
            m.source_id: {
                "id": m.id,
                "email_id": m.source_id,
                "message_id": m.message_id,
                "observed_state": m.observed_state,
                "last_observed_at": m.last_observed_at,
                "source_epoch": m.source_epoch,
            }
            for m in session.scalars(select(Mail).where(Mail.account_id == account_id))
        }


def inventory_provider(w, entries, *, complete=True, epoch=1, coverage="complete"):
    async def metadata(account, since, before, page=1):
        return {
            "emails": [],
            "total": 0,
            "page": page,
            "observations": entries,
            "membership_complete": complete,
            "coverage_state": coverage,
            "warnings": [] if coverage == "complete" else ["本次范围尚未完整确认"],
            "mailbox_state": {"uidvalidity": epoch, "uidnext": 100, "message_count": len(entries)},
        }

    w.provider.metadata = metadata


async def test_complete_inventory_reconciles_old_mail_outside_incremental_window(workspace):
    w = workspace
    original = cached(w)
    inventory_provider(w, [m for uid, m in original.items() if uid != "3"])
    result = await w.mailboxes.sync(["demo_qq"])
    assert result[0]["ok"] and result[0]["fetched"] == 0
    after = cached(w)
    assert after["3"]["observed_state"] == "historical"
    assert after["3"]["last_observed_at"] == original["3"]["last_observed_at"]
    assert after["1"]["observed_state"] == after["5"]["observed_state"] == "current"
    assert w.mailboxes.search(account_ids=["demo_qq"], cache_state="current")["total"] == 2
    history = w.mailboxes.search(account_ids=["demo_qq"], cache_state="historical")
    assert history["total"] == 1 and history["mails"][0]["id"] == original["3"]["id"]
    assert any("历史缓存" in note for note in history["warnings"])
    # No body fetch was necessary to re-observe old cached messages.
    assert len(w.mailboxes.search(account_ids=["demo_qq"])["mails"]) == 3


async def test_partial_inventory_does_not_infer_absence_or_advance_watermark(workspace):
    w = workspace
    original = cached(w)
    before = w.accounts.get("demo_qq")["last_sync_at"]
    inventory_provider(w, [original["1"]], complete=False, coverage="partial")
    result = await w.mailboxes.sync(["demo_qq"])
    assert result[0]["ok"]
    assert w.accounts.get("demo_qq")["last_sync_at"] == before
    assert {m["observed_state"] for m in cached(w).values()} == {"current"}
    assert cached(w)["3"]["last_observed_at"] == original["3"]["last_observed_at"]


async def test_uid_reset_matches_message_identity_and_does_not_reuse_old_uid(workspace):
    w = workspace
    original = cached(w)
    # UID 3 now identifies a different message. Old message 1 is now UID 9.
    inventory_provider(
        w,
        [original["1"] | {"email_id": "9"}, {"email_id": "3", "message_id": "<unrelated@test>"}],
        epoch=77,
        complete=False,
        coverage="partial",
    )
    assert (await w.mailboxes.sync(["demo_qq"]))[0]["ok"]
    after = cached(w)
    assert after["9"]["id"] == original["1"]["id"] and after["9"]["source_epoch"] == "77"
    assert after["9"]["observed_state"] == "current"
    assert after["3"]["id"] == original["3"]["id"]
    assert after["3"]["observed_state"] == "unknown" and after["3"]["source_epoch"] == "1"
    assert after["5"]["observed_state"] == "unknown"


async def test_membership_can_be_confirmed_when_receive_time_coverage_is_unknown(workspace):
    w = workspace
    original, before = cached(w), w.accounts.get("demo_qq")["last_sync_at"]
    inventory_provider(w, [original["1"]], complete=True, coverage="unknown")
    assert (await w.mailboxes.sync(["demo_qq"]))[0]["ok"]
    assert cached(w)["1"]["observed_state"] == "current"
    assert cached(w)["3"]["observed_state"] == "historical"
    assert w.accounts.get("demo_qq")["last_sync_at"] == before


async def test_failed_state_verification_never_promotes_fetched_bodies(workspace):
    w = workspace
    original, before = cached(w), w.accounts.get("demo_qq")["last_sync_at"]
    account = w.accounts.get("demo_qq")
    # Force an initial scan to read bodies again, then invalidate the snapshot.
    with w.db.session() as session:
        from mail_assistant.domain.models import Account

        session.get(Account, account["id"]).last_sync_at = None
        for mail in session.scalars(select(Mail).where(Mail.account_id == account["id"])):
            mail.source_epoch = None

    async def changed(account, state):
        raise AppError("快照在读取后变化", "mailbox_changed", retryable=True)

    w.provider.verify_snapshot = changed
    result = await w.mailboxes.sync(["demo_qq"])
    assert not result[0]["ok"] and result[0]["code"] == "mailbox_changed"
    assert {m["observed_state"] for m in cached(w).values()} == {"unknown"}
    assert cached(w)["1"]["last_observed_at"] == original["1"]["last_observed_at"]
    assert w.accounts.get("demo_qq")["last_sync_at"] is None and before


async def test_folder_switch_keeps_exact_name_old_sources_and_digest_scope(workspace):
    w = workspace
    old_ids = {m["id"] for m in cached(w).values()}
    task = w.tasks.enqueue("digest", {"account_ids": ["demo_qq"]})
    original_digest = (await w.execute(task))["digest_id"]
    before = w.accounts.get("demo_qq")
    folder = " 课程/项目 "
    saved = await w.accounts.save(
        AccountInput(**(before | {"folder": folder, "expected_revision": before["revision"]})),
        "demo_qq",
    )
    assert (
        saved["folder"] == folder
        and saved["status"] == "unverified"
        and saved["last_sync_at"] is None
    )
    with w.db.session() as session:
        assert session.get(Coverage, "demo_qq") is None
        assert all(session.get(Mail, mid).observed_state == "historical" for mid in old_ids)
        session.get(Mail, next(iter(old_ids))).pending_digest = True
        assert {item["mail_id"] for item in session.get(Digest, original_digest).items} <= old_ids
    new_task = w.tasks.enqueue("digest", {"account_ids": ["demo_qq"]})
    new_digest = (await w.execute(new_task))["digest_id"]
    with w.db.session() as session:
        current_ids = {item["mail_id"] for item in session.get(Digest, new_digest).items}
        assert len(current_ids) == 3 and not current_ids.intersection(old_ids)
        assert all(session.get(Mail, mid).folder == folder for mid in current_ids)
    assert w.mailboxes.search(account_ids=["demo_qq"], cache_state="historical")["total"] == 3
    assert w.mailboxes.read([next(iter(old_ids))])[0]["observed_state"] == "historical"


async def test_folder_task_uses_exact_names_and_rejects_stale_config(workspace):
    w = workspace
    account = w.accounts.get("demo_qq")
    task = w.tasks.enqueue(
        "account_folders",
        {"account_id": account["id"], "revision": account["revision"]},
        lane="interactive",
    )
    result = await w.execute(task)
    w.tasks.finish(task["id"], result)
    assert result["folders"][1]["name"] == "课程与项目" and not result["folders"][2]["selectable"]
    original = w.provider.list_folders

    async def changed(account):
        await w.accounts.save(
            AccountInput(
                **(
                    account
                    | {"label": "读取期间的新名称", "expected_revision": account["revision"]}
                )
            ),
            account["id"],
        )
        return await original(account)

    w.provider.list_folders = changed
    with pytest.raises(AppError, match="配置已改变"):
        await w.execute(task)


async def test_folder_mcp_contract_keeps_unicode_flags_and_reports_malformed_results(tmp_path):
    provider = McpMailProvider(Settings(tmp_path))

    async def call(name, args):
        assert name == "list_mailboxes" and args == {"account_name": "stable-account"}
        return [
            {"name": " 课程/项目 ", "delimiter": "/", "flags": []},
            {"name": "归档", "delimiter": "/", "flags": ["\\Noselect"]},
        ]

    provider.call = call
    folders = await provider.list_folders({"upstream_name": "stable-account"})
    assert folders[0]["name"] == " 课程/项目 " and folders[0]["selectable"]
    assert not folders[1]["selectable"]

    async def invalid(*args):
        return {"error": "cannot list"}

    provider.call = invalid
    with pytest.raises(AppError, match="列表无效"):
        await provider.list_folders({"upstream_name": "stable-account"})


async def test_observation_migration_retains_old_mail_as_unconfirmed(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    await w.seed_demo()
    old_ids = {m["id"] for m in cached(w).values()}
    settings = w.settings
    await w.close()
    cfg = Config()
    from pathlib import Path
    import mail_assistant

    cfg.set_main_option("script_location", str(Path(mail_assistant.__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{settings.database}")
    command.downgrade(cfg, "0002_deliveries")
    restored = Workspace(settings)
    try:
        assert {m["id"] for m in cached(restored).values()} == old_ids
        assert {m["observed_state"] for m in cached(restored).values()} == {"unknown"}
        assert (await restored.mailboxes.sync(["demo_qq"]))[0]["ok"]
        assert {m["observed_state"] for m in cached(restored).values()} == {"current"}
    finally:
        await restored.close()


async def test_malformed_inventory_isolated_to_one_account(workspace):
    w = workspace
    original = w.provider.metadata

    async def malformed(account, *args):
        result = await original(account, *args)
        return result | {"observations": None} if account["id"] == "demo_qq" else result

    w.provider.metadata = malformed
    results = await w.mailboxes.sync()
    assert {r["account_id"]: r["ok"] for r in results} == {"demo_qq": False, "demo_163": True}
    assert next(r for r in results if r["account_id"] == "demo_qq")["code"] == "mcp_contract_error"
