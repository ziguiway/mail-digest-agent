from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, Field, field_validator


class BootstrapRequest(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class ModelInput(BaseModel):
    base_url: str = Field(default="https://api.openai.com/v1", max_length=500)
    name: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=2000)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("服务地址须以 http:// 或 https:// 开头")
        parsed = urlsplit(value)
        if parsed.hostname == "openrouter.ai" and parsed.path in {"", "/api"}:
            value = parsed._replace(path="/api/v1").geturl()
        return value


class ModelView(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    name: str = ""
    key_set: bool = False
    status: str = "unconfigured"
    capabilities: dict = Field(default_factory=dict)
    error: str | None = None
    revision: int = 0


class AccountInput(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    provider: Literal["qq", "163", "custom"] = "qq"
    host: str = Field(default="", max_length=255)
    port: int = Field(default=993, ge=1, le=65535)
    folder: str = Field(default="INBOX", min_length=1, max_length=200)
    password: str | None = Field(default=None, max_length=2000)
    enabled: bool = True
    expected_revision: int | None = None

    @field_validator("host", "folder")
    @classmethod
    def no_controls(cls, value, info):
        if any(c in value for c in "\r\n\0"):
            raise ValueError("配置不能包含控制字符")
        return value.strip() if info.field_name == "host" else value


class AccountView(BaseModel):
    id: str
    label: str
    email: str
    provider: str
    host: str
    port: int
    folder: str
    enabled: bool
    secret_set: bool
    status: str
    error: str | None
    revision: int
    last_sync_at: str | None
    sync_complete: bool
    sync_method: Literal["uid_state", "uid_inventory"] | None = None
    sync_diagnostics: list[str] = Field(default_factory=list)
    created_at: str
    removed_at: str | None = None


class MailboxFolder(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    delimiter: str | None = None
    flags: list[str] = Field(default_factory=list)
    selectable: bool = True


class ScheduleInput(BaseModel):
    enabled: bool = False
    sync_enabled: bool = True
    digest_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "Asia/Shanghai"
    account_ids: list[str] = Field(default_factory=list)
    sync_minutes: int = Field(default=5, ge=1, le=1440)
    initial_days: int = Field(default=30, ge=1, le=365)
    first_digest_hours: int = Field(default=24, ge=1, le=720)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("未知时区")
        return value


class TaskInput(BaseModel):
    account_ids: list[str] = Field(default_factory=list)


class DigestInput(TaskInput):
    force_reanalyze: bool = False
    request_id: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"
    )


class QuestionInput(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    account_ids: list[str] = Field(default_factory=list)
    request_id: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"
    )


class TaskView(BaseModel):
    id: str
    kind: str
    lane: str
    status: str
    phase: str
    payload: dict
    result: dict | None
    error: str | None
    error_code: str | None
    attempts: int
    max_attempts: int
    created_at: str
    started_at: str | None
    finished_at: str | None


class MailView(BaseModel):
    id: str
    account_id: str
    account_label: str = ""
    subject: str
    sender: str
    recipients: list[str]
    sent_at: str
    received_at: str | None
    body: str
    body_truncated: bool
    priority: str | None
    folder: str
    message_id: str
    references: list[str]
    observed_state: Literal["current", "historical", "unknown"] = "unknown"
    last_observed_at: str | None = None


class DigestItem(BaseModel):
    mail_id: str
    priority: Literal["high", "medium", "low"]
    summary: str = Field(min_length=1)
    action: str = Field(min_length=1)
    deadline: str | None = None
    reason: str = Field(min_length=1)


class DigestView(BaseModel):
    id: str
    title: str
    items: list[dict]
    coverage: list[dict]
    status: str
    delivery_status: str
    created_at: str
    group_id: str | None = None
    revision: int = 0
    legacy: bool = True
    range_start: str | None = None
    range_end: str | None = None
    timezone: str = "Asia/Shanghai"
    source_count: int = 0
    account_ids: list[str] = Field(default_factory=list)
    checked_at: str | None = None
    checked_start: str | None = None
    checked_end: str | None = None
    refresh_error: str | None = None


class ConversationView(BaseModel):
    id: str
    title: str
    channel: str
    created_at: str


class QueryTableRow(BaseModel):
    mail_id: str = Field(min_length=1, max_length=80)
    handling: Literal["action", "confirm", "waiting", "notice"]
    action: str = Field(min_length=1, max_length=600)


class QueryAnswer(BaseModel):
    answer: str = Field(min_length=1)
    mail_ids: list[str] = Field(max_length=50)
    rows: list[QueryTableRow] = Field(default_factory=list, max_length=50)


class QueryPresentation(BaseModel):
    version: Literal[1] = 1
    type: Literal["mail_table"] = "mail_table"
    summary: str
    rows: list[QueryTableRow]
    notes: list[str] = Field(default_factory=list)


class MessageView(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    sources: list[dict]
    task_id: str | None
    created_at: str
    presentation: QueryPresentation | None = None


class FeishuInput(BaseModel):
    enabled: bool = False
    binding_id: str


class FeishuBindInput(BaseModel):
    mode: Literal["new", "existing"] = "new"
    previous_binding_id: str = ""
    brand: Literal["feishu", "lark"] = "feishu"
    app_id: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_-]*$")
    app_secret: str | None = Field(default=None, max_length=2000)


class FeishuTargetInput(BaseModel):
    binding_id: str


class DeliveryRetryInput(BaseModel):
    allow_uncertain: bool = False


class FeishuView(BaseModel):
    binding_id: str = ""
    revision: int = 0
    status: str = "unconfigured"
    configured: bool = False
    enabled: bool = False
    profile: str | None = None
    app_id: str | None = None
    urls: list[dict] = Field(default_factory=list)
    auth_task_id: str | None = None
    authorization_expires_at: str | None = None
    owner_open_id: str | None = None
    chat_id: str | None = None
    paired_at: str | None = None
    pairing_expires_at: str | None = None
    pairing_code: str | None = None
    last_test_at: str | None = None
    error: str | None = None
    error_code: str | None = None
    listener: dict = Field(default_factory=dict)
    test_message: str
    simulated: bool = False


class DeliveryView(BaseModel):
    id: str
    kind: str
    source_id: str
    binding_id: str
    recipient_chat_id: str
    status: str
    error: str | None
    chunks: list[dict]
    created_at: str
    updated_at: str
    task_id: str | None = None
    task_status: str | None = None


class ImportInput(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
