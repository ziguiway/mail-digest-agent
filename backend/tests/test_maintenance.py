import io
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
from mail_assistant.domain.errors import AppError
from mail_assistant.domain.models import Delivery, Mail
from mail_assistant.domain.schemas import ModelInput
from mail_assistant.infrastructure.maintenance import WorkspaceLease, backup, restore


@pytest.fixture
async def saved(tmp_path):
    settings = Settings(tmp_path / "original", mode="demo")
    w = Workspace(settings)
    await w.seed_demo()
    w.save_model(ModelInput(name="offline-test", api_key="local-fixture-key"))
    w.db.set("worker", {"heartbeat_at": "2099-01-01T00:00:00+00:00", "status": "running"})
    w.db.set("feishu:secret", {"encrypted": w.secrets.seal("fixture-app-secret")})
    with w.db.session() as session:
        session.add(
            Delivery(
                id="outbox-fixture",
                binding_id="binding-fixture",
                kind="digest",
                source_id="digest-fixture",
                recipient_chat_id="chat-fixture",
                text="fixture-message",
                status="uncertain",
                chunks=[
                    {"status": "sent", "message_id": "receipt-fixture"},
                    {"status": "uncertain"},
                ],
            )
        )
    await w.close()
    return settings


async def test_roundtrip_new_directory_preserves_mail_keys_and_uncertain_receipts(saved, tmp_path):
    archive = tmp_path / "backup.tar.gz"
    report = backup(saved, archive)
    assert report["files"] >= 3 and archive.stat().st_mode & 0o777 == 0o600
    target = replace(saved, data_dir=tmp_path / "restored")
    restored = restore(target, archive)
    assert not restored["services_started"]
    assert (target.data_dir / "master.key").read_bytes() == (
        saved.data_dir / "master.key"
    ).read_bytes()
    w = Workspace(target)
    try:
        assert w.secrets.open(w.db.get("model")["encrypted_key"]) == "local-fixture-key"
        assert w.secrets.open(w.db.get("feishu:secret")["encrypted"]) == "fixture-app-secret"
        assert w.db.get("worker") is None
        with w.db.session() as session:
            assert len(session.scalars(select(Mail)).all()) == 6
            delivery = session.get(Delivery, "outbox-fixture")
            assert delivery.status == "uncertain"
            assert delivery.chunks[0]["message_id"] == "receipt-fixture"
        with pytest.raises(AppError, match="停止 API 和 Worker"):
            backup(target, tmp_path / "busy.tar.gz")
    finally:
        await w.close()
    with pytest.raises(AppError, match="空数据目录"):
        restore(target, archive)


def test_lease_protects_services_and_maintenance_across_processes(tmp_path):
    script = (
        "import sys; from pathlib import Path; "
        "from mail_assistant.infrastructure.maintenance import WorkspaceLease; "
        "lease=WorkspaceLease(Path(sys.argv[1])); print('ready',flush=True); sys.stdin.read()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        with WorkspaceLease(tmp_path):
            with pytest.raises(AppError, match="停止 API 和 Worker"):
                WorkspaceLease(tmp_path, exclusive=True)
    finally:
        process.communicate(timeout=10)
    assert process.returncode == 0
    with WorkspaceLease(tmp_path, exclusive=True):
        with pytest.raises(AppError):
            Workspace(tmp_settings := Settings(tmp_path, mode="demo"))
    assert not tmp_settings.database.exists()  # refusal occurs before migration or key creation
    assert not (tmp_path / "master.key").exists()


def rewrite_archive(source, destination, transform):
    with tarfile.open(source, "r:gz") as original, tarfile.open(destination, "w:gz") as edited:
        for member in original.getmembers():
            data = original.extractfile(member).read()
            member, data = transform(member, data)
            member.size = len(data)
            edited.addfile(member, io.BytesIO(data))


def test_corruption_traversal_and_key_mismatch_never_populate_target(saved, tmp_path):
    archive = tmp_path / "original.tar.gz"
    backup(saved, archive)
    corrupt = tmp_path / "corrupt.tar.gz"

    def corrupt_data(member, data):
        return member, b"!" + data[1:] if member.name == "payload/master.key" else data

    rewrite_archive(archive, corrupt, corrupt_data)
    target = replace(saved, data_dir=tmp_path / "rejected")
    with pytest.raises(AppError):
        restore(target, corrupt)
    assert [p.name for p in target.data_dir.iterdir()] == [".workspace.lock"]

    traversal = tmp_path / "traversal.tar.gz"

    def escape(member, data):
        if member.name == "payload/bootstrap.token":
            member.name = "../outside"
        return member, data

    rewrite_archive(archive, traversal, escape)
    with pytest.raises(AppError):
        restore(target, traversal)
    assert not (tmp_path / "outside").exists()
    mismatched = tmp_path / "mismatched-key.tar.gz"
    wrong_key = Fernet.generate_key()

    def replace_key(member, data):
        if member.name == "manifest.json":
            manifest = json.loads(data)
            manifest["files"]["master.key"]["sha256"] = hashlib.sha256(wrong_key).hexdigest()
            data = json.dumps(manifest).encode()
        elif member.name == "payload/master.key":
            data = wrong_key
        return member, data

    rewrite_archive(archive, mismatched, replace_key)
    with pytest.raises(AppError, match="密钥不匹配"):
        restore(target, mismatched)
    assert [p.name for p in target.data_dir.iterdir()] == [".workspace.lock"]
    original_key = (saved.data_dir / "master.key").read_bytes()
    (saved.data_dir / "master.key").write_bytes(Fernet.generate_key())
    with pytest.raises(AppError, match="密钥不匹配"):
        backup(saved, tmp_path / "wrong-key.tar.gz")
    assert not (tmp_path / "wrong-key.tar.gz").exists()
    (saved.data_dir / "master.key").write_bytes(original_key)
    with sqlite3.connect(saved.database) as connection:
        connection.execute("UPDATE alembic_version SET version_num='unsupported_future_version'")
    with pytest.raises(AppError, match="当前版本不支持"):
        backup(saved, tmp_path / "future-version.tar.gz")


async def test_mcp_restore_rebases_public_bootstrap_without_old_directory(saved, tmp_path):
    from mcp_email_server.bootstrap import read_bootstrap, write_bootstrap
    from mcp_email_server.managed import ManagedCatalog
    from mail_assistant.infrastructure.mail_provider import McpManager

    saved.mcp_config.parent.mkdir(mode=0o700)
    catalog = saved.mcp_config.parent / "catalog.sqlite3"
    ManagedCatalog.initialize(catalog)  # no account, no keychain access or network
    write_bootstrap(mode="managed", db_path=catalog, path=saved.mcp_config)
    archive = tmp_path / "mcp.tar.gz"
    report = backup(saved, archive)
    assert report["portability"] or sys.platform.startswith("linux") or sys.platform == "win32"
    # Move the source aside: restore must not need its absolute catalog path.
    saved.data_dir.rename(tmp_path / "old-unavailable")
    target = replace(saved, data_dir=tmp_path / "mcp-restored")
    restore(target, archive)
    selection = read_bootstrap(target.mcp_config)
    assert selection.db_path == target.data_dir / "mcp/catalog.sqlite3"
    assert ManagedCatalog(selection.db_path).catalog_revision() == 1
    status = await McpManager(target).command("config", "status")
    assert status["mode"] == "managed"


def test_backup_rejects_recursive_destination_symlinks_and_different_mode(saved, tmp_path):
    with pytest.raises(AppError, match="目录之外"):
        backup(saved, saved.data_dir / "recursive.tar.gz")
    outside = tmp_path / "outside-data"
    outside.write_text("not workspace data")
    (saved.data_dir / "linked").symlink_to(outside)
    with pytest.raises(AppError, match="符号链接"):
        backup(saved, tmp_path / "linked.tar.gz")
    (saved.data_dir / "linked").unlink()
    archive = tmp_path / "demo.tar.gz"
    backup(saved, archive)
    with pytest.raises(AppError, match="运行模式"):
        restore(Settings(tmp_path / "live"), archive)
    with pytest.raises(AppError, match="已存在"):
        backup(saved, archive)


def test_cli_backup_restore_emits_json_without_secrets(saved, tmp_path):
    archive, restored = tmp_path / "cli.tar.gz", tmp_path / "cli-restored"
    env = os.environ | {"MAIL_ASSISTANT_MODE": "demo"}
    for directory, command, option, path in (
        (saved.data_dir, "backup", "--output", archive),
        (restored, "restore", "--input", archive),
    ):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mail_assistant",
                "--data-dir",
                str(directory),
                command,
                option,
                str(path),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert result.returncode == 0, result.stderr
        assert "local-fixture-key" not in result.stdout + result.stderr
        assert isinstance(json.loads(result.stdout), dict)
    with sqlite3.connect(restored / "workspace.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM mails").fetchone()[0] == 6


async def test_backup_includes_committed_wal_records_as_standalone_database(saved, tmp_path):
    connection = sqlite3.connect(saved.database)
    try:
        connection.execute(
            "INSERT INTO settings(key, value) VALUES ('wal-fixture', '{\"present\": true}')"
        )
        connection.commit()
        assert saved.database.with_name("workspace.sqlite3-wal").stat().st_size > 32
        archive = tmp_path / "wal.tar.gz"
        backup(saved, archive)
        with tarfile.open(archive) as compressed:
            assert not any(m.name.endswith(("-wal", "-shm")) for m in compressed.getmembers())
        target = replace(saved, data_dir=tmp_path / "wal-restored")
        restore(target, archive)
        w = Workspace(target)
        try:
            assert w.db.get("wal-fixture") == {"present": True}
        finally:
            await w.close()
    finally:
        connection.close()


def test_cli_archive_stream_roundtrip_has_no_log_noise(saved, tmp_path):
    env = os.environ | {"MAIL_ASSISTANT_MODE": "demo"}
    produced = subprocess.run(
        [
            sys.executable,
            "-m",
            "mail_assistant",
            "--data-dir",
            str(saved.data_dir),
            "backup",
            "--output",
            "-",
        ],
        env=env,
        capture_output=True,
        timeout=40,
    )
    assert produced.returncode == 0, produced.stderr
    assert produced.stdout[:2] == b"\x1f\x8b"
    assert json.loads(produced.stderr)["path"] == "stdout"
    target = tmp_path / "stream-restored"
    consumed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mail_assistant",
            "--data-dir",
            str(target),
            "restore",
            "--input",
            "-",
        ],
        input=produced.stdout,
        env=env,
        capture_output=True,
        timeout=40,
    )
    assert consumed.returncode == 0, consumed.stderr
    assert consumed.stdout == b""
    assert json.loads(consumed.stderr)["data_dir"] == str(target)


def test_restore_supports_writable_volume_with_readonly_parent(saved, tmp_path):
    archive = tmp_path / "volume.tar.gz"
    backup(saved, archive)
    parent = tmp_path / "readonly-parent"
    target = parent / "volume"
    target.mkdir(parents=True, mode=0o700)
    parent.chmod(0o500)
    try:
        result = restore(replace(saved, data_dir=target), archive)
        assert not result["services_started"]
        assert (target / "workspace.sqlite3").exists()
    finally:
        parent.chmod(0o700)
