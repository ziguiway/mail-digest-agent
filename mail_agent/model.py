from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .config import ModelConfig


class ModelError(RuntimeError):
    pass


class ChatModel:
    def __init__(self, config: ModelConfig):
        self.config = config

    def complete(self, messages: list[dict], tools: list[dict] | None = None, *, json_mode=False) -> dict:
        key = os.getenv(self.config.api_key_env)
        if not key or not self.config.name:
            raise ModelError("请先配置模型名称及 API Key")
        payload = {"model": self.config.name, "messages": messages, "temperature": 0.1}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                data = json.load(response)
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ModelError("模型输出被截断，请缩小查询或增大服务端输出限制")
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError("Invalid message")
            return {k: message[k] for k in ("role", "content", "tool_calls") if k in message} | {"role": "assistant"}
        except urllib.error.HTTPError as error:
            raise ModelError(f"模型 API 返回 HTTP {error.code}；请检查 base_url、模型名、密钥及额度") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ModelError("模型 API 连接失败或超时") from error
        except (KeyError, IndexError, ValueError, TypeError) as error:
            raise ModelError("模型 API 返回格式不符合 Chat Completions 约定") from error


def json_content(message: dict) -> dict:
    content = message.get("content") or ""
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError) as error:
        raise ModelError("模型没有返回有效 JSON；本次不会更新摘要发送进度") from error
    if not isinstance(data, dict):
        raise ModelError("模型应返回 JSON 对象")
    return data
