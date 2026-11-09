"""Exercise isolated Compose projects; no real mailbox/model/Feishu credentials.

Use --build to exercise the one-command installer on the first startup,
or run against a prebuilt image. Only randomly named verification
projects are created and removed; existing deployments are never selected.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, HTTPSHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class Project:
    def __init__(self, image, mode="live", *, tls=False):
        self.name = "mail-check-" + uuid.uuid4().hex[:12]
        self.port, self.tls = free_port(), tls
        self.env = os.environ | {
            "MAIL_ASSISTANT_IMAGE": image,
            "MAIL_ASSISTANT_MODE": mode,
            "MAIL_ASSISTANT_HTTP_PORT": str(self.port),
            "MAIL_ASSISTANT_DOMAIN": "localhost",
            "MAIL_ASSISTANT_TLS_BIND": "127.0.0.1",
            "MAIL_ASSISTANT_TLS_PORT": str(self.port),
            "MAIL_ASSISTANT_TLS_HTTP_PORT": str(free_port()),
        }
        self.arguments = [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-p",
            self.name,
            "-f",
            str(ROOT / "compose.yaml"),
        ]
        if tls:
            self.arguments += ["-f", str(ROOT / "compose.https.yaml")]
        self.jar = http.cookiejar.CookieJar()
        self.base = f"{'https://localhost' if tls else 'http://127.0.0.1'}:{self.port}"
        self.client = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.jar))

    def run(self, *args, data=None, check=True, timeout=240, launch=False):
        command = self.arguments
        if launch:
            command = ["sh", str(ROOT / "deploy/up.sh"), *self.arguments[2:]]
        result = subprocess.run(
            command + list(args),
            cwd=ROOT,
            env=self.env,
            input=data,
            capture_output=True,
            timeout=timeout,
        )
        if check and result.returncode:
            raise RuntimeError(
                f"{self.name} {args[:2]} failed: {result.stderr.decode(errors='replace')[-2500:]}"
            )
        return result

    def start(self, *, build=False):
        print(
            f"Starting isolated {'HTTPS' if self.tls else self.env['MAIL_ASSISTANT_MODE']} project {self.name}",
            flush=True,
        )
        if build:
            launch = self.run(launch=True, timeout=1200)
            token = self.run("exec", "-T", "api", "cat", "/var/lib/mail-assistant/bootstrap.token")
            assert token.stdout.strip() in launch.stdout.splitlines()
            assert self.base.encode() in launch.stdout
        else:
            self.run("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
        if self.tls:
            for _ in range(30):
                certificate = self.run(
                    "exec",
                    "-T",
                    "caddy",
                    "cat",
                    "/data/caddy/pki/authorities/local/root.crt",
                    check=False,
                )
                if certificate.returncode == 0:
                    context = ssl.create_default_context(cadata=certificate.stdout.decode())
                    self.client = build_opener(
                        ProxyHandler({}),
                        HTTPCookieProcessor(self.jar),
                        HTTPSHandler(context=context),
                    )
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Caddy local CA was not generated")
        assert self.api("/health")["worker"]["alive"]
        assert self.run("exec", "-T", "api", "id", "-u").stdout.strip() == b"10001"
        assert b"1.0.97" in self.run("exec", "-T", "worker", "lark-cli", "--version").stdout
        with self.client.open(self.base + "/", timeout=10) as response:
            assert "text/html" in response.headers["Content-Type"]

    def api(self, path, method="GET", value=None, *, headers=None):
        data = json.dumps(value).encode() if value is not None else None
        request = Request(
            self.base + "/api/v1" + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} | (headers or {}),
        )
        with self.client.open(request, timeout=20) as response:
            return json.load(response)

    def bootstrap(self, password):
        token = self.run(
            "exec", "-T", "api", "cat", "/var/lib/mail-assistant/bootstrap.token"
        ).stdout.decode()
        self.api(
            "/auth/bootstrap",
            "POST",
            {"token": token, "password": password},
            headers={"Origin": self.base},
        )
        assert len(self.jar) == 1 and all(cookie.secure == self.tls for cookie in self.jar)
        try:
            self.api("/auth/logout", "POST", {}, headers={"Origin": "https://unrelated.invalid"})
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("Mismatched origin was accepted")

    def wait_task(self, task):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            result = self.api("/tasks/" + task["id"])
            if result["status"] == "succeeded":
                return result["result"]
            if result["status"] in {"failed", "cancelled"}:
                raise RuntimeError(
                    f"Task {result['kind']} ended as {result['status']}: {result['error_code']}"
                )
            time.sleep(0.3)
        raise TimeoutError("Container task did not complete")

    def backup(self):
        self.run("stop")
        result = self.run("run", "--rm", "--no-deps", "-T", "api", "backup", "--output", "-")
        assert result.stdout[:2] == b"\x1f\x8b"
        return result.stdout

    def restore(self, archive):
        self.run("run", "--rm", "--no-deps", "-T", "api", "restore", "--input", "-", data=archive)

    def clean(self):
        self.run("down", "--volumes", "--remove-orphans", check=False)


def import_fixture(project):
    config = """[model]
name = "container-fixture"
base_url = "http://127.0.0.1:9/v1"
api_key_env = "FIXTURE_MODEL"
[[accounts]]
name = "QQ migration fixture"
provider = "qq"
email = "qq@example.invalid"
host = "127.0.0.1"
password_env = "FIXTURE_QQ"
[[accounts]]
name = "NetEase migration fixture"
provider = "163"
email = "netease@example.invalid"
host = "127.0.0.1"
password_env = "FIXTURE_163"
"""
    dotenv = "FIXTURE_MODEL=container-fixture-key\nFIXTURE_QQ=fixture-qq-code\nFIXTURE_163=fixture-163-code\n"
    code = (
        "from pathlib import Path; import os; "
        f"Path('/tmp/legacy.toml').write_text({config!r}); "
        f"Path('/tmp/.env').write_text({dotenv!r}); "
        "os.execv('/app/.venv/bin/mail-assistant', ['mail-assistant','import-legacy','--config','/tmp/legacy.toml'])"
    )
    project.run("stop")
    result = project.run(
        "run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "api", "-c", code
    )
    assert json.loads(result.stdout)["imported_accounts"] == 2


def check_imported(project):
    assert len(project.api("/accounts")) == 2
    schedule = project.api("/settings/schedule")
    assert not schedule["enabled"] and not schedule["sync_enabled"]
    code = """
import asyncio
from mail_assistant.application.workspace import Workspace
from mail_assistant.config import Settings
async def check():
    w = Workspace(Settings.from_env())
    try:
        assert w.secrets.open(w.db.get('model')['encrypted_key']) == 'container-fixture-key'
        remote = await w.provider.list_accounts()
        assert {a['account_name'] for a in remote} == {w.accounts.get(a['id'])['upstream_name'] for a in w.accounts.list()}
    finally:
        await w.close()
    print('verified')
asyncio.run(check())
"""
    assert (
        project.run("exec", "-T", "api", "python", "-c", code).stdout.rstrip().endswith(b"verified")
    )


def verify(image, *, tls, build=False):
    projects, password = [], secrets.token_urlsafe(24)
    results = []
    try:
        live = Project(image)
        projects.append(live)
        live.start(build=build)
        if build:
            results.append("one-command source build, startup, URL and bootstrap token")
        live.run(
            "exec",
            "-T",
            "-e",
            "http_proxy=http://127.0.0.1:9",
            "-e",
            "no_proxy=",
            "api",
            "python",
            "/app/deploy/health.py",
            "api",
        )
        results.append("loopback API health check bypasses the outbound HTTP proxy")
        live.bootstrap(password)
        import_fixture(live)
        live.start()
        live.api("/auth/login", "POST", {"password": password})
        check_imported(live)
        busy = live.run(
            "run", "--rm", "--no-deps", "-T", "api", "backup", "--output", "-", check=False
        )
        assert busy.returncode != 0 and b"workspace_busy" in busy.stderr and not busy.stdout
        archive = live.backup()
        restored = Project(image)
        projects.append(restored)
        restored.restore(archive)
        restored.start()
        restored.api("/auth/login", "POST", {"password": password})
        check_imported(restored)
        restored.run("restart")
        restored.run("up", "-d", "--wait", "--wait-timeout", "90")
        assert restored.api("/health")["worker"]["alive"]
        results.append(
            "live bootstrap, UID, CLI, offline import, maintenance lock, new-volume restore, MCP discovery, key decryption, restart"
        )
        print("Live configuration and new-volume recovery passed", flush=True)

        demo = Project(image, mode="demo")
        projects.append(demo)
        demo.start()
        demo.bootstrap(password)
        demo.api("/demo/start", "POST", {})
        seeded = next(task for task in demo.api("/tasks") if task["kind"] == "digest")
        digest = demo.wait_task(seeded)
        assert len(demo.api("/digests/" + digest["digest_id"])["items"]) == 5
        repeated = demo.wait_task(demo.api("/digests", "POST", {}))
        assert repeated["reused"] and repeated["digest_id"] == digest["digest_id"]
        assert len(demo.api("/digests/" + repeated["digest_id"])["items"]) == 5
        question = demo.wait_task(
            demo.api(
                "/questions",
                "POST",
                {"question": "会议后来有更新吗？", "request_id": "container-check"},
            )
        )
        messages = demo.api(f"/conversations/{question['conversation_id']}/messages")
        assert "16:00" in messages[-1]["content"] and messages[-1]["sources"]
        expected_count = demo.api("/mails")["total"]
        demo_archive = demo.backup()
        recovered = Project(image, mode="demo")
        projects.append(recovered)
        recovered.restore(demo_archive)
        recovered.start()
        recovered.api("/auth/login", "POST", {"password": password})
        assert recovered.api("/mails")["total"] == expected_count == 6
        assert recovered.api("/digests/" + digest["digest_id"])["items"]
        assert recovered.api(f"/conversations/{question['conversation_id']}/messages")[-1][
            "sources"
        ]
        results.append(
            "separate API/Worker sample digest, question sources, mail/digest/conversation recovery"
        )
        print("Container workflow and retained sources passed", flush=True)

        if tls:
            secured = Project(image, tls=True)
            projects.append(secured)
            secured.start()
            secured.bootstrap(password)
            config = json.loads(secured.run("config", "--format", "json").stdout)
            assert not config["services"]["api"].get("ports")
            secured.run("stop", "worker")
            queued = secured.api("/settings/model/test", "POST", {})
            started = time.monotonic()
            with secured.client.open(
                secured.base + f"/api/v1/tasks/{queued['id']}/events", timeout=5
            ) as stream:
                assert stream.headers["Content-Type"].startswith("text/event-stream")
                lines = [stream.readline() for _ in range(3)]
                assert any(b"event: stage" in line for line in lines)
            assert time.monotonic() - started < 5
            assert secured.api("/tasks/" + queued["id"])["status"] == "queued"
            results.append(
                "HTTPS local CA, hostname verification, Secure session, origin, private API, SSE before task completion"
            )
            print("HTTPS authentication and unbuffered task events passed", flush=True)
        return {
            "status": "passed",
            "image": image,
            "checks": results,
            "real_external_services": False,
            "public_ca_issuance": False,
        }
    except Exception:
        for project in projects:
            logs = project.run("logs", "--no-color", "--tail", "35", check=False)
            if logs.stdout:
                print(f"Diagnostics for isolated project {project.name}:", file=sys.stderr)
                print(logs.stdout.decode(errors="replace"), file=sys.stderr)
        raise
    finally:
        for project in reversed(projects):
            project.clean()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="mail-digest-agent:local")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--https", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.image, tls=args.https, build=args.build)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
