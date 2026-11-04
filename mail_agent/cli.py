from __future__ import annotations

import argparse
import fcntl
import json
import logging
import shutil
import sys
import tempfile
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

from .agent import MailAgent
from .config import Config
from .demo import NOTICE, create_demo
from .feishu import Feishu
from .mailbox import MailSync
from .model import ChatModel
from .service import Service
from .store import Store


@contextmanager
def process_lock(database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(database) + ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("已有进程使用这个数据库；请通过飞书查询，或先停止已有服务") from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def parser():
    result = argparse.ArgumentParser(description="QQ / 网易多邮箱摘要与飞书查询智能体")
    result.add_argument("--config", default="config.toml", help="配置文件路径")
    result.add_argument("--accounts", help="仅启用指定账号配置名，例如 qq,163")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="创建配置模板，不覆盖已有文件")
    doctor = commands.add_parser("doctor", help="检查本地配置与命令是否存在")
    doctor.add_argument("--connect", action="store_true", help="实际连接邮箱并执行只读同步")
    commands.add_parser("sync", help="只读增量同步邮箱")
    digest = commands.add_parser("digest", help="生成摘要；默认只预览，不更新发送进度")
    digest.add_argument("--send", action="store_true", help="通过机器人发送到配置的飞书接收对象")
    ask = commands.add_parser("ask", help="自然语言查询邮件")
    ask.add_argument("question")
    ask.add_argument("--session", default="terminal")
    ask.add_argument("--trace", action="store_true", help="显示工具调用过程")
    commands.add_parser("chat", help="终端多轮查询；输入 /exit 退出")
    commands.add_parser("serve", help="运行每天摘要调度与飞书消息监听")
    demo = commands.add_parser("demo", help="运行无需凭据的离线样例演示")
    demo.add_argument("--output", help="将演示报告保存到 Markdown 文件")
    return result


def run_demo(output=None):
    with tempfile.TemporaryDirectory(prefix="mail-agent-demo-") as temp:
        root = Path(temp)
        agent = create_demo(root, root / "demo.sqlite3")
        try:
            sections = [NOTICE, "", agent.digest(agent.sync.run())["body"]]
            for question in ("找一下导师的会议通知", "那封邮件后来有没有更新？最终时间是什么？"):
                answer = agent.ask(question)
                sections.extend(["", f"用户：{question}", "", answer, "", "工具调用：" + " → ".join(t["tool"] for t in agent.last_trace)])
            text = "\n".join(sections)
            print(text)
            if output:
                path = Path(output).expanduser().resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                print(f"\n演示报告：{path}")
        finally:
            agent.store.close()


def run(args):
    if args.command == "demo":
        run_demo(args.output)
        return 0
    path = Path(args.config).expanduser().resolve()
    if args.command == "init":
        path.parent.mkdir(parents=True, exist_ok=True)
        templates = files("mail_agent").joinpath("templates")
        for source, target in ((templates.joinpath("config.toml"), path), (templates.joinpath("secrets.env"), path.parent / ".env")):
            if not target.exists():
                with target.open("x") as f:
                    f.write(source.read_text())
                target.chmod(0o600)
                print(f"已创建：{target}")
            else:
                print(f"已保留：{target}")
        print("填写 config.toml 中的邮箱地址、模型名称、飞书 open_id，以及 .env 中的三个密钥，再运行 doctor。")
        return 0
    if not path.exists():
        raise ValueError("找不到配置文件；请先运行 python3 -m mail_agent init")
    config = Config.load(path)
    if args.accounts:
        selected = set(args.accounts.split(","))
        unknown = selected - {a.name for a in config.accounts}
        if unknown:
            raise ValueError("未知账号：" + ",".join(sorted(unknown)))
        for account in config.accounts:
            account.enabled = account.name in selected
    requires_feishu = args.command == "serve" or (args.command == "digest" and args.send)
    requires_model = args.command != "sync"
    errors = config.check(model=requires_model, feishu=requires_feishu or args.command == "doctor")
    if (requires_feishu or args.command == "doctor") and shutil.which(config.feishu.cli) is None:
        errors.append("未找到 lark-cli，请安装或在 feishu.cli 中填写完整路径")
    if args.command == "doctor":
        print("配置文件：", path)
        print("启用邮箱：", "、".join(config.active_accounts))
        print("摘要时间：", config.digest_time, config.timezone)
        print("本地配置检查：", "通过" if not errors else "待补全")
        for error in errors:
            print("- " + error)
        if not args.connect:
            print("此检查不验证真实邮箱、模型 API 或飞书授权。")
            return int(bool(errors))
        # Connecting to IMAP can still be useful before a model is configured.
        mail_errors = config.check(model=False)
        if mail_errors:
            raise ValueError("；".join(mail_errors))
    elif errors:
        raise ValueError("配置未完成：" + "；".join(errors))
    with process_lock(config.database):
        store = Store(config.database)
        try:
            sync = MailSync(config, store)
            agent = MailAgent(config, store, ChatModel(config.model), sync)
            feishu = Feishu(config.feishu, store=store)
            if args.command in {"sync", "doctor"}:
                results = sync.run()
                print(json.dumps(results, ensure_ascii=False, indent=2))
                return int(any(not r["ok"] for r in results))
            if args.command == "digest":
                digest = agent.digest(sync.run(), persist=args.send)
                print(digest["body"])
                if args.send:
                    feishu.deliver(digest["body"], "digest:" + digest["id"])
                    store.commit_digest(digest)
                    print("\n已发送到配置的飞书接收对象。")
                else:
                    print("\n（预览：未发送，未更新摘要发送进度）")
            elif args.command == "ask":
                print(agent.ask(args.question, session=args.session))
                if args.trace:
                    print("\n工具调用：" + json.dumps(agent.last_trace, ensure_ascii=False, indent=2))
            elif args.command == "chat":
                print("输入问题开始查询；/exit 退出。")
                while True:
                    question = input("你 > ").strip()
                    if question in {"/exit", "/quit"}:
                        break
                    if question:
                        try:
                            print("\n" + agent.ask(question) + "\n")
                        except Exception as error:
                            print(f"查询失败：{error}")
            elif args.command == "serve":
                Service(agent, feishu).serve()
        finally:
            store.close()
    return 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return run(parser().parse_args())
    except (KeyboardInterrupt, EOFError):
        print("\n已停止。", file=sys.stderr)
        return 0
    except Exception as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
