import importlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from fastapi.testclient import TestClient
from sqlalchemy import select

from mail_assistant.api.app import create_app
from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
from mail_assistant.domain.models import Mail
from mail_assistant.domain.schemas import ModelInput
from mail_assistant.infrastructure.database import record


async def test_reply_uses_saved_source_and_only_reference_linked_mails_from_same_account(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    try:
        await w.seed_demo()
        digest = await w.execute(w.tasks.claim("background"))
        with w.db.session() as session:
            source = session.scalar(select(Mail).where(Mail.subject == "课题组会议时间更新"))
            mail_id = source.id
            values = record(source)
            session.add(
                Mail(
                    **(
                        values
                        | {
                            "id": "other-account",
                            "account_id": "demo_qq",
                            "body": "另一个邮箱的副本",
                        }
                    )
                )
            )
            session.add(
                Mail(
                    **(
                        values
                        | {
                            "id": "subject-only",
                            "message_id": "<unrelated@example.invalid>",
                            "references": [],
                            "body": "仅有相同主题的邮件",
                        }
                    )
                )
            )
            source.body = "之后变化的缓存正文"
        writer = AsyncMock(return_value={"body": "老师您好，我可以参加周五 16:00 的组会。"})
        w.intelligence.draft_reply = writer
        task = w.tasks.enqueue(
            "reply_draft",
            {"mail_id": mail_id, "digest_id": digest["digest_id"], "intent": "确认参会"},
            lane="interactive",
        )
        result = await w.execute(w.tasks.claim("interactive"))
        w.tasks.finish(task["id"], result)
        intent, original, linked = writer.call_args.args
        assert intent == "确认参会" and "周五 16:00" in original["body"]
        assert len(linked) == result["related_count"] == 1
        assert linked[0]["subject"] == "课题组会议通知"
        assert result["subject"] == "Re: 课题组会议时间更新"
        assert "16:00" in result["body"] and result["clarification"] == ""
    finally:
        await w.close()


def test_reply_draft_api_checks_owner_intent_and_source(tmp_path):
    app = create_app(Settings(tmp_path, mode="demo"))
    with TestClient(app) as client:
        path = "/api/v1/mails/missing/reply-drafts"
        assert client.post(path, json={"intent": "确认参会"}).status_code == 401
        assert client.post("/api/v1/demo/start").status_code == 200
        assert client.post(path, json={"intent": "确认参会"}).status_code == 404
        mails = client.get("/api/v1/mails").json()["mails"]
        path = f"/api/v1/mails/{mails[0]['id']}/reply-drafts"
        assert client.post(path, json={"intent": "   "}).status_code == 422
        assert client.post(path, json={"intent": "回复", "digest_id": "missing"}).status_code == 404
        response = client.post(path, json={"intent": "  确认参会  "})
        assert response.status_code == 202
        task = response.json()
        assert task["kind"] == "reply_draft" and task["lane"] == "interactive"
        assert task["payload"]["intent"] == "确认参会"


@pytest.mark.parametrize(
    "answer",
    [
        {"body": "老师您好，我可以参会。", "clarification": ""},
        {"body": "", "clarification": "请确认是周五还是周六参会。"},
    ],
)
async def test_agently_generates_a_draft_or_clarification_without_tools(
    tmp_path, monkeypatch, answer
):
    def respond(request):
        payload = json.loads(request.content)
        assert not payload.get("tools")
        assert "确认参会" in json.dumps(payload["messages"], ensure_ascii=False)
        chunks = [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": json.dumps(answer, ensure_ascii=False)},
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=stream + "data: [DONE]\n\n"
        )

    provider = importlib.import_module("agently.builtins.plugins.ModelRequester.OpenAICompatible")
    monkeypatch.setattr(
        provider,
        "AsyncClient",
        lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(respond), **options),
    )
    w = Workspace(Settings(tmp_path))
    try:
        w.save_model(
            ModelInput(
                name="offline-model", base_url="https://example.invalid/v1", api_key="fixture"
            )
        )
        result = await w.intelligence.draft_reply(
            "确认参会", {"subject": "组会通知", "body": "请确认是否参会。"}, []
        )
        assert result.model_dump() == answer
    finally:
        await w.close()
