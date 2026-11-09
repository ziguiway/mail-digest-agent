from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from mail_assistant.agents.intelligence import AgentlyIntelligence
from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.models import Task, TaskEvent
from mail_assistant.domain.schemas import ModelInput


class FakeAgent:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
        self.loop = None
        self.actions = None
        self.context = None
        self.settings = {}

    def set_settings(self, key, value):
        self.settings[key] = value

    def set_action_loop(self, **options):
        self.loop = options

    def use_actions(self, actions, **options):
        self.actions = actions, options

    def input(self, value):
        return self

    def info(self, value):
        self.context = value
        return self

    def instruct(self, value):
        return self

    def output(self, value):
        return self

    def get_result(self):
        return self

    def create_request(self):
        return self

    async def async_get_data(self):
        if self.error:
            raise self.error
        return self.result


def intelligence(monkeypatch, agent):
    value = AgentlyIntelligence(None, None)
    monkeypatch.setattr(value, "agent", lambda: agent)
    return value


def tools(evidence=None):
    async def search_mails(keywords: list[str], limit: int = 20) -> dict:
        return {"mails": []}

    async def read_mails(mail_ids: list[str]) -> dict:
        return {"mails": []}

    async def get_thread(mail_id: str) -> dict:
        return {"linked": []}

    return SimpleNamespace(
        search_mails=search_mails,
        read_mails=read_mails,
        get_thread=get_thread,
        calls=["search_mails", "read_mails"],
        evidence=evidence or {},
        coverage=lambda: [],
    )


async def test_query_bounds_planning_and_preserves_source_validation(monkeypatch):
    agent = FakeAgent({"answer": "请查看课程安排。", "mail_ids": ["mail-1"]})
    model = intelligence(monkeypatch, agent)
    history = [
        {
            "id": "internal",
            "task_id": "hidden",
            "role": "user",
            "content": "课程安排？",
            "sources": [],
        }
    ]
    result = await model.answer("今天有什么需要处理？", history, tools({"mail-1": {}}))
    assert result["mail_ids"] == ["mail-1"]
    assert agent.loop == {"max_rounds": 4, "planning_protocol": "native_tool_calls"}
    assert agent.actions[1] == {"always": True}
    specs = agent.settings["OpenAICompatible.request_options.tools"]
    assert {spec["function"]["name"] for spec in specs} == {
        "search_mails",
        "read_mails",
        "get_thread",
    }
    search = specs[0]["function"]["parameters"]
    assert search["required"] == ["keywords"] and search["properties"]["limit"]["default"] == 20
    assert agent.context["history"] == [{"role": "user", "content": "课程安排？", "sources": []}]
    assert agent.context["evidence"] == [{}]
    agent.result["mail_ids"] = ["unread-mail"]
    with pytest.raises(AppError) as caught:
        await model.answer("课程安排？", [], tools({"mail-1": {}}))
    assert caught.value.code == "invalid_sources"


async def test_final_generation_uses_current_evidence_without_old_answer_statistics(monkeypatch):
    agent = FakeAgent({"answer": "本轮结论。", "mail_ids": ["mail-1"], "rows": []})
    model = intelligence(monkeypatch, agent)
    await model.answer(
        "继续查询",
        [{"role": "assistant", "content": "旧统计：邮箱共一封", "sources": [{"id": "old"}]}],
        tools({"mail-1": {"id": "mail-1", "body": "当前正文"}}),
    )
    assert agent.context["history"] == [{"role": "assistant", "sources": [{"id": "old"}]}]
    assert agent.context["evidence"] == [{"id": "mail-1", "body": "当前正文"}]
    assert "旧统计" not in str(agent.context)


async def test_found_but_unread_mails_are_not_reported_as_no_mail(monkeypatch):
    agent = FakeAgent({"answer": "没有邮件", "mail_ids": [], "rows": []})
    model = intelligence(monkeypatch, agent)
    mail_tools = tools()
    mail_tools.searches = [{"total": 6}]
    with pytest.raises(AppError) as caught:
        await model.answer("今天需要处理什么？", [], mail_tools)
    assert caught.value.code == "mail_read_incomplete"


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (TimeoutError("private-canary"), "model_timeout", True),
        (httpx.ConnectError("private-canary"), "model_connection_failed", True),
        (RuntimeError("Status Code: 401\nprivate-canary"), "model_auth_failed", False),
        (RuntimeError("Status Code: 402\nprivate-canary"), "model_quota_exceeded", False),
        (RuntimeError("Status Code: 429\nprivate-canary"), "model_rate_limited", True),
        (ValueError("private-canary"), "model_query_error", False),
        (
            ValueError("Can not satisfy ensure keys ['answer'] within 3 retries. private-canary"),
            "model_output_error",
            False,
        ),
    ],
)
async def test_query_failures_keep_the_actual_safe_cause(monkeypatch, error, code, retryable):
    model = intelligence(monkeypatch, FakeAgent(error=error))
    with pytest.raises(AppError) as caught:
        await model.answer("今天的邮件？", [], tools())
    assert caught.value.code == code and caught.value.retryable is retryable
    assert "private-canary" not in caught.value.message
    assert "缩小问题" not in caught.value.message


async def test_claiming_retry_clears_current_error_but_keeps_failure_event(tmp_path):
    w = Workspace(Settings(tmp_path, mode="demo"))
    try:
        queued = w.tasks.enqueue("query", {}, lane="interactive")
        first = w.tasks.claim("interactive")
        assert first["id"] == queued["id"]
        w.tasks.fail(
            first["id"], AppError("模型服务响应超时，请稍后重试", "model_timeout", retryable=True)
        )
        with w.db.session() as session:
            row = session.get(Task, first["id"])
            row.available_at = row.created_at
        second = w.tasks.claim("interactive")
        assert second["attempts"] == 2 and second["error"] is None and second["error_code"] is None
        with w.db.session() as session:
            failures = list(
                session.scalars(
                    select(TaskEvent).where(
                        TaskEvent.task_id == first["id"], TaskEvent.kind == "error"
                    )
                )
            )
            assert len(failures) == 1 and failures[0].data["code"] == "model_timeout"
    finally:
        await w.close()


import asyncio
import importlib
import json


@pytest.mark.parametrize("empty", [False, True])
async def test_real_agently_native_wire_searches_reads_and_cites_without_provider_calls(
    tmp_path, monkeypatch, empty
):
    class MailTools:
        def __init__(self):
            self.calls, self.evidence = [], {}

        def coverage(self):
            return []

        async def search_mails(self, keywords: list[str]) -> dict:
            self.calls.append("search_mails")
            return {
                "mails": [] if empty else [{"id": "mail-1", "subject": "课程安排"}],
                "total": 0 if empty else 1,
            }

        async def read_mails(self, mail_ids: list[str]) -> dict:
            assert mail_ids == ["mail-1"]
            self.calls.append("read_mails")
            self.evidence["mail-1"] = {"id": "mail-1", "body": "请提交课程作业。"}
            return {"mails": [self.evidence["mail-1"]]}

        async def get_thread(self, mail_id: str) -> dict:
            raise AssertionError("No thread lookup is needed")

    mail_tools = MailTools()
    mail_tools.searches = [{"total": 0}] if empty else []
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get("tools"):
            assert {spec["function"]["name"] for spec in payload["tools"]} == {
                "search_mails",
                "read_mails",
                "get_thread",
            }
        else:
            assert mail_tools.calls == (
                ["search_mails"] if empty else ["search_mails", "read_mails"]
            )
        if not mail_tools.calls:
            name, arguments = "search_mails", {"keywords": []}
        elif not empty and "read_mails" not in mail_tools.calls:
            name, arguments = "read_mails", {"mail_ids": ["mail-1"]}
        else:
            name, arguments = None, None
        if name:
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-" + name,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ]
            }
        else:
            delta = {
                "content": json.dumps(
                    {
                        "answer": "有一项课程作业需要处理。",
                        "mail_ids": [] if empty else ["mail-1"],
                        "rows": []
                        if empty
                        else [
                            {
                                "mail_id": "mail-1",
                                "handling": "action",
                                "action": "请提交课程作业。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            }
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "tool_calls" if name else "stop"}
                ]
            },
        ]
        stream = (
            "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=stream)

    client = httpx.AsyncClient
    provider = importlib.import_module("agently.builtins.plugins.ModelRequester.OpenAICompatible")
    monkeypatch.setattr(
        provider,
        "AsyncClient",
        lambda **options: client(transport=httpx.MockTransport(respond), **options),
    )
    w = Workspace(Settings(tmp_path))
    w.save_model(
        ModelInput(
            name="offline-model", base_url="https://example.invalid/v1", api_key="offline-test-key"
        )
    )
    try:
        result = await asyncio.wait_for(w.intelligence.answer("我需要做什么？", [], mail_tools), 20)
        assert mail_tools.calls == (["search_mails"] if empty else ["search_mails", "read_mails"])
        assert result == {
            "answer": "有一项课程作业需要处理。",
            "mail_ids": [] if empty else ["mail-1"],
            "rows": []
            if empty
            else [{"mail_id": "mail-1", "handling": "action", "action": "请提交课程作业。"}],
        }
        assert 3 <= len(requests) <= 6
        assert any(request.get("tools") for request in requests)
        assert not requests[-1].get("tools")
    finally:
        await w.close()
