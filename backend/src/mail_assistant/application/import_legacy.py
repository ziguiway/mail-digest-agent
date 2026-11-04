"""Import v0.1 TOML configuration, without executing .env or using Config.load."""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from ..domain.errors import AppError
from ..domain.models import Account, Setting, new_id, stamp
from ..domain.schemas import AccountInput, ModelInput, ScheduleInput
from ..infrastructure.maintenance import WorkspaceLease

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROVIDERS = {
    "qq": "imap.qq.com",
    "163": "imap.163.com",
    "126": "imap.126.com",
    "yeah": "imap.yeah.net",
}


@dataclass
class LegacyConfig:
    source: Path
    fingerprint: str
    accounts: list[tuple[str, AccountInput]]
    model: ModelInput
    schedule: ScheduleInput
    missing: list[str]
    notes: list[str]
    feishu_hint: dict

    @property
    def ledger_key(self):
        return "legacy_import:" + hashlib.sha256(str(self.source).encode()).hexdigest()[:32]

    def account_id(self, name):
        return uuid.uuid5(uuid.NAMESPACE_URL, f"mail-assistant:{self.source}:{name}").hex

    def report(self):
        return {
            "source": str(self.source),
            "ready": not self.missing,
            "accounts": [
                {"label": name, "provider": form.provider, "enabled": form.enabled}
                for name, form in self.accounts
            ],
            "model": self.model.name,
            "missing_environment": self.missing,
            "notes": self.notes,
            "schedule_enabled": False,
            "sync_enabled": False,
        }


def _bytes(path):
    if path.stat().st_size > 2 * 1024**2:
        raise AppError("配置文件超过大小限制", "invalid_legacy_config")
    return path.read_bytes()


def read_legacy(source: Path, env_file: Path | None = None):
    source = source.expanduser().resolve()
    content = _bytes(source)
    values = dict(os.environ)  # snapshot only; never alter the process environment
    dotenv = env_file.expanduser().resolve() if env_file else source.parent / ".env"
    if env_file is not None or dotenv.exists():
        try:
            lines = _bytes(dotenv).decode().splitlines()
        except UnicodeError as exc:
            raise AppError(".env 需要使用 UTF-8 编码", "invalid_legacy_config") from exc
        for number, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep or not ENV_NAME.fullmatch(key):
                raise AppError(f".env 第 {number} 行格式无效", "invalid_legacy_config")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values.setdefault(key, value)
    missing = []

    def credential(name):
        if not isinstance(name, str) or not ENV_NAME.fullmatch(name):
            raise AppError("凭据变量名称无效", "invalid_legacy_config")
        value = values.get(name, "")
        if not value and name not in missing:
            missing.append(name)
        return value or None

    try:
        raw = tomllib.loads(content.decode())
        allowed = {
            "accounts",
            "model",
            "feishu",
            "timezone",
            "digest_time",
            "database",
            "initial_cache_days",
            "first_digest_hours",
            "max_sync_messages",
            "max_digest_messages",
        }
        if set(raw) - allowed:
            raise AppError("旧配置包含不支持的顶层字段，请核对文件版本", "invalid_legacy_config")
        accounts, names = [], set()
        for item in raw.get("accounts", []):
            name = item["name"]
            if not isinstance(name, str) or not name or name in names:
                raise AppError("邮箱名称必须非空且唯一", "invalid_legacy_config")
            names.add(name)
            provider = item.get("provider", "custom")
            accounts.append(
                (
                    name,
                    AccountInput(
                        label=name,
                        email=item["email"],
                        provider=provider if provider in {"qq", "163"} else "custom",
                        host=item.get("host") or PROVIDERS.get(provider, ""),
                        port=item.get("port", 993),
                        folder=item.get("folder", "INBOX"),
                        enabled=item.get("enabled", True),
                        password=credential(item["password_env"]),
                    ),
                )
            )
            if not accounts[-1][1].host:
                raise AppError("自定义邮箱缺少服务器地址", "invalid_legacy_config")
        if not accounts:
            raise AppError("旧配置至少需要一个邮箱账号", "invalid_legacy_config")
        model = raw.get("model", {})
        parsed_model = ModelInput(
            name=model.get("name", ""),
            base_url=model.get("base_url", "https://api.openai.com/v1"),
            api_key=credential(model.get("api_key_env", "MODEL_API_KEY")),
        )
        schedule = ScheduleInput(
            enabled=False,
            sync_enabled=False,
            digest_time=raw.get("digest_time", "09:00"),
            timezone=raw.get("timezone", "Asia/Shanghai"),
            initial_days=raw.get("initial_cache_days", 30),
            first_digest_hours=raw.get("first_digest_hours", 24),
        )
        hint = raw.get("feishu", {})
        hint = {
            key: hint[key]
            for key in ("receive_id", "receive_id_type", "owner_open_id")
            if hint.get(key)
        }
    except (ValidationError, KeyError, TypeError, ValueError, AttributeError) as exc:
        # Pydantic and TOML errors may contain input values: never echo them.
        raise AppError(
            "旧配置字段无效，请核对邮箱、模型、时间与时区", "invalid_legacy_config"
        ) from exc
    notes = [
        "只导入配置，原 TOML／.env 和原型邮件数据库保持不变",
        "模型和邮箱导入后需验证；每日摘要和后台同步均保持关闭",
    ]
    if hint:
        notes.append("旧飞书目标仅保存为提示；需在网页重新完成绑定、配对和测试")
    if any(k in raw for k in ("max_sync_messages", "max_digest_messages")) or any(
        k in model for k in ("timeout", "max_tool_rounds")
    ):
        notes.append("原型同步／摘要条数上限和模型执行上限不直接映射；工程版使用当前执行策略")
    return LegacyConfig(
        source,
        hashlib.sha256(content).hexdigest(),
        accounts,
        parsed_model,
        schedule,
        missing,
        notes,
        hint,
    )


async def import_legacy(settings, config: LegacyConfig):
    if config.missing:
        raise AppError("缺少凭据环境变量：" + "、".join(config.missing), "missing_environment")
    from .workspace import Workspace

    with WorkspaceLease(settings.data_dir, exclusive=True) as lease:
        w = Workspace(settings, maintenance_lease=lease)
        try:
            ledger = w.db.get(config.ledger_key)
            if ledger and ledger["fingerprint"] != config.fingerprint:
                raise AppError(
                    "此来源已导入且文件内容已变化，请在网页修改配置", "import_source_changed"
                )
            if ledger and ledger.get("status") == "complete":
                return config.report() | {
                    "status": "already_imported",
                    "imported_accounts": len(ledger["accounts"]),
                }
            if not ledger:
                if w.accounts.list() or w.db.get("model") or w.db.get("schedule"):
                    raise AppError(
                        "目标已有配置，请导入到新的工作空间以避免覆盖", "import_target_configured"
                    )
                ledger = {
                    "fingerprint": config.fingerprint,
                    "status": "in_progress",
                    "accounts": {},
                    "started_at": stamp(),
                }
                w.db.set(config.ledger_key, ledger)
            if settings.mode == "live":
                await w.manager.ensure()
                existing = {
                    a["name"] for a in (await w.manager.command("account", "list"))["accounts"]
                }
            else:
                existing = set()
            for name, form in config.accounts:
                if name in ledger["accounts"]:
                    continue
                identity = config.account_id(name)
                data = form.model_dump(exclude={"password", "expected_revision"})
                data.update(id=identity, upstream_name="acct_" + identity)
                upstream_revision = 1
                if settings.mode == "live":
                    if data["upstream_name"] in existing:
                        shown = await w.manager.show(data["upstream_name"])
                        if shown["email_address"] != form.email:
                            raise AppError(
                                "已有 MCP 账号与导入身份不一致", "import_identity_conflict"
                            )
                        await w.manager.update(
                            data | {"upstream_revision": shown["revision"]}, form.password
                        )
                    else:
                        await w.manager.add(data, form.password)
                    shown = await w.manager.set_enabled(data["upstream_name"], form.enabled)
                    upstream_revision = shown["revision"]
                ledger = ledger | {"accounts": ledger["accounts"] | {name: identity}}
                # One transaction: a committed local account always has its checkpoint.
                with w.db.session() as session:
                    session.add(
                        Account(**data, secret_set=True, upstream_revision=upstream_revision)
                    )
                    session.get(Setting, config.ledger_key).value = ledger
            schedule = config.schedule.model_dump() | {
                "account_ids": [
                    config.account_id(name) for name, form in config.accounts if form.enabled
                ]
            }
            profile = config.model.model_dump(exclude={"api_key"}) | {
                "encrypted_key": w.secrets.seal(config.model.api_key),
                "revision": 1,
                "status": "unverified",
                "capabilities": {},
                "error": None,
            }
            with w.db.session() as session:
                for key, value in {
                    "model": profile,
                    "schedule": schedule,
                    "mailbox_config_generation": {"id": new_id()},
                    "legacy_feishu_hint": config.feishu_hint,
                }.items():
                    # Partial retries must never silently replace user edits.
                    if session.get(Setting, key) and key in {"model", "schedule"}:
                        raise AppError(
                            "导入期间配置已修改，请人工核对后继续", "import_target_changed"
                        )
                    row = session.get(Setting, key)
                    if row:
                        row.value = value
                    else:
                        session.add(Setting(key=key, value=value))
                session.get(Setting, config.ledger_key).value = ledger | {
                    "status": "complete",
                    "finished_at": stamp(),
                }
            return config.report() | {
                "status": "imported",
                "imported_accounts": len(config.accounts),
            }
        finally:
            await w.close()
