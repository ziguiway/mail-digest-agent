import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from mail_assistant.application.workspace import QueryTools, Workspace
from mail_assistant.config import Settings
from mail_assistant import __file__ as package_file
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.models import Conversation, Mail, Message
from mail_assistant.domain.query_answers import present_query_answer, validate_query_answer
from mail_assistant.domain.schemas import QuestionInput


def answer(rows=None, ids=None):
    return {
        "answer": "有一项需要确认。",
        "mail_ids": ids or ["mail-1"],
        "rows": rows
        if rows is not None
        else [{"mail_id": "mail-1", "handling": "confirm", "action": "核对是否本人操作。"}],
    }


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (answer(ids=["unread"]), "invalid_sources"),
        (
            answer(rows=[{"mail_id": "unread", "handling": "confirm", "action": "核对。"}]),
            "invalid_sources",
        ),
        (answer(ids=["mail-1", "mail-2"]), "invalid_sources"),
        (
            answer(rows=[{"mail_id": "mail-1", "handling": "high", "action": "核对。"}]),
            "model_output_error",
        ),
        (
            answer(rows=[{"mail_id": "mail-1", "handling": "confirm", "action": ""}]),
            "model_output_error",
        ),
        (
            answer(rows=[{"mail_id": "mail-1", "handling": "notice", "action": "查看。"}] * 2),
            "model_output_error",
        ),
    ],
)
def test_table_rejects_unread_missing_duplicate_or_invalid_rows(value, code):
    with pytest.raises(AppError) as caught:
        validate_query_answer(value, {"mail-1": {}, "mail-2": {}})
    assert caught.value.code == code


def test_presentation_keeps_complete_text_and_uses_source_title():
    value = answer()
    value["rows"][0]["subject"] = "模型补造的标题"
    evidence = {"mail-1": {"subject": "真实来源标题", "body_truncated": True}}
    normalized = validate_query_answer(value, evidence)
    content, presentation = present_query_answer(normalized, evidence, ["部分邮箱覆盖未确认"])
    assert "真实来源标题" in content and "核对是否本人操作" in content
    assert "模型补造的标题" not in content and "subject" not in presentation["rows"][0]
    assert presentation["version"] == 1 and presentation["type"] == "mail_table"
    assert presentation["summary"] == value["answer"]
    assert any("正文片段" in note for note in presentation["notes"])
    assert any("覆盖未确认" in note for note in presentation["notes"])


def test_legacy_answer_keeps_plain_text_and_deduplicates_references():
    value = validate_query_answer(
        {"answer": "会议地点改为 B202。", "mail_ids": ["mail-1", "mail-1"]}, {"mail-1": {}}
    )
    text, presentation = present_query_answer(value, {}, ["来源为历史缓存"])
    assert presentation is None and value["mail_ids"] == ["mail-1"]
    assert text == "覆盖提示：来源为历史缓存\n\n会议地点改为 B202。"


async def test_overview_table_is_persisted_with_plain_text_and_replayed_once(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    try:
        await w.seed_demo()
        task = w.questions.submit(
            QuestionInput(question="今天有什么需要我处理的邮件？", request_id="table-overview")
        )
        result = await w.execute(task)
        assert await w.execute(task) == result
        with w.db.session() as session:
            message = session.get(Message, result["message_id"])
            value = message.presentation
            assert value["rows"] and value["summary"]
            assert {row["mail_id"] for row in value["rows"]} == {
                source["id"] for source in message.sources
            }
            assert any(row["handling"] == "notice" for row in value["rows"])
            for source in message.sources:
                assert source["subject"] in message.content and "received_at" in source
            messages = list(
                session.scalars(
                    select(Message).where(
                        Message.task_id == task["id"], Message.role == "assistant"
                    )
                )
            )
            assert len(messages) == 1
    finally:
        await w.close()


async def test_evidence_reports_actual_model_visible_body_truncation(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    try:
        await w.seed_demo()
        await w.mailboxes.sync(None)
        with w.db.session() as session:
            mail = session.scalar(select(Mail))
            mail.body, mail.body_truncated = "正文" * 7000, False
            mail_id = mail.id
        task = w.tasks.enqueue("query", {})
        tools = QueryTools(w, None, task["id"])
        result = await tools.read_mails([mail_id])
        assert len(result["mails"][0]["body"]) == 12000
        assert tools.evidence[mail_id]["body_truncated"]
        assert tools.evidence[mail_id] == result["mails"][0]
        await tools.get_thread(mail_id)
        assert tools.evidence[mail_id]["body_truncated"]
    finally:
        await w.close()


async def test_message_migration_preserves_legacy_content_and_sources(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    with w.db.session() as session:
        conversation = Conversation(title="迁移前对话")
        session.add(conversation)
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="原有完整回答",
            sources=[{"id": "old-mail"}],
        )
        session.add(message)
        session.flush()
        message_id = message.id
    path = w.settings.database
    await w.close()
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(package_file).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + str(path))
    command.downgrade(cfg, "0005_digest_refresh")
    command.upgrade(cfg, "head")
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "select content,sources,presentation from messages where id=?", (message_id,)
        ).fetchone()
        assert row[0] == "原有完整回答" and "old-mail" in row[1] and row[2] is None
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"
