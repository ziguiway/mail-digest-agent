from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

PROVIDERS = {"qq": "imap.qq.com", "163": "imap.163.com", "126": "imap.126.com", "yeah": "imap.yeah.net"}


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid environment entry in {path.name}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass
class Account:
    name: str
    provider: str
    email: str
    password_env: str
    enabled: bool = True
    folder: str = "INBOX"
    host: str = ""
    port: int = 993

    @property
    def server(self) -> str:
        return self.host or PROVIDERS.get(self.provider, "")


@dataclass
class ModelConfig:
    base_url: str = "https://api.openai.com/v1"
    name: str = ""
    api_key_env: str = "MODEL_API_KEY"
    timeout: int = 90
    max_tool_rounds: int = 10


@dataclass
class FeishuConfig:
    cli: str = "lark-cli"
    receive_id_type: str = "user_id"
    receive_id: str = ""
    owner_open_id: str = ""


@dataclass
class Config:
    root: Path
    database: Path
    accounts: list[Account] = field(default_factory=list)
    model: ModelConfig = field(default_factory=ModelConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    timezone: str = "Asia/Shanghai"
    digest_time: str = "09:00"
    initial_cache_days: int = 30
    first_digest_hours: int = 24
    max_sync_messages: int = 200
    max_digest_messages: int = 100

    @property
    def active_accounts(self) -> list[str]:
        return [a.name for a in self.accounts if a.enabled]

    @classmethod
    def load(cls, path: str | Path) -> Config:
        path = Path(path).expanduser().resolve()
        load_env(path.parent / ".env")
        with path.open("rb") as f:
            data = tomllib.load(f)
        config = cls(
            root=path.parent,
            database=path.parent / data.pop("database", "data/mail-agent.sqlite3"),
            accounts=[Account(**a) for a in data.pop("accounts", [])],
            model=ModelConfig(**data.pop("model", {})),
            feishu=FeishuConfig(**data.pop("feishu", {})),
            **data,
        )
        ZoneInfo(config.timezone)
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", config.digest_time):
            raise ValueError("digest_time must be HH:MM")
        names = [a.name for a in config.accounts]
        if len(names) != len(set(names)):
            raise ValueError("Each account needs a unique name")
        if not 1 <= config.model.max_tool_rounds <= 30:
            raise ValueError("model.max_tool_rounds must be between 1 and 30")
        for field_name in ("initial_cache_days", "first_digest_hours", "max_sync_messages", "max_digest_messages"):
            if getattr(config, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        return config

    def check(self, *, model: bool = True, feishu: bool = False) -> list[str]:
        errors = []
        if not self.active_accounts:
            errors.append("至少启用一个邮箱账号")
        for account in self.accounts:
            if account.enabled:
                if not account.email or not account.server:
                    errors.append(f"邮箱 {account.name} 缺少地址或服务器")
                if not os.getenv(account.password_env):
                    errors.append(f"缺少环境变量 {account.password_env}")
        if model:
            if not self.model.name:
                errors.append("缺少 model.name")
            if not os.getenv(self.model.api_key_env):
                errors.append(f"缺少环境变量 {self.model.api_key_env}")
        if feishu:
            if self.feishu.receive_id_type not in {"user_id", "chat_id"}:
                errors.append("feishu.receive_id_type 应为 user_id 或 chat_id")
            if not self.feishu.receive_id:
                errors.append("缺少 feishu.receive_id")
            if not (self.feishu.owner_open_id or (self.feishu.receive_id_type == "user_id" and self.feishu.receive_id)):
                errors.append("缺少 feishu.owner_open_id")
        return errors
