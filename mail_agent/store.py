from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


def normalize_subject(subject: str) -> str:
    return re.sub(r"^(?:(?:re|fw|fwd|回复|答复|转发)\s*[:：]\s*)+", "", subject.strip(), flags=re.I).strip().casefold()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS mails (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, uidvalidity TEXT NOT NULL,
                uid INTEGER NOT NULL, message_id TEXT NOT NULL, refs TEXT NOT NULL,
                subject TEXT NOT NULL, norm_subject TEXT NOT NULL,
                sender TEXT NOT NULL, recipient TEXT NOT NULL,
                sent_at TEXT NOT NULL, received_at TEXT NOT NULL,
                body TEXT NOT NULL, body_truncated INTEGER NOT NULL DEFAULT 0,
                digested INTEGER NOT NULL DEFAULT 0,
                UNIQUE(account, uidvalidity, uid)
            );
            CREATE INDEX IF NOT EXISTS mails_received ON mails(account, received_at);
            CREATE INDEX IF NOT EXISTS mails_message_id ON mails(message_id);
            CREATE TABLE IF NOT EXISTS sync_state (
                account TEXT PRIMARY KEY, uidvalidity TEXT NOT NULL, last_uid INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS digests (
                id TEXT PRIMARY KEY, body TEXT NOT NULL, mail_ids TEXT NOT NULL,
                completed_accounts TEXT NOT NULL, end_at TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS handled_events (message_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS event_inbox (message_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)

    @contextmanager
    def transaction(self):
        with self.lock, self.db:
            yield self.db

    def close(self):
        self.db.close()

    def get(self, key: str, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value):
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def sync_state(self, account: str):
        with self.lock:
            row = self.db.execute("SELECT * FROM sync_state WHERE account=?", (account,)).fetchone()
        return dict(row) if row else None

    def put_mail(self, mail: dict, *, digested: bool = False):
        data = {**mail, "refs": json.dumps(mail.get("refs", [])), "norm_subject": normalize_subject(mail["subject"]), "digested": int(digested)}
        keys = list(data)
        with self.transaction() as db:
            db.execute(f"INSERT OR IGNORE INTO mails ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", [data[k] for k in keys])

    def advance_sync(self, account: str, validity: str, uid: int):
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO sync_state VALUES (?,?,?)", (account, validity, uid))

    @staticmethod
    def _mail(row):
        if row is None:
            return None
        mail = dict(row)
        mail["refs"] = json.loads(mail["refs"])
        mail["body_truncated"] = bool(mail["body_truncated"])
        return mail

    def read(self, mail_id: str, accounts: list[str]):
        if not accounts:
            return None
        with self.lock:
            row = self.db.execute(f"SELECT * FROM mails WHERE id=? AND account IN ({','.join('?' for _ in accounts)})", [mail_id, *accounts]).fetchone()
        return self._mail(row)

    def search(self, accounts: list[str], *, keywords=None, sender="", since="", before="", account="", limit=20, offset=0):
        if account:
            accounts = [account] if account in accounts else []
        if not accounts:
            return {"mails": [], "has_more": False}
        limit = max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        clauses = [f"account IN ({','.join('?' for _ in accounts)})"]
        params: list = list(accounts)
        for term in keywords or []:
            if not isinstance(term, str):
                raise ValueError("keywords must contain strings")
            clauses.append("(instr(lower(subject),lower(?))>0 OR instr(lower(body),lower(?))>0 OR instr(lower(sender),lower(?))>0)")
            params.extend([term] * 3)
        if sender:
            clauses.append("instr(lower(sender),lower(?))>0")
            params.append(sender)
        if since:
            clauses.append("received_at>=?")
            params.append(since)
        if before:
            clauses.append("received_at<?")
            params.append(before)
        with self.lock:
            rows = self.db.execute("SELECT * FROM mails WHERE " + " AND ".join(clauses) + " ORDER BY received_at DESC, id LIMIT ? OFFSET ?", [*params, limit + 1, offset]).fetchall()
        return {"mails": [self._mail(r) for r in rows[:limit]], "has_more": len(rows) > limit, "next_offset": offset + limit if len(rows) > limit else None}

    def pending(self, accounts: list[str], limit=100):
        if not accounts:
            return [], False
        with self.lock:
            rows = self.db.execute(f"SELECT * FROM mails WHERE digested=0 AND account IN ({','.join('?' for _ in accounts)}) ORDER BY received_at,id LIMIT ?", [*accounts, limit + 1]).fetchall()
        return [self._mail(r) for r in rows[:limit]], len(rows) > limit

    def thread(self, mail_id: str, accounts: list[str]):
        seed = self.read(mail_id, accounts)
        if seed is None:
            raise ValueError("未找到邮件，或该邮箱当前未启用")
        with self.lock:
            rows = self.db.execute(f"SELECT * FROM mails WHERE account IN ({','.join('?' for _ in accounts)})", accounts).fetchall()
        all_mails = [self._mail(r) for r in rows]
        ids = {seed["message_id"], *seed["refs"]} - {""}
        selected = {seed["id"]: seed}
        changed = True
        while changed:
            changed = False
            for mail in all_mails:
                links = {mail["message_id"], *mail["refs"]} - {""}
                if mail["id"] not in selected and ids.intersection(links):
                    selected[mail["id"]] = mail
                    ids.update(links)
                    changed = True
        linked = sorted(selected.values(), key=lambda m: m["received_at"])
        # Subject matches are candidates only; they do not prove a reply relationship.
        candidates = [m for m in all_mails if m["id"] not in selected and m["norm_subject"] == seed["norm_subject"]]
        return {"linked": linked[-30:], "linked_truncated": len(linked) > 30, "subject_candidates": sorted(candidates, key=lambda m: m["received_at"])[-10:], "subject_candidates_truncated": len(candidates) > 10}

    def pending_digest(self):
        with self.lock:
            row = self.db.execute("SELECT * FROM digests WHERE delivered=0 ORDER BY rowid LIMIT 1").fetchone()
        if not row:
            return None
        data = dict(row)
        for key in ("mail_ids", "completed_accounts"):
            data[key] = json.loads(data[key])
        return data

    def save_digest(self, digest: dict):
        with self.transaction() as db:
            db.execute("INSERT INTO digests(id,body,mail_ids,completed_accounts,end_at) VALUES (?,?,?,?,?)", (digest["id"], digest["body"], json.dumps(digest["mail_ids"]), json.dumps(digest["completed_accounts"]), digest["end_at"]))

    def commit_digest(self, digest: dict):
        with self.transaction() as db:
            for mail_id in digest["mail_ids"]:
                db.execute("UPDATE mails SET digested=1 WHERE id=?", (mail_id,))
            db.execute("UPDATE digests SET delivered=1 WHERE id=?", (digest["id"],))
            for account in digest["completed_accounts"]:
                db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (f"digest_since:{account}", json.dumps(digest["end_at"])))

    def handled(self, message_id: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM handled_events WHERE message_id=?", (message_id,)).fetchone() is not None

    def mark_handled(self, message_id: str):
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO handled_events VALUES (?)", (message_id,))
            db.execute("DELETE FROM event_inbox WHERE message_id=?", (message_id,))

    def queue_event(self, event: dict):
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM handled_events WHERE message_id=?", (event["message_id"],)).fetchone():
                db.execute("INSERT OR IGNORE INTO event_inbox VALUES (?,?)", (event["message_id"], json.dumps(event, ensure_ascii=False)))

    def queued_events(self):
        with self.lock:
            rows = self.db.execute("SELECT payload FROM event_inbox ORDER BY rowid LIMIT 100").fetchall()
        return [json.loads(r[0]) for r in rows]
