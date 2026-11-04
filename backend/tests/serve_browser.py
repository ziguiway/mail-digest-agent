"""Start an isolated demo workspace for browser tests; never use live settings."""

import tempfile
from pathlib import Path

import uvicorn
from mail_assistant.api.app import create_app
from mail_assistant.config import Settings

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="mail-browser-") as directory:
        uvicorn.run(
            create_app(Settings(Path(directory), mode="demo"), embedded_worker=True),
            host="127.0.0.1",
            port=8771,
        )
