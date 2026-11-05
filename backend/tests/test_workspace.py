from datetime import datetime, timedelta, timezone
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mail_assistant.api.app import create_app
from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.models import Account, Digest, Mail, Message, Task, stamp
from mail_assistant.domain.schemas import AccountInput, ModelInput, ScheduleInput
from mail_assistant.infrastructure.mail_provider import McpMailProvider, McpManager
from mail_assistant.worker import Worker


@pytest.fixture
async def workspace(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    await w.seed_demo()
    yield w
    await w.close()


async def drain(w, lane="background"):
    task = w.tasks.claim(lane)
    assert task
    result = await w.execute(task)
    w.tasks.finish(task["id"], result)
    return task, result


async def test_digest_uses_recent_mail_and_retry_returns_saved_result(workspace):
    w = workspace
    task, result = await drain(w)
    repeated = await w.execute(task)
    assert result == repeated
    with w.db.session() as s:
        digest = s.get(Digest, result["digest_id"])
        assert len(digest.items) == 5
        assert digest.delivery_status == "not_configured"
        assert s.scalar(select(func.count()).select_from(Digest)) == 1
        recent = s.scalars(select(Mail).where(Mail.pending_digest.is_(True))).all()
        assert recent == []
        assert s.scalar(select(func.count()).select_from(Mail)) == 6


async def test_multiturn_query_cites_the_meeting_update_without_duplicate_reply(workspace):
    from mail_assistant.domain.models import Conversation

    w = workspace
    with w.db.session() as s:
        conversation = Conversation(title="会议安排")
        s.add(conversation)
        s.flush()
        conversation_id = conversation.id
        s.add(Message(conversation_id=conversation_id, role="user", content="找导师的会议通知"))
    task = w.tasks.enqueue(
        "query",
        {"conversation_id": conversation_id, "question": "找导师的会议通知"},
        lane="interactive",
    )
    await drain(w, "interactive")
    with w.db.session() as s:
        s.add(Message(conversation_id=conversation_id, role="user", content="后来有更新吗？"))
    followup = w.tasks.enqueue(
        "query",
        {"conversation_id": conversation_id, "question": "后来有更新吗？"},
        lane="interactive",
    )
    _, result = await drain(w, "interactive")
    await w.execute(followup)
    with w.db.session() as s:
        reply = s.get(Message, result["message_id"])
        assert "16:00" in reply.content and "B202" in reply.content
        assert reply.sources
        assert (
            s.scalar(
                select(func.count()).select_from(Message).where(Message.task_id == followup["id"])
            )
            == 1
        )
        assert (
            s.scalar(select(func.count()).select_from(Message).where(Message.task_id == task["id"]))
            == 1
        )


async def test_account_identity_revision_and_credentials_revalidation(workspace):
    w = workspace
    original = w.accounts.get("demo_qq")
    form = AccountInput(
        **(original | {"label": "新昵称", "expected_revision": original["revision"]})
    )
    saved = await w.accounts.save(form, original["id"])
    assert saved["id"] == original["id"] and saved["status"] == "verified"
    with pytest.raises(AppError, match="配置已更新"):
        await w.accounts.save(form, original["id"])
    with pytest.raises(AppError, match="添加新账号"):
        await w.accounts.save(
            AccountInput(
                **(saved | {"email": "other@qq.com", "expected_revision": saved["revision"]})
            ),
            original["id"],
        )
    updated = await w.accounts.save(
        AccountInput(
            **(saved | {"password": "new-test-code", "expected_revision": saved["revision"]})
        ),
        original["id"],
    )
    assert updated["status"] == "unverified"
    assert "password" not in updated
    await w.accounts.disable(original["id"])
    assert all(a["id"] != original["id"] for a in w.mailboxes.selected())
    with w.db.session() as s:
        assert (
            s.scalar(
                select(func.count()).select_from(Mail).where(Mail.account_id == original["id"])
            )
            > 0
        )


async def test_model_probe_cannot_verify_a_newer_configuration(workspace):
    w = workspace

    async def change_during_probe():
        w.save_model(ModelInput(name="new-model", api_key="new-test-key"))
        return {"structured_output": True, "tool_calling": True}

    w.intelligence.probe = change_during_probe
    task = w.tasks.enqueue("model_test", {}, lane="interactive")
    with pytest.raises(AppError, match="模型配置已更改"):
        await w.execute(task)
    profile = w.model_view()
    assert profile["name"] == "new-model" and profile["status"] == "unverified"
    assert "encrypted_key" not in profile and "api_key" not in profile


async def test_model_probe_compare_and_write_race_across_workspaces(workspace):
    w = workspace
    other = Workspace(w.settings)
    compare = w.db.compare_set

    def interleaved(key, revision, value):
        if key == "model" and value.get("status") == "verified":
            other.save_model(ModelInput(name="changed-by-api", api_key="new-local-test-key"))
        return compare(key, revision, value)

    w.db.compare_set = interleaved
    try:
        task = w.tasks.enqueue("model_test", {}, lane="interactive")
        with pytest.raises(AppError, match="模型配置已更改"):
            await w.execute(task)
        assert other.model_view()["status"] == "unverified"
        assert other.model_view()["name"] == "changed-by-api"
    finally:
        await other.close()


async def test_concurrent_account_edits_do_not_accept_the_same_revision_twice(workspace):
    w = workspace
    account = w.accounts.get("demo_qq")

    async def update(*args):
        await asyncio.sleep(0)
        return {"revision": 9}

    w.accounts.mode = "live"
    w.manager.update = update
    forms = [
        AccountInput(**(account | {"label": label, "expected_revision": account["revision"]}))
        for label in ("第一次修改", "第二次修改")
    ]
    results = await asyncio.gather(
        *(w.accounts.save(form, account["id"]) for form in forms), return_exceptions=True
    )
    assert sum(isinstance(value, dict) for value in results) == 1
    assert (
        sum(isinstance(value, AppError) and value.code == "revision_conflict" for value in results)
        == 1
    )


async def test_task_dedup_retry_and_recovery(workspace):
    w = workspace
    first = w.tasks.enqueue("sync", key="one-request")
    assert w.tasks.enqueue("sync", key="one-request")["id"] == first["id"]
    # Drain seeded digest before claiming the task under test.
    await drain(w)
    claimed = w.tasks.claim("background")
    assert claimed["id"] == first["id"]
    assert w.tasks.claim("background") is None
    w.tasks.recover()
    recovered = w.tasks.claim("background")
    assert recovered["id"] == first["id"] and recovered["attempts"] == 2
    w.tasks.fail(first["id"], AppError("临时失败", "temporary", retryable=True))
    assert w.tasks.claim("background") is None
    with w.db.session() as s:
        row = s.get(Task, first["id"])
        assert row.status == "queued" and row.error_code == "temporary"
        row.available_at = stamp()
    assert w.tasks.claim("background")["id"] == first["id"]
    w.tasks.fail(first["id"], AppError("仍然失败", "temporary", retryable=True))
    with w.db.session() as s:
        assert s.get(Task, first["id"]).status == "failed"
    assert w.tasks.retry(first["id"])["attempts"] == 0


async def test_sync_keeps_successful_account_when_another_fails(workspace):
    w = workspace
    original = w.provider.metadata
    before = w.accounts.get("demo_qq")["last_sync_at"]

    async def partial(account, *args):
        if account["id"] == "demo_qq":
            raise AppError("授权码已失效", "auth_failed")
        return await original(account, *args)

    w.provider.metadata = partial
    result = await w.mailboxes.sync()
    assert {r["account_id"]: r["ok"] for r in result} == {"demo_qq": False, "demo_163": True}
    assert w.accounts.get("demo_qq")["last_sync_at"] == before
    assert not w.accounts.get("demo_qq")["sync_complete"]


async def test_missing_body_does_not_advance_the_account_watermark(workspace):
    w = workspace
    with w.db.session() as session:
        session.get(Account, "demo_qq").last_sync_at = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        # This scenario needs an uncached body; a valid cache requires no download.
        for mail in session.scalars(select(Mail).where(Mail.account_id == "demo_qq")):
            mail.source_epoch = None
    previous = w.accounts.get("demo_qq")["last_sync_at"]
    w.provider.bodies = AsyncMock(return_value=[])
    result = await w.mailboxes.sync(["demo_qq"])
    assert not result[0]["ok"] and result[0]["code"] == "incomplete_batch"
    assert w.accounts.get("demo_qq")["last_sync_at"] == previous


async def test_thread_distinguishes_reference_headers_from_subject_candidates(workspace):
    w = workspace
    meeting = w.mailboxes.search(keywords=["会议"])["mails"]
    assert len(meeting) == 2
    related = w.mailboxes.thread(meeting[0]["id"])
    assert len(related["linked"]) == 2
    unrelated = meeting[0] | {
        "email_id": "999",
        "message_id": "<unrelated@test>",
        "references": "",
        "in_reply_to": "",
        "date": stamp(),
    }
    candidate_id = w.mailboxes.upsert(w.accounts.get(meeting[0]["account_id"]), unrelated)
    related = w.mailboxes.thread(meeting[0]["id"])
    assert candidate_id not in {m["id"] for m in related["linked"]}
    assert candidate_id in {m["id"] for m in related["subject_candidates"]}


async def test_background_sync_is_independent_from_daily_digest(workspace):
    w = workspace
    await drain(w)
    w.db.set("schedule", ScheduleInput(enabled=False, sync_enabled=True).model_dump())
    worker = Worker(w)
    await worker.tick()
    with w.db.session() as s:
        queued = s.scalars(select(Task).where(Task.status == "queued")).all()
        assert [task.kind for task in queued] == ["sync"]
    await drain(w)
    w.db.set("schedule", ScheduleInput(enabled=False, sync_enabled=False).model_dump())
    await worker.tick()
    assert w.tasks.claim("background") is None


async def test_disabled_selected_account_does_not_expand_schedule_to_other_accounts(workspace):
    w = workspace
    await drain(w)
    w.db.set("schedule", ScheduleInput(enabled=True, account_ids=["demo_qq"]).model_dump())
    await w.accounts.disable("demo_qq")
    await Worker(w).tick()
    assert w.tasks.claim("background") is None
    assert w.accounts.get("demo_163")["enabled"]


def test_live_auth_bootstrap_session_and_origin(tmp_path):
    app = create_app(Settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/v1/accounts").status_code == 401
        assert client.post("/api/v1/demo/start").status_code == 404
        assert (
            client.post(
                "/api/v1/auth/bootstrap", json={"token": "wrong", "password": "test-password"}
            ).status_code
            == 403
        )
        token = app.state.workspace.secrets.bootstrap_token
        response = client.post(
            "/api/v1/auth/bootstrap", json={"token": token, "password": "test-password"}
        )
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/api/v1/auth/status").json()["authenticated"]
        assert (
            client.post(
                "/api/v1/auth/bootstrap", json={"token": token, "password": "other-password"}
            ).status_code
            == 409
        )
        assert client.put("/api/v1/onboarding", json={"complete": True}).status_code == 400
        assert client.put("/api/v1/settings/schedule", json={"enabled": True}).status_code == 400
        assert (
            client.post(
                "/api/v1/auth/logout", headers={"Origin": "https://unrelated.invalid"}
            ).status_code
            == 403
        )
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert client.get("/api/v1/accounts").status_code == 401
        assert client.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 401
        assert (
            client.post("/api/v1/auth/login", json={"password": "test-password"}).status_code == 200
        )


def test_question_submission_replay_preserves_one_message_and_task(tmp_path):
    app = create_app(Settings(tmp_path, mode="demo"))
    with TestClient(app) as client:
        assert client.post("/api/v1/demo/start").status_code == 200
        question = {"question": "会议后来有更新吗？", "request_id": "retryable-browser-request"}
        first = client.post("/api/v1/questions", json=question)
        second = client.post("/api/v1/questions", json=question)
        assert first.status_code == second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        conversation_id = first.json()["payload"]["conversation_id"]
        messages = client.get(f"/api/v1/conversations/{conversation_id}/messages").json()
        assert len(messages) == 1 and messages[0]["task_id"] == first.json()["id"]
        assert (
            client.post("/api/v1/questions", json=question | {"question": "另一条问题"}).status_code
            == 409
        )
        with app.state.workspace.db.session() as session:
            assert (
                session.scalar(select(func.count()).select_from(Task).where(Task.kind == "query"))
                == 1
            )


async def test_managed_mcp_catalog_account_lifecycle_and_read_session(tmp_path):
    """Exercise the real installed CLI and stdio protocol without a mail connection."""
    settings = Settings(tmp_path)
    manager = McpManager(settings)
    await manager.ensure()
    assert (await manager.command("config", "policy"))["allowed_mutations"] == []
    account = {
        "upstream_name": "contract_local",
        "label": "离线契约检查",
        "email": "test@example.invalid",
        "host": "127.0.0.1",
        "port": 9993,
        "upstream_revision": 1,
        "enabled": True,
    }
    await manager.add(account, "offline-test-code")
    shown = await manager.show(account["upstream_name"])
    assert shown["enabled"] and shown["incoming"]["host"] == "127.0.0.1"
    disabled = await manager.set_enabled(account["upstream_name"], False)
    assert not disabled["enabled"] and disabled["revision"] > shown["revision"]
    generation = [0]
    provider = McpMailProvider(settings, lambda: generation[0])
    try:
        assert await provider.list_accounts() == []
        await manager.update(account | {"label": "更新昵称"}, "rotated-test-code")
        # A configuration generation change reopens the actor without IMAP login.
        generation[0] += 1
        accounts = await provider.list_accounts()
        assert accounts and accounts[0]["account_name"] == account["upstream_name"]
        with pytest.raises(AppError, match="只读接口"):
            await provider.call("send_email", {})
        removed = await manager.remove(account["upstream_name"])
        assert removed["cleanup_required"] >= 0
        generation[0] += 1
        assert await provider.list_accounts() == []
        assert (await manager.remove(account["upstream_name"]))["already_removed"]
    finally:
        await provider.close()
