from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=new_id)
    upstream_name: Mapped[str] = mapped_column(String(70), unique=True)
    label: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(320))
    provider: Mapped[str] = mapped_column(String(30))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=993)
    folder: Mapped[str] = mapped_column(String(200), default="INBOX")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    secret_set: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="unverified")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    upstream_revision: Mapped[int] = mapped_column(Integer, default=1)
    last_sync_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sync_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)
    removed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Mail(Base):
    __tablename__ = "mails"
    __table_args__ = (
        Index("ix_mails_account_folder_state", "account_id", "folder", "observed_state"),
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    folder: Mapped[str] = mapped_column(String(200))
    source_id: Mapped[str] = mapped_column(String(200))
    message_id: Mapped[str] = mapped_column(String(1000), default="", index=True)
    references: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(Text)
    sender: Mapped[str] = mapped_column(Text)
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    sent_at: Mapped[str] = mapped_column(String(40), index=True)
    received_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    body_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_digest: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    priority: Mapped[str | None] = mapped_column(String(10), nullable=True)
    first_seen_at: Mapped[str] = mapped_column(String(40), default=stamp)
    observed_state: Mapped[str] = mapped_column(
        String(20), default="unknown", server_default="unknown"
    )
    source_epoch: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_observed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Coverage(Base):
    __tablename__ = "coverage"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    start_at: Mapped[str] = mapped_column(String(40))
    end_at: Mapped[str] = mapped_column(String(40))
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)


class DigestGroup(Base):
    __tablename__ = "digest_groups"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[str] = mapped_column(String(10))
    timezone: Mapped[str] = mapped_column(String(80))
    scope: Mapped[list] = mapped_column(JSON)
    latest_digest_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    active_task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    checked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    checked_start: Mapped[str | None] = mapped_column(String(40), nullable=True)
    checked_end: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class MailAnalysis(Base):
    __tablename__ = "mail_analysis"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mail_id: Mapped[str] = mapped_column(ForeignKey("mails.id"), index=True)
    item: Mapped[dict] = mapped_column(JSON)
    valid_until: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Digest(Base):
    __tablename__ = "digests"
    __table_args__ = (UniqueConstraint("group_id", "revision"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    items: Mapped[list] = mapped_column(JSON, default=list)
    coverage: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="generated")
    delivery_status: Mapped[str] = mapped_column(String(30), default="not_configured")
    created_at: Mapped[str] = mapped_column(String(40), default=stamp, index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("digest_groups.id"), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    legacy: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    range_start: Mapped[str | None] = mapped_column(String(40), nullable=True)
    range_end: Mapped[str | None] = mapped_column(String(40), nullable=True)
    timezone: Mapped[str] = mapped_column(
        String(80), default="Asia/Shanghai", server_default="Asia/Shanghai"
    )
    source_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sources: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(100), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    channel: Mapped[str] = mapped_column(String(20), default="web")
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(40))
    lane: Mapped[str] = mapped_column(String(20), default="background", index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    phase: Mapped[str] = mapped_column(String(200), default="等待执行")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    available_at: Mapped[str] = mapped_column(String(40), default=stamp)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class TaskEvent(Base):
    __tablename__ = "task_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[str] = mapped_column(String(40))


class Receipt(Base):
    __tablename__ = "receipts"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("binding_id", "kind", "source_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=new_id)
    binding_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[str] = mapped_column(String(100))
    recipient_chat_id: Mapped[str] = mapped_column(String(200))
    reply_to: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    chunks: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)
    updated_at: Mapped[str] = mapped_column(String(40), default=stamp)
