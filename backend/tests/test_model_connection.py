import httpx
import pytest
from agently import Agently

from mail_assistant.agents.intelligence import AgentlyIntelligence, model_failure
from mail_assistant.config import Settings
from mail_assistant.application.workspace import Workspace
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.schemas import ModelInput
from mail_assistant.infrastructure.database import Database
from mail_assistant.infrastructure.security import Secrets


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (" https://openrouter.ai/api/ ", "https://openrouter.ai/api/v1"),
        ("https://openrouter.ai", "https://openrouter.ai/api/v1"),
        ("https://openrouter.ai/api/v1/", "https://openrouter.ai/api/v1"),
        ("https://example.com/custom/", "https://example.com/custom"),
    ],
)
def test_openrouter_base_url_preserves_model_alias_and_other_providers(url, expected):
    form = ModelInput(base_url=url, name="~z-ai/glm-flash-latest")
    assert form.base_url == expected and form.name == "~z-ai/glm-flash-latest"


def test_real_agently_agents_use_separate_writable_workspace_roots(tmp_path, monkeypatch):
    unrelated = tmp_path / "application-code"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    original = Agently.settings.get("task_workspace.default_parent", None)
    roots = []
    for label in ("first", "second"):
        data = tmp_path / label
        db = Database(data / "workspace.sqlite3")
        secrets = Secrets(data)
        try:
            agent = AgentlyIntelligence(db, secrets).agent(
                {
                    "name": "offline-model",
                    "base_url": "https://example.invalid/v1",
                    "encrypted_key": secrets.seal("offline-test-key"),
                }
            )
            root = agent.task_workspace.root
            assert root.is_dir() and root.is_relative_to(data)
            roots.append(root)
        finally:
            db.close()
    assert roots[0] != roots[1] and not (unrelated / ".agently").exists()
    assert Agently.settings.get("task_workspace.default_parent", None) == original


async def test_agent_initialization_failure_is_saved_as_actionable_model_error(
    tmp_path, monkeypatch
):
    w = Workspace(Settings(tmp_path))
    w.save_model(ModelInput(name="offline-model", api_key="offline-test-key"))

    def denied(*args, **kwargs):
        raise PermissionError("secret-canary must not reach the UI")

    monkeypatch.setattr("mail_assistant.agents.intelligence.Agent", denied)
    try:
        task = w.tasks.enqueue("model_test", {}, lane="interactive")
        with pytest.raises(AppError) as caught:
            await w.execute(task)
        w.tasks.fail(task["id"], caught.value)
        assert caught.value.code == "agent_workspace_unavailable"
        profile = w.model_view()
        assert profile["status"] == "error" and "目录权限" in profile["error"]
        assert "secret-canary" not in profile["error"]
    finally:
        await w.close()


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "model_auth_failed"),
        (402, "model_quota_exceeded"),
        (404, "model_endpoint_not_found"),
        (429, "model_rate_limited"),
        (503, "model_service_error"),
    ],
)
def test_native_and_framework_wrapped_http_errors_are_safe(status, code):
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    native = httpx.HTTPStatusError(
        "secret-canary", request=request, response=httpx.Response(status, request=request)
    )
    wrapped = RuntimeError(f"Status Code: {status}\nsecret-canary\nRequest Data: private-prompt")
    for exc in (native, wrapped):
        failure = model_failure(exc, "fallback", "model_probe_failed")
        assert failure.code == code
        assert "secret-canary" not in failure.message and "private-prompt" not in failure.message
