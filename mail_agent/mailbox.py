from __future__ import annotations

import hashlib
import imaplib
import os
import re
import ssl
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from .config import Account, Config
from .store import Store

UTC = timezone.utc


def utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "br", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def parse_mail(raw: bytes, account: Account, validity: str, uid: int, received_at: datetime) -> dict:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    part = message.get_body(preferencelist=("plain", "html"))
    body = ""
    if part is not None:
        try:
            body = part.get_content()
        except (LookupError, UnicodeError):
            body = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        if not isinstance(body, str):
            body = ""
        if part.get_content_type() == "text/html":
            parser = HTMLText()
            parser.feed(body)
            body = "".join(parser.parts)
    body = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", body).strip()
    try:
        sent_at = parsedate_to_datetime(str(message.get("Date", "")))
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=UTC)
    except (ValueError, TypeError, IndexError, OverflowError):
        sent_at = received_at
    message_id = str(message.get("Message-ID", "")).strip()
    identity = message_id or f"{validity}:{uid}"
    mail_id = "m_" + hashlib.sha256(f"{account.name}:{identity}".encode()).hexdigest()[:16]
    return {
        "id": mail_id, "account": account.name, "uidvalidity": validity, "uid": uid,
        "message_id": message_id,
        "refs": list(dict.fromkeys(re.findall(r"<[^<>\s]+>", str(message.get("References", "")) + " " + str(message.get("In-Reply-To", ""))))),
        "subject": str(message.get("Subject", "（无主题）")),
        "sender": str(message.get("From", "")), "recipient": str(message.get("To", "")),
        "sent_at": utc_iso(sent_at), "received_at": utc_iso(received_at),
        "body": body[:40000], "body_truncated": int(len(body) > 40000),
    }


class MailSync:
    def __init__(self, config: Config, store: Store, connection_factory=None):
        self.config, self.store = config, store
        self.connection_factory = connection_factory or imaplib.IMAP4_SSL

    def sync_account(self, account: Account, now: datetime) -> dict:
        password = os.getenv(account.password_env)
        if not password:
            raise ValueError(f"缺少环境变量 {account.password_env}")
        signature = [account.email, account.server, account.folder]
        old_signature = self.store.get(f"account_signature:{account.name}")
        if old_signature is not None and old_signature != signature:
            raise ValueError("邮箱身份发生变化，请使用新的 accounts.name 或新的数据库")
        client = self.connection_factory(account.server, account.port, ssl_context=ssl.create_default_context(), timeout=30)
        try:
            client.login(account.email, password)
            capabilities = {c.decode().upper() if isinstance(c, bytes) else str(c).upper() for c in client.capabilities}
            if "ID" in capabilities:
                # imaplib has no public ID convenience method. Register RFC 2971
                # before using its low-level command API (needed by NetEase).
                imaplib.Commands.setdefault("ID", ("AUTH",))
                status, _ = client._simple_command("ID", '("name" "MailDigestAgent" "version" "0.1.0" "vendor" "course-project")')
                if status != "OK":
                    raise RuntimeError("IMAP ID 握手失败")
            status, _ = client.select(account.folder, readonly=True)
            if status != "OK":
                raise RuntimeError("无法只读打开邮箱文件夹，请检查 IMAP 服务与授权码")
            _, validity_data = client.response("UIDVALIDITY")
            if not validity_data or not validity_data[0]:
                raise RuntimeError("邮箱没有返回 UIDVALIDITY，停止同步以避免跳过邮件")
            validity = validity_data[0].decode()
            state = self.store.sync_state(account.name)
            last_uid = state["last_uid"] if state and state["uidvalidity"] == validity else 0
            since_key = f"digest_since:{account.name}"
            first_digest_start = self.store.get(since_key)
            bootstrap = first_digest_start is None
            if bootstrap:
                first_digest_start = utc_iso(now - timedelta(hours=self.config.first_digest_hours))
                self.store.set(since_key, first_digest_start)
            self.store.set(f"account_signature:{account.name}", signature)
            if last_uid:
                criteria = f"UID {last_uid + 1}:*"
            else:
                start = now - timedelta(days=self.config.initial_cache_days)
                month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][start.month - 1]
                criteria = f'SINCE "{start.day:02d}-{month}-{start.year}"'
            status, data = client.uid("SEARCH", None, criteria)
            if status != "OK":
                raise RuntimeError("IMAP 搜索失败")
            # N:* can include the last message even when N exceeds the largest
            # UID. Always filter numerically instead of trusting that range.
            uids = sorted({int(uid) for uid in (data[0] or b"").split() if int(uid) > last_uid})
            chosen = uids[:self.config.max_sync_messages]
            for uid in chosen:
                status, data = client.uid("FETCH", str(uid), "(BODY.PEEK[] INTERNALDATE)")
                if status != "OK":
                    raise RuntimeError(f"读取 UID {uid} 失败，下次将从此处重试")
                literal = next((x for x in data if isinstance(x, tuple) and len(x) == 2 and isinstance(x[1], bytes)), None)
                if literal is None:
                    raise RuntimeError(f"UID {uid} 缺少邮件正文，下次重试")
                date_match = re.search(rb'INTERNALDATE "([^"]+)"', literal[0])
                if not date_match:
                    raise RuntimeError(f"UID {uid} 缺少 INTERNALDATE")
                received_at = parsedate_to_datetime(date_match.group(1).decode())
                mail = parse_mail(literal[1], account, validity, uid, received_at)
                # Initial backfill is searchable, but only the configured recent
                # window is included in the first notification. Persisted start
                # also applies when bootstrap spans several runs.
                historical = mail["received_at"] < first_digest_start and not self.store.get(f"backfill_done:{account.name}", False)
                self.store.put_mail(mail, digested=historical)
                self.store.advance_sync(account.name, validity, uid)
            if not chosen:
                self.store.advance_sync(account.name, validity, last_uid)
            has_more = len(uids) > len(chosen)
            if not has_more:
                self.store.set(f"backfill_done:{account.name}", True)
            self.store.set(f"sync_at:{account.name}", utc_iso(now))
            return {"account": account.name, "ok": True, "fetched": len(chosen), "has_more": has_more}
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    def run(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(UTC)
        results = []
        for account in self.config.accounts:
            if not account.enabled:
                continue
            try:
                results.append(self.sync_account(account, now))
            except Exception as error:
                # Do not echo raw IMAP login responses or credentials to logs.
                hint = str(error) if isinstance(error, ValueError) else "连接、授权或读取失败；请检查 IMAP 设置后重试"
                results.append({"account": account.name, "ok": False, "error": hint, "error_type": type(error).__name__})
        return results
