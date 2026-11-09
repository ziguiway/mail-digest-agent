"""Read-only container health checks without application initialization."""

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


def check(component):
    if component in {"api", "standalone"}:
        client = build_opener(ProxyHandler({}))
        with client.open("http://127.0.0.1:8765/api/v1/health", timeout=3) as response:
            state = json.load(response)
            return state["status"] == "ok" and (component == "api" or state["worker"]["alive"])
    path = Path(os.environ.get("MAIL_ASSISTANT_DATA_DIR", "runtime/live")) / "workspace.sqlite3"
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=3) as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = 'worker'").fetchone()
    state = json.loads(row[0]) if row else {}
    return state.get("status") == "running" and datetime.fromisoformat(
        state["heartbeat_at"]
    ) > datetime.now(UTC) - timedelta(seconds=25)


if __name__ == "__main__":
    try:
        healthy = check(sys.argv[1])
    except Exception:
        healthy = False
    sys.exit(0 if healthy else 1)
