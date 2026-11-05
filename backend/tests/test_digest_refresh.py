import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mail_assistant.api.app import create_app
from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.models import Digest, DigestGroup, Mail, MailAnalysis, Task
from mail_assistant.domain.schemas import DigestInput, ScheduleInput
from mail_assistant.infrastructure import demo
from mail_assistant.worker import Worker


@pytest.fixture
async def w(tmp_path):
    workspace = Workspace(Settings(tmp_path, mode="demo"))
    await workspace.seed_demo()
    task = workspace.tasks.claim("background")
    result = await workspace.execute(task)
    workspace.tasks.finish(task["id"], result)
    yield workspace
    await workspace.close()


async def refresh(w, *, ids=None, force=False, cutoff=None):
    task = w.digests.enqueue(
        DigestInput(account_ids=ids or [], force_reanalyze=force), cutoff=cutoff
    )
    result = await w.execute(task)
    w.tasks.finish(task["id"], result)
    return result


def rows(w):
    with w.db.session() as session:
        return [
            dict(id=d.id, revision=d.revision, items=d.items, group=d.group_id)
            for d in session.scalars(select(Digest).order_by(Digest.created_at))
        ]


def add_mails(monkeypatch, *, replacement=False):
    original = demo.fixtures

    def changed(account):
        messages = original(account)
        if account["provider"] == "qq":
            if replacement:
                messages[-1] = messages[-1] | {
                    "email_id": "9",
                    "message_id": "<replacement@test>",
                    "body": "替换后的邮件",
                }
            else:
                messages.extend(
                    [
                        messages[0]
                        | {
                            "email_id": uid,
                            "message_id": f"<new-{uid}@test>",
                            "subject": f"新增邮件 {uid}",
                            "body": "请确认新的课程事项。",
                        }
                        for uid in ("7", "9")
                    ]
                )
        return messages

    monkeypatch.setattr(demo, "fixtures", changed)


async def test_repeated_refresh_keeps_five_mails_without_reanalysis_or_new_versions(w):
    original = rows(w)[0]
    w.intelligence.summarize = AsyncMock(wraps=w.intelligence.summarize)
    for _ in range(3):
        result = await refresh(w)
        assert result["digest_id"] == original["id"] and result["mail_count"] == 5
        assert result["reused"] and result["reused_analysis"] == 5
        assert result["message"] == "已是最新，内容未变化"
    assert len(rows(w)) == 1 and not w.intelligence.summarize.called
    with w.db.session() as session:
        assert not session.scalars(select(Mail).where(Mail.pending_digest.is_(True))).all()


async def test_new_two_mails_return_full_seven_and_keep_original_revision(w, monkeypatch):
    first = rows(w)[0]
    add_mails(monkeypatch)
    w.intelligence.summarize = AsyncMock(wraps=w.intelligence.summarize)
    result = await refresh(w)
    assert result["mail_count"] == 7 and result["added"] == 2 and result["analyzed"] == 2
    assert result["reused_analysis"] == 5
    assert len(w.intelligence.summarize.call_args.args[0]) == 2
    versions = rows(w)
    assert len(versions) == 2 and versions[0] == first
    assert versions[1]["revision"] == 2 and len(versions[1]["items"]) == 7
    assert versions[1]["group"] == versions[0]["group"]


async def test_same_count_replacement_changes_version(w, monkeypatch):
    add_mails(monkeypatch, replacement=True)
    result = await refresh(w)
    assert result["mail_count"] == 5 and result["added"] == result["removed"] == 1
    assert not result["reused"] and rows(w)[-1]["revision"] == 2


async def test_mailbox_selection_has_its_own_group_and_reuses_analysis(w):
    result = await refresh(w, ids=["demo_qq"])
    assert result["mail_count"] == 3 and result["reused_analysis"] == 3
    assert rows(w)[0]["group"] != rows(w)[-1]["group"]


async def test_force_and_expired_analysis_recompute_without_duplicate_unchanged_version(w):
    w.intelligence.summarize = AsyncMock(wraps=w.intelligence.summarize)
    forced = await refresh(w, force=True)
    assert forced["analyzed"] == 5 and forced["reused"]
    with w.db.session() as session:
        for cached in session.scalars(select(MailAnalysis)):
            cached.valid_until = "2000-01-01T00:00:00.000+00:00"
    refreshed = await refresh(w)
    assert refreshed["analyzed"] == 5 and refreshed["reused"]
    assert w.intelligence.summarize.call_count == 2 and len(rows(w)) == 1


async def test_failed_analysis_preserves_previous_result_and_check_time(w):
    previous = w.digests.latest()
    w.intelligence.summarize = AsyncMock(side_effect=AppError("测试模型失败", "model_output_error"))
    task = w.digests.enqueue(DigestInput(force_reanalyze=True))
    with pytest.raises(AppError):
        await w.execute(task)
    current = w.digests.latest()
    assert current["id"] == previous["id"] and current["checked_at"] == previous["checked_at"]
    assert current["refresh_error"] == "测试模型失败" and len(rows(w)) == 1


async def test_request_replay_and_inflight_coalescing_keep_original_cutoff(w):
    form = DigestInput(request_id="stable-refresh")
    first = w.digests.enqueue(form)
    replay = w.digests.enqueue(form, cutoff=datetime.now(timezone.utc) + timedelta(days=1))
    assert (
        first["id"] == replay["id"]
        and first["payload"]["range_end"] == replay["payload"]["range_end"]
    )
    assert w.digests.enqueue(DigestInput(request_id="another-click"))["id"] == first["id"]
    with pytest.raises(AppError, match="其他刷新参数"):
        w.digests.enqueue(DigestInput(account_ids=["demo_qq"], request_id="stable-refresh"))
    result = await w.execute(first)
    assert await w.execute(first) == result
    w.tasks.finish(first["id"], result)
    assert w.digests.enqueue(DigestInput(request_id="another-click"))["id"] == first["id"]


async def test_parallel_workspaces_share_generation_receipt(w):
    other = Workspace(w.settings)
    original = w.intelligence.summarize

    async def slow(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await original(*args, **kwargs)

    summarize = AsyncMock(side_effect=slow)
    w.intelligence.summarize = other.intelligence.summarize = summarize
    task = w.digests.enqueue(DigestInput(force_reanalyze=True))
    try:
        a, b = await asyncio.gather(w.execute(task), other.execute(task))
        assert a == b and summarize.call_count == 1 and len(rows(w)) == 1
    finally:
        await other.close()


async def test_frozen_window_is_half_open_and_crosses_local_midnight(w):
    cutoff = datetime(2026, 11, 4, 16, 30, tzinfo=timezone.utc)  # 11/05 00:30 Shanghai
    w.mailboxes.sync = AsyncMock(
        return_value=[{"account_id": "demo_qq", "ok": True, "coverage_state": "complete"}]
    )
    with w.db.session() as session:
        mails = session.scalars(select(Mail).order_by(Mail.id)).all()
        for mail in mails:
            mail.received_at = (cutoff - timedelta(hours=25)).isoformat(timespec="milliseconds")
        mails[0].received_at = (cutoff - timedelta(hours=1)).isoformat(timespec="milliseconds")
        mails[1].received_at = (cutoff - timedelta(hours=24)).isoformat(timespec="milliseconds")
        mails[2].received_at = cutoff.isoformat(timespec="milliseconds")
    result = await refresh(w, cutoff=cutoff)
    assert result["mail_count"] == 2
    with w.db.session() as session:
        group = session.get(DigestGroup, result["group_id"])
        assert group.day == "2026-11-05"


async def test_first_true_empty_creates_no_digest_and_partial_empty_does_not_hide_previous(w):
    w.mailboxes.sync = AsyncMock(return_value=[{"ok": True, "coverage_state": "complete"}])
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    count = len(rows(w))
    result = await refresh(w, cutoff=cutoff)
    assert result["empty"] and result["digest_id"] is None and len(rows(w)) == count
    previous = w.digests.latest()
    w.mailboxes.sync = AsyncMock(
        return_value=[
            {"ok": False, "coverage_state": "unknown"},
            {"ok": True, "coverage_state": "complete"},
        ]
    )
    with w.db.session() as session:
        for mail in session.scalars(select(Mail)):
            mail.observed_state = "unknown"
    result = await refresh(w)
    assert result["partial"] and not result["empty"] and result["digest_id"] == previous["id"]
    assert len(rows(w)) == count


async def test_window_expiry_can_record_true_empty_but_not_repeated_empty_versions(w):
    w.mailboxes.sync = AsyncMock(return_value=[{"ok": True, "coverage_state": "complete"}])
    cutoff = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)
    with w.db.session() as session:
        mails = session.scalars(select(Mail)).all()
        for mail in mails:
            mail.received_at = (cutoff - timedelta(hours=25)).isoformat(timespec="milliseconds")
        mails[0].received_at = (cutoff - timedelta(hours=24) + timedelta(minutes=1)).isoformat(
            timespec="milliseconds"
        )
    first = await refresh(w, cutoff=cutoff)
    assert first["mail_count"] == 1
    expired = await refresh(w, cutoff=cutoff + timedelta(minutes=2))
    assert expired["empty"] and expired["digest_id"] and expired["removed"] == 1
    count = len(rows(w))
    assert (await refresh(w, cutoff=cutoff + timedelta(minutes=3)))["reused"]
    assert len(rows(w)) == count


async def test_manual_refresh_does_not_consume_scheduled_digest_or_send_it(w):
    w.feishu.state = lambda: {"enabled": True}
    w.feishu.queue_digest = Mock()
    result = await refresh(w)
    assert not w.feishu.queue_digest.called
    local = datetime.now(timezone.utc) + timedelta(hours=8) - timedelta(minutes=1)
    w.db.set(
        "schedule",
        ScheduleInput(
            enabled=True, sync_enabled=False, digest_time=local.strftime("%H:%M")
        ).model_dump(),
    )
    await Worker(w).tick()
    with w.db.session() as session:
        task = session.scalar(select(Task).where(Task.idempotency_key.like("daily:%")))
        assert task and task.payload["range_end"]
        scheduled = {c.name: getattr(task, c.name) for c in task.__table__.columns}
    daily = await w.execute(scheduled)
    assert daily["mail_count"] == 5 and daily["digest_id"] == result["digest_id"]
    w.feishu.queue_digest.assert_called_once()


async def test_snapshot_source_survives_later_mail_changes(w):
    first = w.digests.latest()
    mail_id = first["items"][0]["mail_id"]
    with w.db.session() as session:
        mail = session.get(Mail, mail_id)
        original = mail.body
        mail.body = "后来修改的缓存"
    with TestClient(create_app(w.settings)) as client:
        client.post("/api/v1/demo/start")
        response = client.get(f"/api/v1/digests/{first['id']}/sources/{mail_id}")
        assert response.status_code == 200 and response.json()["body"] == original
        view = client.get(f"/api/v1/digests/{first['id']}").json()
        assert "sources" not in view and view["revision"] == 1


async def test_model_revision_invalidates_analysis_cache(w):
    profile = w.db.get("model", {})
    w.db.set("model", profile | {"revision": profile.get("revision", 0) + 1})
    w.intelligence.summarize = AsyncMock(wraps=w.intelligence.summarize)
    result = await refresh(w)
    assert result["analyzed"] == 5 and result["reused_analysis"] == 0
    assert w.intelligence.summarize.call_count == 1


async def test_static_analysis_cache_lasts_beyond_urgent_deadline_cache(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    now = datetime.now(timezone.utc)
    static = w.digests.cache_expiry(now, {"summary": "资讯通知", "deadline": None}, "Asia/Shanghai")
    urgent = w.digests.cache_expiry(
        now, {"deadline": (now + timedelta(hours=1)).isoformat()}, "Asia/Shanghai"
    )
    assert datetime.fromisoformat(static) > now + timedelta(hours=23)
    assert datetime.fromisoformat(urgent) <= now + timedelta(minutes=15)
    await w.close()


async def test_upgrade_preserves_legacy_nonempty_and_empty_digests(tmp_path):
    import sqlite3
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    import mail_assistant

    settings = Settings(tmp_path, mode="demo")
    original = Workspace(settings)
    await original.seed_demo()
    task = original.tasks.claim("background")
    generated = await original.execute(task)
    await original.close()
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(mail_assistant.__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{settings.database}")
    command.downgrade(cfg, "0004_removed_connections")
    with sqlite3.connect(settings.database) as connection:
        connection.execute(
            "INSERT INTO digests(id,title,items,coverage,status,delivery_status,created_at) VALUES(?,?,?,?,?,?,?)",
            ("old-empty", "旧版空摘要", "[]", "[]", "generated", "not_configured", now_stamp()),
        )
    restored = Workspace(settings)
    try:
        values = {d["id"]: d for d in restored.digests.list()}
        assert set(values) == {generated["digest_id"], "old-empty"}
        assert len(values[generated["digest_id"]]["items"]) == 5
        assert all(d["legacy"] and d["range_start"] is None for d in values.values())
    finally:
        await restored.close()


def now_stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
