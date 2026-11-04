from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    mode: str = "live"
    host: str = "127.0.0.1"
    port: int = 8765
    web_dist: Path | None = None
    mcp_command: str = field(
        default_factory=lambda: str(Path(sys.executable).with_name("mcp-email-server"))
    )
    lark_command: str = "lark-cli"
    forwarded_allow_ips: str = "127.0.0.1"

    def __post_init__(self):
        # Upstream's managed catalog rejects symlinks in the directory chain.
        # macOS temporary directories are commonly reached through /var.
        object.__setattr__(self, "data_dir", self.data_dir.expanduser().resolve())

    @classmethod
    def from_env(cls):
        root = (
            Path(os.environ.get("MAIL_ASSISTANT_DATA_DIR", "runtime/live")).expanduser().resolve()
        )
        mode = os.environ.get("MAIL_ASSISTANT_MODE", "live")
        if mode not in {"live", "demo"}:
            raise ValueError("MAIL_ASSISTANT_MODE must be live or demo")
        dist = os.environ.get("MAIL_ASSISTANT_WEB_DIST")
        return cls(
            root,
            mode,
            os.environ.get("MAIL_ASSISTANT_HOST", "127.0.0.1"),
            int(os.environ.get("MAIL_ASSISTANT_PORT", "8765")),
            Path(dist).resolve() if dist else None,
            os.environ.get(
                "MAIL_ASSISTANT_MCP_COMMAND",
                str(Path(sys.executable).with_name("mcp-email-server")),
            ),
            os.environ.get("MAIL_ASSISTANT_LARK_COMMAND", "lark-cli"),
            os.environ.get("MAIL_ASSISTANT_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        )

    @property
    def database(self):
        return self.data_dir / "workspace.sqlite3"

    @property
    def mcp_config(self):
        return self.data_dir / "mcp" / "config.toml"

    def mcp_env(self):
        return os.environ | {
            "MCP_EMAIL_SERVER_CONFIG_PATH": str(self.mcp_config),
            "MCP_EMAIL_SERVER_CREDENTIAL_STORAGE": "plaintext",
            "MCP_EMAIL_SERVER_LOG_LEVEL": "ERROR",
        }

    @property
    def feishu_dir(self):
        return self.data_dir / "feishu"

    def lark_env(self):
        # Only carry process essentials and transport settings. Host Agent and
        # unrelated app credentials must not choose our CLI workspace.
        allowed = {
            "PATH",
            "HOME",
            "USER",
            "LANG",
            "LC_ALL",
            "TZ",
            "TMPDIR",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        }
        return {key: value for key, value in os.environ.items() if key in allowed} | {
            "LARKSUITE_CLI_CONFIG_DIR": str(self.feishu_dir / "cli"),
            "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
            "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
        }
