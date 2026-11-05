from __future__ import annotations

import asyncio
import inspect
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import get_type_hints
from agently import Agent, Agently
from agently.utils import Settings as AgentlySettings
import httpx
from pydantic import TypeAdapter, create_model
from . import prompts
from ..domain.errors import AppError
from ..domain.schemas import DigestItem
from ..domain.query_answers import validate_query_answer


def model_failure(exc, message, code):
    """Classify provider failures without exposing response bodies or credentials."""
    if isinstance(exc, AppError):
        return exc
    current = exc
    for _ in range(8):
        if isinstance(current, (TimeoutError, httpx.TimeoutException)):
            return AppError("模型服务响应超时，请稍后重试", "model_timeout", retryable=True)
        if isinstance(current, httpx.RequestError):
            return AppError(
                "无法连接模型服务，请检查服务端网络、DNS 和 TLS 证书",
                "model_connection_failed",
                retryable=True,
            )
        response = getattr(current, "response", None)
        status = getattr(response, "status_code", None)
        # Agently 4.1.4.10 wraps HTTP failures with this fixed prefix.
        matched = re.search(r"^Status Code: (\d{3})\b", str(current), re.MULTILINE)
        status = status or (int(matched[1]) if matched else None)
        if status in {401, 403}:
            return AppError("模型服务拒绝访问，请检查 API Key 和模型访问权限", "model_auth_failed")
        if status == 402:
            return AppError("模型服务额度不足，请检查账号余额或密钥限额", "model_quota_exceeded")
        if status == 404:
            return AppError(
                "未找到模型接口，请检查基础地址（OpenRouter 使用 /api/v1）和模型名称",
                "model_endpoint_not_found",
            )
        if status == 429:
            return AppError(
                "模型服务请求过于频繁，请稍后重试", "model_rate_limited", retryable=True
            )
        if isinstance(status, int) and status >= 500:
            return AppError("模型服务暂时不可用，请稍后重试", "model_service_error", retryable=True)
        current = current.__cause__ or current.__context__
        if current is None:
            break
    return AppError(message, code)


class AgentlyIntelligence:
    def __init__(self, db, secrets):
        self.db, self.secrets = db, secrets

    def agent(self, profile=None):
        profile = profile if profile is not None else self.db.get("model", {})
        if not profile.get("name") or not profile.get("encrypted_key"):
            raise AppError("请先配置并验证模型连接", "model_unconfigured")
        # The framework creates a task directory during Agent construction.
        # Give each workspace its own writable parent before creating it;
        # container application code is deliberately owned by root.
        settings = AgentlySettings(parent=Agently.settings)
        settings.set("task_workspace.default_parent", str(self.db.path.parent / "agently"))
        try:
            agent = Agent(parent_settings=settings)
        except OSError as exc:
            raise AppError(
                "智能体运行目录不可写，请检查服务端数据目录权限", "agent_workspace_unavailable"
            ) from exc
        agent.set_settings(
            "OpenAICompatible",
            {
                "base_url": profile["base_url"],
                "model": profile["name"],
                "api_key": self.secrets.open(profile["encrypted_key"]),
                "model_type": "chat",
                "request_options": {"temperature": 0.1},
            },
        )
        return agent

    def bind_actions(self, agent, actions, *, max_rounds=4):
        """Bridge Agently Actions to the compatible provider's native tool payload."""
        specs = []
        for action in actions:
            hints = get_type_hints(action)
            fields = {
                name: (
                    hints[name],
                    ... if parameter.default is inspect.Parameter.empty else parameter.default,
                )
                for name, parameter in inspect.signature(action).parameters.items()
            }
            parameters = create_model(action.__name__ + "Input", **fields).model_json_schema()
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": action.__name__,
                        "description": inspect.getdoc(action) or "",
                        "parameters": parameters,
                    },
                }
            )
        agent.set_action_loop(max_rounds=max_rounds, planning_protocol="native_tool_calls")
        # This pinned provider does not map prompt.tools to request_options.tools.
        # Setting only the Action protocol silently omits the wire-level tools.
        agent.set_settings("OpenAICompatible.request_options.tools", specs)
        agent.use_actions(actions, always=True)

    async def summarize(self, mails, progress=None, *, profile=None, analysis_time=None):
        output = []
        profile = profile if profile is not None else self.db.get("model", {})
        for offset in range(0, len(mails), 8):
            batch = mails[offset : offset + 8]
            if progress:
                await progress(f"正在分析邮件 {offset + 1}—{offset + len(batch)}")
            agent = self.agent(profile)
            schema = {
                "items": [
                    {
                        "mail_id": (str, "输入中的邮件 id", True),
                        "priority": (str, "high / medium / low", True),
                        "summary": (str, "事项摘要", True),
                        "action": (str, "需要采取的行动；不明确时写待确认", True),
                        "deadline": (str, "正文未明确时为空字符串", True),
                        "reason": (str, "根据正文说明优先级依据", True),
                    }
                ]
            }
            request = (
                agent.create_request()
                .input(
                    {
                        "analysis_time": analysis_time
                        or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                        "mails": [
                            {
                                k: m.get(k)
                                for k in (
                                    "id",
                                    "subject",
                                    "sender",
                                    "sent_at",
                                    "received_at",
                                    "body",
                                    "body_truncated",
                                )
                            }
                            for m in batch
                        ],
                    }
                )
                .instruct(prompts.SUMMARY)
                .output(schema)
            )
            try:
                data = await asyncio.wait_for(request.get_result().async_get_data(), timeout=120)
                for item in data["items"]:
                    item["deadline"] = item.get("deadline") or None
                items = TypeAdapter(list[DigestItem]).validate_python(data["items"])
            except Exception as exc:
                raise AppError(
                    "模型未返回完整有效的摘要，请检查服务或稍后重试",
                    "model_output_error",
                    retryable=True,
                ) from exc
            if len(items) != len(batch) or {i.mail_id for i in items} != {m["id"] for m in batch}:
                raise AppError("模型摘要的邮件来源不匹配，本次结果未保存", "invalid_sources")
            output.extend(items)
        return output

    async def answer(self, question, history, tools, progress=None):
        agent = self.agent()
        self.bind_actions(agent, [tools.search_mails, tools.read_mails, tools.get_thread])
        turn = (
            agent.input(question)
            .info(
                {
                    "history": [
                        {
                            key: message[key]
                            for key in ("role", "content", "sources")
                            if key in message
                        }
                        for message in history
                    ],
                    "now": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                    "coverage": tools.coverage(),
                }
            )
            .instruct(prompts.QUERY)
            .output(
                {
                    "answer": (str, "给用户的中文回答", True),
                    "mail_ids": [(str, "本轮实际读到的邮件 id")],
                    "rows": [
                        {
                            "mail_id": (str, "本轮已读且列入 mail_ids 的邮件 id", True),
                            "handling": (str, "action / confirm / waiting / notice", True),
                            "action": (str, "简短下一步建议，不编造期限或已完成状态", True),
                        }
                    ],
                }
            )
        )
        try:
            data = await asyncio.wait_for(turn.get_result().async_get_data(), timeout=150)
        except Exception as exc:
            raise model_failure(
                exc, "模型查询未完成，请稍后重试或检查模型兼容性", "model_query_error"
            ) from exc
        if not tools.calls:
            raise AppError(
                "模型未调用邮件检索工具，请检查模型的工具调用能力", "tool_calling_unavailable"
            )
        return validate_query_answer(data, tools.evidence)

    async def probe(self):
        profile = self.db.get("model", {})
        try:
            agent = self.agent(profile)
            result = (
                agent.create_request()
                .input(prompts.STRUCTURED_PROBE)
                .output({"ready": (bool, "固定为 true", True)})
                .get_result()
            )
            data = await asyncio.wait_for(result.async_get_data(), 60)
            structured = data.get("ready") is True
        except AppError:
            raise
        except Exception as exc:
            raise model_failure(
                exc, "模型结构化输出测试失败，请检查模型名称、服务地址和密钥", "model_probe_failed"
            ) from exc
        called = False

        async def workspace_connection_probe(value: str) -> str:
            """执行连接测试，返回收到的 value；验证工具调用通道。"""
            nonlocal called
            called = value == "connected"
            return value

        try:
            tool_agent = self.agent(profile)
            self.bind_actions(tool_agent, [workspace_connection_probe], max_rounds=2)
            await asyncio.wait_for(
                tool_agent.input(prompts.TOOL_PROBE).get_result().async_get_data(), 60
            )
        except AppError:
            raise
        except Exception as exc:
            raise model_failure(
                exc, "结构化输出可用，但工具调用测试失败，请检查模型兼容性", "tool_probe_failed"
            ) from exc
        if not structured or not called:
            raise AppError(
                "模型没有完成必要的输出或工具调用，请更换兼容模型", "model_capability_missing"
            )
        return {"structured_output": structured, "tool_calling": called, "simulated": False}
