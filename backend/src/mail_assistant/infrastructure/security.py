from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path
from cryptography.fernet import Fernet


def private_file(path: Path, content: bytes) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
    return content


class Secrets:
    def __init__(self, root: Path):
        self.fernet = Fernet(private_file(root / "master.key", Fernet.generate_key()))
        self.bootstrap_token = private_file(
            root / "bootstrap.token", secrets.token_urlsafe(32).encode()
        ).decode()

    def seal(self, value: str):
        return self.fernet.encrypt(value.encode()).decode()

    def open(self, value: str):
        return self.fernet.decrypt(value.encode()).decode()


def password_hash(password: str, salt=None):
    salt = salt or secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, maxmem=64 * 1024 * 1024)
    return base64.b64encode(salt + key).decode()


def password_matches(password, stored):
    try:
        salt = base64.b64decode(stored)[:16]
        return hmac.compare_digest(password_hash(password, salt), stored)
    except (ValueError, TypeError):
        return False


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()
