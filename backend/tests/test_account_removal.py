import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mail_assistant.api.app import create_app
from mail_assistant.application.accounts import Accounts
from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.models import Account, Task, TaskEvent
from mail_assistant.domain.schemas import AccountInput


def test_remove_api_cancels_only_queued_scoped_tasks_and_preserves_readonly_sources(tmp_path):
    app = create_app(Settings(tmp_path, mode="demo"))
    with TestClient(app) as client:
        assert client.post("/api/v1/demo/start").status_code == 200
        w = app.state.workspace
        account = w.accounts.get("demo_163")
        mails = client.get("/api/v1/mails?account_id=demo_163").json()["mails"]
        source = next(m for m in mails if "会议时间更新" in m["subject"])
        queued = w.tasks.enqueue("query", {"account_ids": ["demo_163"]})
        other = w.tasks.enqueue("sync", {"account_ids": ["demo_qq"]})
        running = w.tasks.enqueue("sync", {"account_ids": ["demo_163"]})
        with w.db.session() as session:
            session.get(Task, running["id"]).status = "running"
        assert client.delete("/api/v1/accounts/demo_163").status_code == 422
        assert client.delete("/api/v1/accounts/demo_163?expected_revision=999").status_code == 409
        result = client.delete(f"/api/v1/accounts/demo_163?expected_revision={account['revision']}")
        assert result.status_code == 200 and result.json()["removed"]
        assert result.json()["warning"] is None
        assert [a["id"] for a in client.get("/api/v1/accounts").json()] == ["demo_qq"]
        assert client.post("/api/v1/accounts/demo_163/test").status_code == 404
        detail = client.get(f"/api/v1/mails/{source['id']}")
        assert detail.status_code == 200 and detail.json()["observed_state"] == "historical"
        thread = client.get(f"/api/v1/mails/{source['id']}/thread").json()
        assert len(thread["linked"]) == 2
        assert all(mail["observed_state"] == "historical" for mail in thread["linked"])
        with w.db.session() as session:
            assert session.get(Task, queued["id"]).status == "cancelled"
            assert session.get(Task, other["id"]).status == "queued"
            assert session.get(Task, running["id"]).status == "running"
            assert session.get(Account, "demo_163").removed_at
            assert session.scalar(
                select(TaskEvent.id).where(
                    TaskEvent.task_id == queued["id"], TaskEvent.kind == "done"
                )
            )
        assert (
            client.delete(
                f"/api/v1/accounts/demo_163?expected_revision={account['revision']}"
            ).json()
            == result.json()
        )
        assert client.get("/api/v1/mails").json()["total"] == 3
        # Stopping is a distinct reversible operation, without a removed timestamp.
        assert client.post("/api/v1/accounts/demo_qq/disable").status_code == 200
        remaining = client.get("/api/v1/accounts").json()[0]
        assert not remaining["enabled"] and remaining["removed_at"] is None


async def test_unknown_remote_removal_outcome_remains_disabled_and_can_resume(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    await w.seed_demo()

    class Manager:
        calls = 0

        async def remove(self, name):
            assert name == "demo_qq"
            self.calls += 1
            if self.calls == 1:
                raise AppError("远端已写入但响应丢失", "provider_error")
            return {"already_removed": True, "cleanup_required": None}

    manager = Manager()
    accounts = Accounts(w.db, manager, mode="live")
    try:
        before = accounts.get("demo_qq")
        with pytest.raises(AppError, match="移除尚未确认"):
            await accounts.remove(before["id"], before["revision"])
        pending = accounts.get(before["id"])
        assert not pending["enabled"] and pending["status"] == "removing"
        assert pending["removed_at"] is None
        assert [a["id"] for a in w.mailboxes.selected()] == ["demo_163"]
        with pytest.raises(AppError, match="连接正在移除"):
            await accounts.save(
                AccountInput(
                    **(pending | {"enabled": True, "expected_revision": pending["revision"]})
                ),
                pending["id"],
            )
        outcome = await accounts.remove(pending["id"], pending["revision"])
        assert outcome["removed"] and outcome["warning"]
        assert accounts.get(pending["id"], include_removed=True)["status"] == "removed"
        assert await accounts.remove(pending["id"], pending["revision"]) == outcome
        assert manager.calls == 2
    finally:
        await w.close()
