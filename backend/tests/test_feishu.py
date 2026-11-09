import asyncio
import json
import sys
from unittest.mock import AsyncMock
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mail_assistant.api.app import create_app
from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.models import (
    Account,
    Conversation,
    Delivery,
    Digest,
    Mail,
    Message,
    Task,
)
from mail_assistant.domain.schemas import FeishuBindInput
from mail_assistant.infrastructure.lark_cli import LarkCli, authorization_urls, json_documents
from mail_assistant.worker import Worker


def make_stub(tmp_path):
    path = tmp_path / "lark-test"
    source = Path(__file__).with_name("fixtures") / "lark_cli_stub.py"
    path.write_text(f"#!{sys.executable}\n" + source.read_text())
    path.chmod(0o700)
    return str(path)


@pytest.fixture
async def workspace(tmp_path):
    w = Workspace(Settings(tmp_path / "data", mode="demo", lark_command=make_stub(tmp_path)))
    await w.seed_demo()
    # The real service pipeline uses an executable CLI substitute and sample
    # mailbox/model providers. This never invokes a real remote service.
    w.settings = replace(w.settings, mode="live")
    w.feishu.cli = LarkCli(w.settings)
    yield w
    await w.close()


async def wait_for(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


def controls(w, **values):
    w.feishu.cli.prepare()
    path = w.settings.feishu_dir / "fake-control.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values))
    temporary.replace(path)


def event(content, message_id="om_input", **values):
    return {
        "sender_id": "ou_owner",
        "sender_type": "user",
        "chat_id": "oc_owner",
        "chat_type": "p2p",
        "message_id": message_id,
        "message_type": "text",
        "content": content,
        "create_time": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
        **values,
    }


def emit(w, payload):
    with (w.settings.feishu_dir / "fake-events.ndjson").open("a") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


async def execute(w, lane):
    task = w.tasks.claim(lane)
    assert task
    try:
        result = await w.execute(task)
    except AppError as error:
        w.tasks.fail(task["id"], error)
        raise
    w.tasks.finish(task["id"], result)
    return result


async def bind(w):
    task = w.feishu.request_binding(
        FeishuBindInput(previous_binding_id=w.feishu.state().get("binding_id", ""))
    )
    result = await execute(w, "control")
    assert result["binding_id"] == task["payload"]["binding_id"]
    return task


async def pair_without_listener(w):
    await bind(w)
    w.feishu.listener_state(w.feishu.state()["binding_id"], ready=True)
    view = w.feishu.pair()
    w.feishu.receive(event(view["pairing_code"]))
    assert w.feishu.state()["paired_at"]


def test_cli_json_and_opaque_urls():
    text = 'status line\n{\n"appId": "cli_test", "appSecret":"****"\n}\n'
    assert list(json_documents(text))[0]["appId"] == "cli_test"
    url = "https://open.feishu.cn/test?code=a%2Bb&next=x%252Fy"
    assert authorization_urls("  " + url + "\n") == [url]
    assert authorization_urls(json.dumps({"console_url": url})) == [url]


async def test_new_binding_generates_qr_and_recovers_without_a_second_registration(workspace):
    w = workspace
    task = await bind(w)
    assert w.feishu.view()["configured"]
    assert w.feishu.view()["app_id"] == "cli_course_test"
    log = (w.settings.feishu_dir / "fake-log.ndjson").read_text()
    assert "test%2Bopaque&lang=zh" in log
    png = list((w.settings.feishu_dir / "qr").glob("*.png"))[0]
    assert png.read_bytes().startswith(b"\x89PNG")
    w.feishu.change(w.feishu.state()["binding_id"], status="authorizing", configured=False)
    await w.feishu.authorize(task | {"attempts": 2})
    entries = [
        json.loads(line)
        for line in (w.settings.feishu_dir / "fake-log.ndjson").read_text().splitlines()
    ]
    assert sum(row.get("args", [])[:2] == ["config", "init"] for row in entries) == 1


async def test_cancel_waiting_authorization_cleans_up_and_keeps_task_cancelled(workspace):
    w = workspace
    controls(w, wait_authorization=True)
    task = w.feishu.request_binding(FeishuBindInput())
    claimed = w.tasks.claim("control")
    running = asyncio.create_task(w.execute(claimed))
    await wait_for(lambda: bool(w.feishu.view()["urls"]))
    w.feishu.disconnect()
    with pytest.raises(AppError, match="取消") as caught:
        await running
    w.tasks.fail(task["id"], caught.value)
    assert not w.feishu.cli.processes
    assert w.feishu.view()["status"] == "disconnected"
    with w.db.session() as session:
        assert session.get(Task, task["id"]).status == "cancelled"


async def test_expired_authorization_discards_the_old_verification_link(workspace):
    w = workspace
    controls(w, authorization_expired=True)
    w.feishu.request_binding(FeishuBindInput())
    with pytest.raises(AppError):
        await execute(w, "control")
    assert w.feishu.view()["status"] == "expired"
    assert w.feishu.view()["urls"] == []
    assert not w.feishu.cli.processes


async def test_existing_app_secret_is_stdin_only_and_not_in_task_or_logs(workspace):
    w = workspace
    secret = "private-stub-app-secret"
    task = w.feishu.request_binding(
        FeishuBindInput(mode="existing", app_id="cli_existing", app_secret=secret)
    )
    assert secret not in json.dumps(task)
    assert secret not in json.dumps(w.feishu.view())
    await execute(w, "control")
    assert secret not in (w.settings.feishu_dir / "fake-log.ndjson").read_text()
    assert w.db.get("feishu_secret:" + task["payload"]["binding_id"]) is None


async def test_cancel_after_profile_save_does_not_reactivate_the_binding(workspace):
    w = workspace
    original = w.feishu.cli.run

    async def disconnect_after_show(profile, *args, **kwargs):
        result = await original(profile, *args, **kwargs)
        if args == ("config", "show") and result.get("appId"):
            w.feishu.disconnect()
        return result

    w.feishu.cli.run = disconnect_after_show
    w.feishu.request_binding(FeishuBindInput())
    with pytest.raises(AppError, match="操作已停止"):
        await execute(w, "control")
    assert w.feishu.view()["status"] == "disconnected"
    assert not w.feishu.view()["configured"]


async def test_stream_pairing_query_dedup_multiturn_and_reply_sources(workspace):
    w = workspace
    await bind(w)
    stop = asyncio.Event()
    listener = asyncio.create_task(w.feishu.supervise(stop))
    try:
        await wait_for(lambda: w.feishu.view()["listener"]["ready"])
        view = w.feishu.pair()
        emit(w, event(view["pairing_code"], "om_wrong", chat_type="group"))
        await asyncio.sleep(0.05)
        assert not w.feishu.state().get("paired_at")
        emit(w, event(view["pairing_code"], "om_pair"))
        await wait_for(lambda: bool(w.feishu.state().get("paired_at")))
        assert w.feishu.state()["chat_id"] == "oc_owner"
        w.feishu.test_send(w.feishu.state()["binding_id"])
        await execute(w, "delivery")
        w.feishu.set_enabled(True, w.feishu.state()["binding_id"])
        first = event("找导师的会议通知", "om_question")
        emit(w, first)
        emit(w, first | {"event_id": "a-repeated-delivery"})
        await wait_for(lambda: has_query(w))
        await execute(w, "interactive")
        await execute(w, "delivery")
        await execute(w, "delivery")
        emit(w, event("那封邮件后来有更新吗？", "om_followup"))
        await wait_for(lambda: has_query(w))
        await execute(w, "interactive")
        await execute(w, "delivery")
        await execute(w, "delivery")
        with w.db.session() as session:
            conversations = session.scalars(
                select(Conversation).where(Conversation.channel == "feishu")
            ).all()
            assert len(conversations) == 1
            messages = session.scalars(
                select(Message)
                .where(Message.conversation_id == conversations[0].id, Message.role == "assistant")
                .order_by(Message.created_at)
            ).all()
            assert len(messages) == 2 and "16:00" in messages[-1].content
            assert messages[-1].sources
            assert (
                session.scalar(select(func.count()).select_from(Task).where(Task.kind == "query"))
                == 2
            )
        log = (w.settings.feishu_dir / "fake-log.ndjson").read_text()
        assert "邮件依据" in log and "--as" in log and "bot" in log
    finally:
        stop.set()
        await listener
    assert not w.feishu.cli.processes


def has_query(w):
    with w.db.session() as session:
        return (
            session.scalar(select(Task.id).where(Task.kind == "query", Task.status == "queued"))
            is not None
        )


async def test_partial_send_keeps_successful_chunks_and_retries_only_the_failed_part(workspace):
    w = workspace
    await pair_without_listener(w)
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    controls(w, send_fail_at=2)
    task = w.feishu.queue_delivery("query", "fixture-long-text", "甲" * 5000)
    with pytest.raises(AppError, match="缺少权限"):
        await execute(w, "delivery")
    row = w.feishu.deliveries()[0]
    assert row["status"] == "failed" and [chunk["status"] for chunk in row["chunks"]] == [
        "sent",
        "failed",
    ]
    assert w.feishu.view()["urls"]
    controls(w)
    w.feishu.retry_delivery(row["id"])
    await execute(w, "delivery")
    assert w.feishu.deliveries()[0]["status"] == "sent"
    assert int((w.settings.feishu_dir / "fake-send-count").read_text()) == 3
    await w.execute(task)
    assert int((w.settings.feishu_dir / "fake-send-count").read_text()) == 3


async def test_uncertain_result_requires_explicit_retry_and_preserves_the_text(workspace):
    w = workspace
    await pair_without_listener(w)
    controls(w, send_no_output=True)
    w.feishu.test_send(w.feishu.state()["binding_id"])
    with pytest.raises(AppError):
        await execute(w, "delivery")
    delivery = w.feishu.deliveries()[0]
    assert delivery["status"] == "uncertain"
    with pytest.raises(AppError, match="核对飞书"):
        w.feishu.retry_delivery(delivery["id"])
    assert int((w.settings.feishu_dir / "fake-send-count").read_text()) == 1
    controls(w)
    w.feishu.retry_delivery(delivery["id"], allow_uncertain=True)
    await execute(w, "delivery")
    assert w.feishu.view()["last_test_at"]


async def test_repair_invalidates_old_delivery_target_and_unknown_sender_is_ignored(workspace):
    w = workspace
    await pair_without_listener(w)
    task = w.feishu.test_send(w.feishu.state()["binding_id"])
    old_binding = w.feishu.state()["binding_id"]
    view = w.feishu.pair()
    assert view["binding_id"] != old_binding
    w.feishu.receive(event(view["pairing_code"], sender_id="ou_new", chat_id="oc_new"))
    with pytest.raises(AppError, match="接收对象已变化"):
        await execute(w, "delivery")
    assert not (w.settings.feishu_dir / "fake-send-count").exists()
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    w.feishu.receive(event("找会议通知", sender_id="ou_unknown"))
    assert not has_query(w)
    assert task["kind"] == "feishu_send"


async def test_interrupted_chunk_is_uncertain_after_workspace_restart(workspace):
    w = workspace
    await pair_without_listener(w)
    task = w.feishu.test_send(w.feishu.state()["binding_id"])
    claimed = w.tasks.claim("delivery")
    with w.db.session() as session:
        row = session.get(Delivery, task["payload"]["delivery_id"])
        row.status, row.chunks = "sending", [{"index": 0, "status": "sending"}]
    restored = Workspace(w.settings)
    try:
        restored.tasks.recover()
        assert claimed["id"] == restored.tasks.claim("delivery")["id"]
        with pytest.raises(AppError, match="发送结果不确定"):
            await restored.execute(claimed)
        assert restored.feishu.deliveries()[0]["status"] == "uncertain"
        assert not (w.settings.feishu_dir / "fake-send-count").exists()
    finally:
        await restored.close()


async def test_digest_generation_and_failed_delivery_remain_separate(workspace):
    w = workspace
    await pair_without_listener(w)
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    result = await execute(w, "background")
    digest_id = result["digest_id"]
    controls(w, send_fail_at=1)
    w.feishu.queue_digest(digest_id)
    with pytest.raises(AppError):
        await execute(w, "delivery")
    with w.db.session() as session:
        digest = session.get(Digest, digest_id)
        assert digest.status == "generated" and digest.delivery_status == "failed"
        assert len(digest.items) == 5
    controls(w)
    w.feishu.retry_delivery(w.feishu.deliveries()[0]["id"])
    await execute(w, "delivery")
    with w.db.session() as session:
        assert session.scalar(select(func.count()).select_from(Digest)) == 1
        assert session.get(Digest, digest_id).delivery_status == "sent"


async def test_listener_reconnects_after_a_subprocess_failure(workspace):
    w = workspace
    await bind(w)
    controls(w, consume_fail_once=True)
    stop = asyncio.Event()
    running = asyncio.create_task(w.feishu.supervise(stop))
    try:
        await wait_for(lambda: w.feishu.view()["listener"]["ready"])
        assert int((w.settings.feishu_dir / "fake-consume-count").read_text()) == 2
    finally:
        stop.set()
        await running
    assert not w.feishu.cli.processes


async def test_expired_pairing_code_cannot_bind(workspace):
    from datetime import timedelta

    w = workspace
    await bind(w)
    w.feishu.listener_state(w.feishu.state()["binding_id"], ready=True)
    view = w.feishu.pair()
    w.feishu.change(
        view["binding_id"],
        pairing_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    assert w.feishu.view()["pairing_code"] is None
    w.feishu.receive(event(view["pairing_code"]))
    assert not w.feishu.state().get("paired_at")


def test_cli_environment_is_scoped_to_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_HOME", "another-agent-workspace")
    monkeypatch.setenv("FEISHU_APP_SECRET", "unrelated-app-secret")
    monkeypatch.setenv("LARKSUITE_CLI_PROFILE", "personal")
    settings = Settings(tmp_path)
    env = settings.lark_env()
    assert env["LARKSUITE_CLI_CONFIG_DIR"] == str(settings.feishu_dir / "cli")
    assert not any(
        key in env for key in ("OPENCLAW_HOME", "FEISHU_APP_SECRET", "LARKSUITE_CLI_PROFILE")
    )


def test_feishu_api_requires_login_and_pairing_before_enabling(tmp_path):
    app = create_app(Settings(tmp_path / "api", lark_command=make_stub(tmp_path)))
    with TestClient(app) as client:
        assert client.get("/api/v1/settings/feishu").status_code == 401
        token = app.state.workspace.secrets.bootstrap_token
        assert (
            client.post(
                "/api/v1/auth/bootstrap", json={"token": token, "password": "local-test-password"}
            ).status_code
            == 200
        )
        state = client.get("/api/v1/settings/feishu").json()
        assert state["status"] == "unconfigured"
        assert (
            client.put(
                "/api/v1/settings/feishu", json={"enabled": True, "binding_id": ""}
            ).status_code
            == 400
        )
        response = client.post(
            "/api/v1/settings/feishu/bind",
            json={"mode": "existing", "app_id": "cli_test", "app_secret": "test-only-secret"},
        )
        assert response.status_code == 202 and "test-only-secret" not in response.text
        assert client.post("/api/v1/settings/feishu/disconnect").json()["status"] == "disconnected"


async def test_concise_query_reply_keeps_queued_text_on_retry_and_uses_post(workspace):
    w = workspace
    await pair_without_listener(w)
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    with w.db.session() as session:
        conversation = Conversation(title="简洁通知测试", channel="feishu")
        session.add(conversation)
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="网页上的完整文字回答保持不变",
            sources=[
                {
                    "id": "internal-source",
                    "account_label": "学习",
                    "subject": "原始主题",
                    "sender": "导师",
                }
            ],
            presentation={
                "version": 1,
                "type": "mail_table",
                "summary": "有一项需要确认。",
                "rows": [
                    {"mail_id": "internal-source", "handling": "confirm", "action": "核对时间。"}
                ],
                "notes": [],
            },
        )
        session.add(message)
        session.flush()
        message_id = message.id
    context = {"message_id": "om_question", "binding_id": w.feishu.state()["binding_id"]}
    task = w.feishu.queue_answer(message_id, context)
    with w.db.session() as session:
        delivery = session.get(Delivery, task["payload"]["delivery_id"])
        original = delivery.text
        assert "【待确认】原始主题\n下一步：核对时间。" in original
        assert "来源：学习 · 导师" in original and "internal-source" not in original
        assert session.get(Message, message_id).content == "网页上的完整文字回答保持不变"
    controls(w, send_fail_at=1)
    with pytest.raises(AppError):
        await execute(w, "delivery")
    with w.db.session() as session:
        message = session.get(Message, message_id)
        message.content, message.presentation = "后来的修改", None
    assert w.feishu.queue_answer(message_id, context)["id"] == task["id"]
    controls(w)
    w.feishu.retry_delivery(task["payload"]["delivery_id"])
    await execute(w, "delivery")
    entries = [
        json.loads(line)
        for line in (w.settings.feishu_dir / "fake-log.ndjson").read_text().splitlines()
    ]
    sent = [row for row in entries if "send_number" in row]
    assert len(sent) == 2 and sent[0]["content"] == sent[1]["content"]
    assert sent[-1]["content"] == {"zh_cn": {"content": [[{"tag": "text", "text": original}]]}}
    send_args = [
        row["args"] for row in entries if row.get("args", [])[:2] == ["im", "+messages-reply"]
    ]
    assert all(args[args.index("--msg-type") + 1] == "post" for args in send_args)
    assert all(args[args.index("--message-id") + 1] == "om_question" for args in send_args)
    assert (
        send_args[0][send_args[0].index("--idempotency-key") + 1]
        == send_args[1][send_args[1].index("--idempotency-key") + 1]
    )


async def test_digest_notification_uses_saved_sources_after_mailbox_changes(workspace):
    w = workspace
    await pair_without_listener(w)
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    result = await execute(w, "background")
    with w.db.session() as session:
        digest = session.get(Digest, result["digest_id"])
        old_labels = {source["account_label"] for source in digest.sources}
        for account in session.scalars(select(Account)):
            account.label = "后来改名的邮箱"
        for mail in session.scalars(select(Mail)):
            mail.sender = "后来修改的发件人"
    task = w.feishu.queue_digest(result["digest_id"])
    with w.db.session() as session:
        delivery = session.get(Delivery, task["payload"]["delivery_id"])
        assert all(label in delivery.text for label in old_labels)
        assert "后来改名" not in delivery.text and "后来修改" not in delivery.text
        assert "范围：" in delivery.text and "版本 1" in delivery.text
        assert "截止：" in delivery.text
        assert all(item["mail_id"] not in delivery.text for item in digest.items)
        original = delivery.text
    await execute(w, "delivery")
    assert w.feishu.queue_digest(result["digest_id"])["id"] == task["id"]
    with w.db.session() as session:
        assert session.get(Delivery, task["payload"]["delivery_id"]).text == original


async def test_query_ack_is_sent_before_model_execution_and_deduplicates(workspace):
    w = workspace
    await pair_without_listener(w)
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    incoming = event("今天有什么需要处理？", "om_ack")
    w.feishu.receive(incoming)
    w.feishu.receive(incoming)
    await execute(w, "delivery")
    with w.db.session() as session:
        queries = session.scalars(select(Task).where(Task.kind == "query")).all()
        assert len(queries) == 1 and queries[0].status == "queued"
        acknowledgements = session.scalars(
            select(Delivery).where(Delivery.kind == "request_ack")
        ).all()
        assert len(acknowledgements) == 1 and acknowledgements[0].status == "sent"
        assert acknowledgements[0].reply_to == "om_ack"
        assert "已收到，正在检索相关邮件并生成回答" in acknowledgements[0].text
    assert int((w.settings.feishu_dir / "fake-send-count").read_text()) == 1


async def test_terminal_failure_replies_once_and_temporary_retry_does_not(workspace):
    w = workspace
    await pair_without_listener(w)
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    w.feishu.receive(event("查询邮件", "om_failure"))
    claimed = w.tasks.claim("interactive")
    w.tasks.fail(
        claimed["id"], AppError("模型服务响应超时，请稍后重试", "model_timeout", retryable=True)
    )
    w.feishu.reconcile_notifications()
    assert not [d for d in w.feishu.deliveries() if d["kind"] == "request_error"]
    with w.db.session() as session:
        task = session.get(Task, claimed["id"])
        task.available_at = task.created_at
        task.max_attempts = 2
    claimed = w.tasks.claim("interactive")
    w.tasks.fail(
        claimed["id"], AppError("模型服务响应超时，请稍后重试", "model_timeout", retryable=True)
    )
    w.feishu.reconcile_notifications()
    w.feishu.reconcile_notifications()
    with w.db.session() as session:
        failures = session.scalars(select(Delivery).where(Delivery.kind == "request_error")).all()
        assert len(failures) == 1 and failures[0].reply_to == "om_failure"
        assert "邮件查询未完成：模型服务响应超时" in failures[0].text
    await execute(w, "delivery")
    await execute(w, "delivery")
    assert int((w.settings.feishu_dir / "fake-send-count").read_text()) == 2


async def test_reconciliation_recovers_missing_ack_and_does_not_backfill_old_tasks(
    workspace, monkeypatch
):
    w = workspace
    await pair_without_listener(w)
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    original = w.feishu.queue_request_ack
    monkeypatch.setattr(w.feishu, "queue_request_ack", lambda task: None)
    w.feishu.receive(event("恢复后查询", "om_recovery"))
    monkeypatch.setattr(w.feishu, "queue_request_ack", original)
    assert not w.feishu.deliveries()
    w.tasks.enqueue(
        "query",
        {"feishu": {"binding_id": w.feishu.state()["binding_id"], "message_id": "om_old"}},
        lane="interactive",
    )
    w.feishu.reconcile_notifications()
    w.feishu.reconcile_notifications()
    with w.db.session() as session:
        rows = session.scalars(select(Delivery)).all()
        assert len(rows) == 1 and rows[0].reply_to == "om_recovery"


async def test_reconciliation_never_redirects_old_binding_feedback(workspace):
    w = workspace
    await pair_without_listener(w)
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    w.feishu.receive(event("旧绑定的问题", "om_old_binding"))
    task = w.tasks.claim("interactive")
    w.tasks.fail(task["id"], AppError("失败", "test_failure"))
    w.feishu.pair()
    w.feishu.receive(event(w.feishu.view()["pairing_code"], sender_id="ou_new", chat_id="oc_new"))
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    w.feishu.reconcile_notifications()
    assert not [d for d in w.feishu.deliveries() if d["kind"] == "request_error"]


async def test_worker_reports_terminal_model_failure_to_feishu(workspace, monkeypatch):
    w = workspace
    await pair_without_listener(w)
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    w.feishu.receive(event("模型失败也请回复", "om_worker_failure"))
    monkeypatch.setattr(
        w.intelligence,
        "answer",
        AsyncMock(side_effect=AppError("模型返回结构错误", "model_output_error")),
    )
    worker = Worker(w)
    running = asyncio.create_task(worker.lane("interactive"))
    try:
        await wait_for(lambda: any(d["kind"] == "request_error" for d in w.feishu.deliveries()))
    finally:
        worker.stop.set()
        await running
    await execute(w, "delivery")
    await execute(w, "delivery")
    with w.db.session() as session:
        reply = session.scalar(select(Delivery).where(Delivery.kind == "request_error"))
        assert reply.status == "sent" and reply.reply_to == "om_worker_failure"


@pytest.mark.parametrize("partial", [False, True])
async def test_digest_ack_has_a_final_reply_for_empty_or_partial_results(
    workspace, monkeypatch, partial
):
    w = workspace
    await pair_without_listener(w)
    w.feishu.change(w.feishu.state()["binding_id"], enabled=True)
    w.feishu.receive(event("/digest", "om_empty_digest"))
    monkeypatch.setattr(
        w, "generate_digest", AsyncMock(return_value={"empty": not partial, "partial": partial})
    )
    with w.db.session() as session:
        task = session.scalar(
            select(Task).where(
                Task.kind == "digest", Task.payload["feishu"]["notify"].as_boolean().is_(True)
            )
        )
        task = {column.name: getattr(task, column.name) for column in task.__table__.columns}
    await w.execute(task)
    await w.execute(task)
    with w.db.session() as session:
        messages = session.scalars(select(Delivery).where(Delivery.kind == "digest_status")).all()
        assert len(messages) == 1 and messages[0].reply_to == "om_empty_digest"
        assert ("暂时无法给出完整摘要" if partial else "没有符合条件的邮件") in messages[0].text
