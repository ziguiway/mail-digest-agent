import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import select

from mail_assistant.application.import_legacy import read_legacy, import_legacy
from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.models import Account, Task
from mail_assistant.infrastructure.mail_provider import McpManager


@pytest.fixture
def source(tmp_path, monkeypatch):
    for name in ("FIXTURE_QQ", "FIXTURE_163", "FIXTURE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "legacy.toml"
    path.write_text("""
timezone = "Asia/Shanghai"
digest_time = "10:30"
initial_cache_days = 14
first_digest_hours = 12
max_sync_messages = 200
[model]
name = "fixture-model"
api_key_env = "FIXTURE_MODEL"
[feishu]
receive_id_type = "user_id"
receive_id = "fixture-recipient"
[[accounts]]
name = "学校 QQ"
provider = "qq"
email = "fixture@qq.com"
password_env = "FIXTURE_QQ"
[[accounts]]
name = "项目网易"
provider = "163"
email = "fixture@163.com"
password_env = "FIXTURE_163"
enabled = false
""")
    (tmp_path / ".env").write_text(
        'FIXTURE_MODEL="fixture-model-key"\nFIXTURE_QQ=fixture-qq-code\nFIXTURE_163=fixture-163-code\n'
    )
    return path


@pytest.fixture
def manager(monkeypatch):
    class Catalog:
        def __init__(self):
            self.entries, self.added, self.updated = {}, [], []
            self.interrupt_after_add = False

        async def ensure(self, *args):
            pass

        async def command(self, *args, **kwargs):
            assert args == ("account", "list")
            return {"accounts": [{"name": name} for name in self.entries]}

        async def add(self, account, password):
            name = account["upstream_name"]
            assert name not in self.entries
            assert password in {"fixture-qq-code", "fixture-163-code"}
            self.entries[name] = {"email_address": account["email"], "revision": 1, "enabled": True}
            self.added.append(name)
            if self.interrupt_after_add and len(self.added) == 2:
                raise AppError("模拟 CLI 写入后连接中断", "provider_error")

        async def show(self, name):
            return self.entries[name]

        async def update(self, account, password):
            assert account["upstream_revision"] == 1
            self.updated.append(account["upstream_name"])

        async def enabled(self, name, enabled):
            self.entries[name]["enabled"] = enabled
            return self.entries[name]

    catalog = Catalog()
    for name, method in {
        "ensure": catalog.ensure,
        "command": catalog.command,
        "add": catalog.add,
        "show": catalog.show,
        "update": catalog.update,
        "set_enabled": catalog.enabled,
    }.items():
        monkeypatch.setattr(McpManager, name, method)
    return catalog


def test_preflight_does_not_change_environment_or_expose_values(source, monkeypatch):
    original = source.read_bytes()
    before = dict(os.environ)
    config = read_legacy(source)
    assert config.missing == [] and dict(os.environ) == before
    report = json.dumps(config.report(), ensure_ascii=False)
    assert "fixture-model-key" not in report and "fixture-qq-code" not in report
    assert source.read_bytes() == original
    assert config.accounts[0][1].host == "imap.qq.com"
    assert config.accounts[1][1].host == "imap.163.com"
    assert not config.schedule.enabled and not config.schedule.sync_enabled
    monkeypatch.setenv("FIXTURE_QQ", "explicit-process-value")
    assert read_legacy(source).accounts[0][1].password == "explicit-process-value"
    (source.parent / ".env").unlink()
    assert set(read_legacy(source).missing) == {"FIXTURE_163", "FIXTURE_MODEL"}


async def test_import_is_idempotent_keeps_sources_and_does_not_enable_schedules(source, manager):
    settings = Settings(source.parent / "imported")
    original, env = source.read_bytes(), (source.parent / ".env").read_bytes()
    config = read_legacy(source)
    report = await import_legacy(settings, config)
    assert report["status"] == "imported" and report["imported_accounts"] == 2
    assert (await import_legacy(settings, config))["status"] == "already_imported"
    assert len(manager.added) == 2 and manager.updated == []
    w = Workspace(settings)
    try:
        assert len(w.accounts.list()) == 2
        assert {a["status"] for a in w.accounts.list()} == {"unverified"}
        schedule = w.db.get("schedule")
        assert not schedule["enabled"] and not schedule["sync_enabled"]
        assert schedule["digest_time"] == "10:30" and schedule["initial_days"] == 14
        assert schedule["account_ids"] == [config.account_id("学校 QQ")]
        assert w.secrets.open(w.db.get("model")["encrypted_key"]) == "fixture-model-key"
        assert w.db.get("feishu") is None
        assert w.db.get("legacy_feishu_hint")["receive_id"] == "fixture-recipient"
        with w.db.session() as session:
            assert session.scalar(select(Task.id)) is None
        with pytest.raises(AppError, match="停止 API 和 Worker"):
            await import_legacy(settings, config)
    finally:
        await w.close()
    assert source.read_bytes() == original and (source.parent / ".env").read_bytes() == env
    source.write_text(source.read_text().replace('"fixture-model"', '"modified-model"'))
    with pytest.raises(AppError, match="内容已变化"):
        await import_legacy(settings, read_legacy(source))
    assert len(manager.added) == 2


async def test_import_resumes_after_remote_write_before_local_commit(source, manager):
    config, settings = read_legacy(source), Settings(source.parent / "partial")
    manager.interrupt_after_add = True
    with pytest.raises(AppError, match="连接中断"):
        await import_legacy(settings, config)
    w = Workspace(settings)
    try:
        assert len(w.accounts.list()) == 1
        assert w.db.get("model") is None and w.db.get("schedule") is None
    finally:
        await w.close()
    manager.interrupt_after_add = False
    assert (await import_legacy(settings, config))["status"] == "imported"
    assert len(manager.added) == 2 and len(manager.updated) == 1
    w = Workspace(settings)
    try:
        with w.db.session() as session:
            rows = session.scalars(select(Account)).all()
            assert len(rows) == 2 and len({a.upstream_name for a in rows}) == 2
        assert w.db.get(config.ledger_key)["status"] == "complete"
    finally:
        await w.close()


async def test_import_refuses_existing_config_and_missing_credentials(source, manager):
    settings = Settings(source.parent / "existing")
    w = Workspace(settings)
    w.db.set("schedule", {"enabled": False})
    await w.close()
    with pytest.raises(AppError, match="目标已有配置"):
        await import_legacy(settings, read_legacy(source))
    assert manager.entries == {}
    (source.parent / ".env").unlink()
    empty_settings = Settings(source.parent / "missing")
    with pytest.raises(AppError, match="FIXTURE_QQ"):
        await import_legacy(empty_settings, read_legacy(source))
    assert not empty_settings.data_dir.exists()


def test_cli_dry_run_is_read_only_and_never_executes_env(source):
    marker = source.parent / "executed"
    env_path = source.parent / ".env"
    env_path.write_text(env_path.read_text().replace("fixture-qq-code", f"$(touch {marker})"))
    target = source.parent / "dry-run"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mail_assistant",
            "--data-dir",
            str(target),
            "import-legacy",
            "--config",
            str(source),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ready"]
    assert not marker.exists() and not target.exists()
    assert "fixture-model-key" not in result.stdout + result.stderr
