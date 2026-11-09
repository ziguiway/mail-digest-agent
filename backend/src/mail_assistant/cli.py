from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import shutil
import tempfile
from dataclasses import replace
from contextlib import redirect_stdout
from pathlib import Path
import uvicorn
from .config import Settings
from .domain.errors import AppError
from .infrastructure.maintenance import MAX_BYTES, MAX_MANIFEST, MAX_FILES, backup, restore


def main():
    try:
        return _main()
    except AppError as exc:
        print(
            json.dumps({"error": exc.message, "code": exc.code}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    except OSError:
        print("文件或进程操作失败，请检查路径、权限和所需组件", file=sys.stderr)
        return 2


def _main():
    parser = argparse.ArgumentParser(description="来信 · 自托管多邮箱智能工作台")
    parser.add_argument("--data-dir")
    parser.add_argument("--port", type=int)
    parser.add_argument("--host")
    commands = parser.add_subparsers(dest="command", required=True)
    api_command = commands.add_parser("api")
    api_command.add_argument(
        "--embedded-worker", action="store_true", help="在 API 进程内运行后台任务"
    )
    for name in ("worker", "run", "demo", "doctor", "bootstrap-token"):
        commands.add_parser(name)
    backup_command = commands.add_parser("backup", help="停止服务后备份整个工作空间")
    backup_command.add_argument(
        "--output", type=Path, required=True, help="归档路径，或 - 输出归档到 stdout"
    )
    restore_command = commands.add_parser("restore", help="将备份恢复到新的空数据目录")
    restore_command.add_argument(
        "--input", type=Path, required=True, help="归档路径，或 - 从 stdin 读取归档"
    )
    import_command = commands.add_parser("import-legacy", help="预检或导入原型 TOML／.env 配置")
    import_command.add_argument("--config", type=Path, required=True)
    import_command.add_argument("--env-file", type=Path)
    import_command.add_argument("--dry-run", action="store_true")
    schema = commands.add_parser("schema")
    schema.add_argument("--output", default="backend/openapi.json")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = Settings.from_env()
    if args.data_dir:
        settings = replace(settings, data_dir=Path(args.data_dir).expanduser().resolve())
    if args.command == "demo":
        settings = replace(
            settings,
            mode="demo",
            data_dir=Path(args.data_dir or "runtime/demo").expanduser().resolve(),
        )
    settings = replace(settings, host=args.host or settings.host, port=args.port or settings.port)
    if args.command == "import-legacy":
        # Framework imports configure logging; bind their streams to stderr so
        # command stdout remains a single machine-readable JSON report.
        with redirect_stdout(sys.stderr):
            from .application.import_legacy import read_legacy, import_legacy

            config = read_legacy(args.config, args.env_file)
            result = (
                config.report() if args.dry_run else asyncio.run(import_legacy(settings, config))
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 2
    if args.command in {"backup", "restore"}:
        stream = str(args.output if args.command == "backup" else args.input) == "-"
        if stream:
            with tempfile.TemporaryDirectory(prefix="mail-maintenance-") as temporary:
                archive = Path(temporary) / "workspace.tar.gz"
                if args.command == "backup":
                    result = backup(settings, archive)
                    with archive.open("rb") as handle:
                        shutil.copyfileobj(handle, sys.stdout.buffer)
                    result["path"] = "stdout"
                else:
                    total = 0
                    with archive.open("xb") as handle:
                        archive.chmod(0o600)
                        while block := sys.stdin.buffer.read(1024 * 1024):
                            total += len(block)
                            if total > MAX_BYTES + MAX_MANIFEST + MAX_FILES * 1024:
                                raise AppError("输入归档超过大小限制", "backup_too_large")
                            handle.write(block)
                    result = restore(settings, archive)
            print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            result = (
                backup(settings, args.output)
                if args.command == "backup"
                else restore(settings, args.input)
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    from .api.app import create_app
    from .application.workspace import Workspace
    from .worker import Worker

    if args.command == "schema":
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(create_app(settings).openapi(), ensure_ascii=False, indent=2))
        print(path.resolve())
        return 0
    if args.command == "run":
        env = os.environ | {
            "MAIL_ASSISTANT_DATA_DIR": str(settings.data_dir),
            "MAIL_ASSISTANT_MODE": settings.mode,
        }
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "mail_assistant",
                    "--data-dir",
                    str(settings.data_dir),
                    "--port",
                    str(settings.port),
                    "--host",
                    settings.host,
                    command,
                ],
                env=env,
            )
            for command in ("api", "worker")
        ]
        try:
            while all(p.poll() is None for p in processes):
                processes[0].wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                while all(p.poll() is None for p in processes):
                    import time

                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
        except KeyboardInterrupt:
            pass
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                process.wait()
        return 0
    if args.command in {"api", "demo"}:
        print(f"工作台：http://{settings.host}:{settings.port}")
        if settings.mode == "live":
            print(
                f"首次初始化凭证保存在 {settings.data_dir / 'bootstrap.token'}，可用 bootstrap-token 命令查看"
            )
        uvicorn.run(
            create_app(settings, embedded_worker=args.command == "demo" or args.embedded_worker),
            host=settings.host,
            port=settings.port,
            proxy_headers=True,
            forwarded_allow_ips=settings.forwarded_allow_ips,
        )
        return 0
    workspace = Workspace(settings)
    if args.command == "bootstrap-token":
        print(workspace.secrets.bootstrap_token)
        asyncio.run(workspace.close())
        return 0
    if args.command == "doctor":
        print(
            json.dumps(
                {
                    "mode": settings.mode,
                    "data_dir": str(settings.data_dir),
                    "initialized": bool(workspace.db.get("owner")),
                    "model": workspace.model_view(),
                    "accounts": workspace.accounts.list(),
                    "worker": workspace.db.get("worker", {}),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        asyncio.run(workspace.close())
        return 0

    async def serve():
        worker = Worker(workspace)
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, worker.stop.set)
        try:
            await worker.run()
        finally:
            await workspace.close()

    asyncio.run(serve())
    return 0
