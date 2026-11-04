"""Executable CLI fixture. Only temporary test directories are touched."""

import base64
import json
import os
import select
import sys
import time
from pathlib import Path

directory = Path(os.environ["LARKSUITE_CLI_CONFIG_DIR"]).parent
directory.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]
assert args[0] == "--profile"
profile, args = args[1], args[2:]
control_path = directory / "fake-control.json"


def control():
    return json.loads(control_path.read_text()) if control_path.exists() else {}


def log(value):
    with (directory / "fake-log.ndjson").open("a") as handle:
        handle.write(json.dumps({"profile": profile, **value}, ensure_ascii=False) + "\n")


log({"args": args})
if args[:2] == ["config", "show"]:
    path = directory / (profile + ".json")
    if not path.exists():
        print(
            json.dumps({"ok": False, "error": {"type": "config", "subtype": "not_configured"}}),
            file=sys.stderr,
        )
        sys.exit(2)
    print(path.read_text())
elif args[:2] == ["config", "init"]:
    if "--new" in args:
        print(
            "  https://open.feishu.cn/fake/verify?code=test%2Bopaque&lang=zh\n",
            file=sys.stderr,
            flush=True,
        )
        while control().get("wait_authorization"):
            time.sleep(0.02)
        if control().get("authorization_expired"):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {"type": "authorization", "subtype": "authorization_expired"},
                    }
                ),
                file=sys.stderr,
            )
            sys.exit(3)
    else:
        secret = sys.stdin.readline().strip()
        log({"secret_on_stdin": bool(secret)})
    result = {"appId": "cli_course_test", "appSecret": "****", "brand": "feishu"}
    (directory / (profile + ".json")).write_text(json.dumps(result))
    print(json.dumps(result), flush=True)
elif args[:2] == ["auth", "qrcode"]:
    path = Path(args[args.index("--output") + 1])
    assert not path.is_absolute() and ".." not in path.parts
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aPXsAAAAASUVORK5CYII="
        )
    )
elif args[:2] == ["event", "consume"]:
    count_path = directory / "fake-consume-count"
    count = int(count_path.read_text()) + 1 if count_path.exists() else 1
    count_path.write_text(str(count))
    if control().get("consume_fail_once") and count == 1:
        print(
            json.dumps({"ok": False, "error": {"type": "network", "subtype": "connection_failed"}}),
            file=sys.stderr,
        )
        sys.exit(4)
    path = directory / "fake-events.ndjson"
    path.touch(exist_ok=True)
    time.sleep(float(control().get("ready_delay", 0)))
    print("[event] ready event_key=im.message.receive_v1", file=sys.stderr, flush=True)
    with path.open() as handle:
        while True:
            line = handle.readline()
            if line:
                print(line.rstrip(), flush=True)
            else:
                if select.select([sys.stdin], [], [], 0.02)[0] and not sys.stdin.readline():
                    break
elif args[:2] == ["event", "stop"]:
    print(json.dumps({"stopped": True}))
elif args[0] == "im":
    content = json.loads(sys.stdin.read())
    path = directory / "fake-send-count"
    count = int(path.read_text()) + 1 if path.exists() else 1
    path.write_text(str(count))
    log({"send_number": count, "content": content})
    config = control()
    if count == config.get("send_fail_at"):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "type": "authorization",
                        "subtype": "missing_scope",
                        "missing_scopes": ["im:message:send_as_bot"],
                        "console_url": "https://open.feishu.cn/fake/permissions?app=course%2Btest",
                    },
                }
            ),
            file=sys.stderr,
        )
        sys.exit(3)
    if config.get("send_no_output"):
        sys.exit(0)
    if config.get("send_sleep"):
        time.sleep(config["send_sleep"])
    print(
        json.dumps(
            {
                "ok": True,
                "identity": "bot",
                "data": {
                    "message_id": f"om_sent_{count}",
                    "chat_id": config.get("chat_id", "oc_owner"),
                },
            }
        )
    )
else:
    raise SystemExit(2)
