"""Offline, cross-process workspace maintenance without opening a live Workspace."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path, PurePosixPath

from cryptography.fernet import Fernet, InvalidToken
from alembic.script import ScriptDirectory

from ..domain.errors import AppError

FORMAT = 1
MAX_BYTES = 2 * 1024**3
MAX_FILES = 100_000
MAX_MANIFEST = 16 * 1024**2
LOCK = ".workspace.lock"


class WorkspaceLease:
    """Shared for running services, exclusive for offline operations.

    The inode stays in place, including during restore. flock descriptors are
    close-on-exec, so child MCP/CLI processes cannot outlive their parent's lease.
    """

    def __init__(self, root: Path, *, exclusive=False):
        self.root, self.exclusive = root, exclusive
        self.handles = []
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self._acquire(root / LOCK, exclusive)
            # Also reject an older Worker that predates shared service leases.
            if exclusive and (root / "worker.lock").exists():
                self._acquire(root / "worker.lock", True)
        except BaseException:
            self.close()
            raise

    def _acquire(self, path, exclusive):
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        handle = os.fdopen(descriptor, "a+")
        self.handles.append(handle)
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AppError(
                "数据目录正在使用或维护中；离线维护前请停止 API 和 Worker",
                "workspace_busy",
            ) from exc

    def close(self):
        for handle in reversed(self.handles):
            handle.close()
        self.handles.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _error(message="备份归档无效或校验失败", code="invalid_backup"):
    return AppError(message, code)


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _name(value):
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise _error()
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise _error()
    return path


def _sqlite(path):
    with path.open("rb") as handle:
        return handle.read(16) == b"SQLite format 3\0"


@contextmanager
def _connect(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        yield connection


def _check_sqlite(path):
    try:
        with _connect(path) as connection:
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise _error("备份中的数据库未通过完整性检查")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise _error("备份中的数据库包含无效引用")
    except sqlite3.Error as exc:
        raise _error("备份中的数据库无法读取") from exc


def _check_workspace(root):
    database, key = root / "workspace.sqlite3", root / "master.key"
    if not database.is_file() or not key.is_file() or not (root / "bootstrap.token").is_file():
        raise _error("缺少业务数据库、应用密钥或初始化凭证")
    try:
        cipher = Fernet(key.read_bytes())

        def check_tokens(value):
            if isinstance(value, dict):
                for name, item in value.items():
                    if (name == "encrypted" or name.startswith("encrypted_")) and item:
                        cipher.decrypt(item.encode())
                    else:
                        check_tokens(item)
            elif isinstance(value, list):
                for item in value:
                    check_tokens(item)

        with _connect(database) as connection:
            revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
            scripts = ScriptDirectory(str(Path(__file__).parents[1] / "migrations"))
            if len(revisions) != 1 or revisions[0][0] not in {
                s.revision for s in scripts.walk_revisions()
            }:
                raise _error("当前版本不支持此数据库，请使用匹配的应用版本")
            for (value,) in connection.execute("SELECT value FROM settings"):
                check_tokens(json.loads(value))
    except (
        sqlite3.Error,
        InvalidToken,
        ValueError,
        TypeError,
        AttributeError,
    ) as exc:
        raise _error("业务配置与应用密钥不匹配或数据库版本无效") from exc


def _files(root):
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            if path.is_symlink():
                raise _error("数据目录包含符号链接，请先确认文件布局", "unsupported_backup_layout")
        for name in files:
            path = Path(parent) / name
            if name.endswith((".lock", "-wal", "-shm")) or name == LOCK:
                continue
            if not stat.S_ISREG(path.stat().st_mode):
                raise _error("数据目录包含非普通文件", "unsupported_backup_layout")
            yield path


def _mcp_location(settings):
    from mcp_email_server.bootstrap import read_bootstrap

    try:
        selection = read_bootstrap(settings.mcp_config)
    except (ValueError, OSError) as exc:
        raise _error("MCP 配置无法读取，请先检查连接组件状态", "invalid_mcp_config") from exc
    if not selection.exists:
        return None
    if selection.mode != "managed" or selection.db_path is None:
        raise _error("仅支持备份工程版管理的 MCP 账号目录", "unsupported_backup_layout")
    try:
        relative = selection.db_path.relative_to(settings.data_dir).as_posix()
    except ValueError as exc:
        raise _error("MCP 账号目录位于工作空间之外，无法一致备份", "external_catalog") from exc
    if not selection.db_path.is_file():
        raise _error("MCP 账号目录不存在", "missing_catalog")
    return relative


def _portability(settings, catalog):
    notes = []
    if catalog and not (sys.platform.startswith("linux") or sys.platform == "win32"):
        notes.append(
            {
                "component": "mailbox",
                "status": "external_credentials",
                "action": "本平台 MCP 使用系统钥匙串；归档不导出钥匙串，迁移机器后需重新填写邮箱授权码",
            }
        )
    if settings.feishu_dir.exists():
        notes.append(
            {
                "component": "feishu",
                "status": "reauthorization_may_be_required",
                "action": "已包含工作空间内的飞书文件；系统钥匙串或外部凭据未导出，恢复后需检查并按需重新绑定",
            }
        )
    return notes


def backup(settings, destination: Path):
    root, destination = settings.data_dir, destination.expanduser().resolve()
    if destination.is_relative_to(root):
        raise _error("备份文件必须位于数据目录之外", "invalid_destination")
    if destination.exists():
        raise _error("备份文件已存在，请选择新文件名", "destination_exists")
    with WorkspaceLease(root, exclusive=True):
        _check_workspace(root)
        catalog = _mcp_location(settings)
        notes = _portability(settings, catalog)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="mail-backup-") as temporary:
            staged = Path(temporary) / "payload"
            staged.mkdir(mode=0o700)
            entries, total = {}, 0
            for source in _files(root):
                name = source.relative_to(root).as_posix()
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if _sqlite(source):
                    with _connect(source) as src, closing(sqlite3.connect(target)) as dst:
                        src.backup(dst)
                        # A standalone snapshot must not require WAL/SHM sidecars.
                        dst.execute("PRAGMA journal_mode=DELETE")
                    _check_sqlite(target)
                else:
                    shutil.copyfile(source, target)
                target.chmod(0o600)
                size = target.stat().st_size
                total += size
                if total > MAX_BYTES or len(entries) >= MAX_FILES:
                    raise _error("数据超过当前备份大小上限", "backup_too_large")
                entries[name] = {"size": size, "sha256": _hash(target)}
            manifest = {
                "format": FORMAT,
                "app_version": version("mail-assistant"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "mode": settings.mode,
                "source_root": str(root),
                "mcp_catalog": catalog,
                "portability": notes,
                "files": entries,
            }
            manifest_path = Path(temporary) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))
            fd, temp_name = tempfile.mkstemp(prefix=".mail-backup-", dir=destination.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    with tarfile.open(fileobj=handle, mode="w:gz") as archive:
                        archive.add(manifest_path, arcname="manifest.json", recursive=False)
                        for name in sorted(entries):
                            archive.add(staged / name, arcname=f"payload/{name}", recursive=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.link(temp_name, destination)  # publish atomically; never replace another backup
            finally:
                Path(temp_name).unlink(missing_ok=True)
    return {"path": str(destination), "files": len(entries), "bytes": total, "portability": notes}


def _unpack(source, staged):
    try:
        with tarfile.open(source, mode="r:gz") as archive:
            members, total, manifest = {}, 0, None
            for member in archive:
                _name(member.name)
                if not member.isfile() or member.name in members:
                    raise _error()
                total += member.size
                if member.size < 0 or total > MAX_BYTES or len(members) > MAX_FILES:
                    raise _error()
                members[member.name] = member
                if member.name == "manifest.json":
                    if member.size > MAX_MANIFEST:
                        raise _error()
                    manifest = json.load(archive.extractfile(member))
            if not isinstance(manifest, dict) or type(manifest.get("format")) is not int:
                raise _error()
            if manifest["format"] != FORMAT or manifest.get("mode") not in {"live", "demo"}:
                raise _error("不支持此备份格式或运行模式")
            entries = manifest.get("files")
            if not isinstance(entries, dict) or len(entries) > MAX_FILES:
                raise _error()
            expected = {"manifest.json"}
            for name, metadata in entries.items():
                _name(name)
                member_name = f"payload/{name}"
                expected.add(member_name)
                member = members.get(member_name)
                if (
                    not isinstance(metadata, dict)
                    or not member
                    or metadata.get("size") != member.size
                ):
                    raise _error()
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with target.open("xb") as handle:
                    shutil.copyfileobj(archive.extractfile(member), handle, 1024 * 1024)
                target.chmod(0o600)
                if _hash(target) != metadata.get("sha256"):
                    raise _error()
            if set(members) != expected:
                raise _error()
            return manifest
    except (tarfile.TarError, OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise _error() from exc


def restore(settings, source: Path):
    root, source = settings.data_dir, source.expanduser().resolve()
    with WorkspaceLease(root, exclusive=True):
        if any(p.name != LOCK for p in root.iterdir()):
            raise _error("恢复目标必须是空数据目录，请使用新的目录或空卷", "target_not_empty")
        # A Docker named volume is writable while its parent (/var/lib) need
        # not be. Stage on that same volume and preserve its lock inode.
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=root) as temporary:
            staged = Path(temporary)
            manifest = _unpack(source, staged)
            if manifest["mode"] != settings.mode:
                raise _error("备份运行模式与目标不同，请显式设置匹配的 MAIL_ASSISTANT_MODE")
            for path in staged.rglob("*"):
                if path.is_file() and _sqlite(path):
                    _check_sqlite(path)
            _check_workspace(staged)
            catalog = manifest.get("mcp_catalog")
            if catalog:
                _name(catalog)
                if not (staged / catalog).is_file() or not _sqlite(staged / catalog):
                    raise _error("备份缺少 MCP 账号目录")
                from mcp_email_server.bootstrap import bootstrap_path, write_bootstrap

                config = staged / "mcp" / "config.toml"
                bootstrap_path(config).unlink(missing_ok=True)
                # Public configuration mechanism, never patch upstream SQL tables.
                write_bootstrap(mode="managed", db_path=root / catalog, path=config)
            with closing(sqlite3.connect(staged / "workspace.sqlite3")) as connection, connection:
                # A restored heartbeat must not report a stopped Worker as alive.
                connection.execute("DELETE FROM settings WHERE key = 'worker'")
            moved = []
            try:
                for path in staged.iterdir():
                    target = root / path.name
                    if path.name == LOCK or target.exists():
                        raise _error()
                    path.rename(target)
                    moved.append(target)
                root.chmod(0o700)
            except BaseException:
                for target in reversed(moved):
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                raise
    return {
        "data_dir": str(root),
        "files": len(manifest["files"]),
        "app_version": manifest.get("app_version"),
        "mode": manifest["mode"],
        "portability": manifest.get("portability", []),
        "services_started": False,
    }
