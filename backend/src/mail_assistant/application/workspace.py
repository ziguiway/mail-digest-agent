from __future__ import annotations

from threading import RLock
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from ..config import Settings
from ..domain.errors import AppError
from ..domain.models import Account, Message
from ..domain.schemas import ModelView, ReplyDraftWriting
from ..domain.query_answers import present_query_answer, validate_query_answer
from ..infrastructure.database import Database, record
from ..infrastructure.security import Secrets
from ..infrastructure.mail_provider import McpMailProvider, McpManager
from ..infrastructure.demo import DemoProvider, DemoIntelligence
from ..infrastructure.maintenance import WorkspaceLease
from ..agents.intelligence import AgentlyIntelligence
from .accounts import Accounts
from .mailboxes import Mailboxes
from .digests import Digests
from .tasks import Tasks
from .questions import Questions
from .feishu import Feishu


class QueryTools:
    def __init__(self, workspace, account_ids, task_id):
        self.workspace, self.account_ids, self.task_id = workspace, account_ids, task_id
        self.evidence, self.calls = {}, []
        self.searches = []

    async def stage(self, name, title):
        self.calls.append(name)
        self.workspace.tasks.event(self.task_id, "stage", {"phase": title})

    def coverage(self):
        return [
            {k: a[k] for k in ("id", "label", "last_sync_at", "sync_complete", "error")}
            for a in self.workspace.mailboxes.selected(self.account_ids)
        ]

    async def search_mails(
        self,
        keywords: list[str],
        sender: str = "",
        since: str = "",
        before: str = "",
        account_id: str = "",
        limit: int = 20,
        offset: int = 0,
        cache_state: str = "all",
    ) -> dict:
        """搜索邮件缓存；关键词为短词列表，日期为本地日期或 ISO 时间，结果可分页。"""
        await self.stage("search_mails", "正在检索相关邮件")
        if account_id and account_id not in {
            a["id"] for a in self.workspace.mailboxes.selected(self.account_ids)
        }:
            return {"error": "邮箱不在本次查询范围内"}
        result = self.workspace.mailboxes.search(
            account_ids=[account_id] if account_id else self.account_ids,
            keywords=keywords,
            sender=sender,
            since=since,
            before=before,
            limit=limit,
            offset=offset,
            cache_state=cache_state,
        )
        result["mails"] = [
            m
            | {
                "body": m["body"][:220],
                "body_truncated": len(m["body"]) > 220 or m["body_truncated"],
            }
            for m in result["mails"]
        ]
        self.searches.append(
            {
                "keywords": keywords,
                "sender": sender,
                "since": since,
                "before": before,
                "account_id": account_id,
                "limit": limit,
                "offset": offset,
                "cache_state": cache_state,
                **{key: value for key, value in result.items() if key != "mails"},
            }
        )
        return result

    async def read_mails(self, mail_ids: list[str]) -> dict:
        """读取本轮检索到的邮件正文，每次最多十封，生成结论前应阅读。"""
        await self.stage("read_mails", "正在阅读邮件正文")
        if not 1 <= len(mail_ids) <= 10:
            return {"error": "每次读取一至十封邮件"}
        mails = self.workspace.mailboxes.read(mail_ids, self.account_ids)
        visible = [
            m
            | {
                "body": m["body"][:12000],
                "body_truncated": m["body_truncated"] or len(m["body"]) > 12000,
            }
            for m in mails
        ]
        self.evidence.update({m["id"]: m for m in visible})
        return {"mails": visible}

    async def get_thread(self, mail_id: str) -> dict:
        """通过引用头查找相关往来；同标题候选不证明是同一会话，须核对正文。"""
        await self.stage("get_thread", "正在核对前后往来")
        result = self.workspace.mailboxes.thread(mail_id, self.account_ids)
        for key in ("linked", "subject_candidates"):
            result[key] = [
                m
                | {
                    "body": m["body"][:4000],
                    "body_truncated": m["body_truncated"] or len(m["body"]) > 4000,
                }
                for m in result[key]
            ]
            self.evidence.update({m["id"]: m for m in result[key]})
        return result


class Workspace:
    def __init__(self, settings: Settings, *, maintenance_lease=None):
        self.owns_lease = maintenance_lease is None
        if maintenance_lease is not None and (
            not maintenance_lease.exclusive
            or maintenance_lease.root != settings.data_dir
            or not maintenance_lease.handles
        ):
            raise AppError("工作空间维护锁无效", "invalid_maintenance_lease")
        self.lease = maintenance_lease or WorkspaceLease(settings.data_dir)
        try:
            self._initialize(settings)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            if self.owns_lease:
                self.lease.close()
            raise

    def _initialize(self, settings):
        self.settings = settings
        self.model_lock = RLock()
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = Database(settings.database)
        self.db.migrate()
        self.secrets = Secrets(settings.data_dir)
        self.tasks = Tasks(self.db)
        self.manager = McpManager(settings)
        self.accounts = Accounts(self.db, self.manager, settings.mode)
        self.provider = (
            DemoProvider()
            if settings.mode == "demo"
            else McpMailProvider(settings, lambda: self.db.get("mailbox_config_generation"))
        )
        self.mailboxes = Mailboxes(self.db, self.provider, self.accounts)
        self.questions = Questions(self.db, self.tasks, self.mailboxes)
        self.feishu = Feishu(self)
        self.intelligence = (
            DemoIntelligence()
            if settings.mode == "demo"
            else AgentlyIntelligence(self.db, self.secrets)
        )
        self.digests = Digests(self)

    def model_view(self):
        model = self.db.get("model", {})
        return ModelView.model_validate(
            model | {"key_set": bool(model.get("encrypted_key")) or self.settings.mode == "demo"}
        ).model_dump()

    def save_model(self, form):
        with self.model_lock:
            return self._save_model(form)

    def _save_model(self, form):
        old = self.db.get("model", {})
        key = self.secrets.seal(form.api_key) if form.api_key else old.get("encrypted_key")
        if not key and self.settings.mode != "demo":
            raise AppError("请填写模型 API Key", "missing_model_key")
        revision = old.get("revision", 0)
        saved = self.db.compare_set(
            "model",
            revision,
            {
                "base_url": form.base_url,
                "name": form.name,
                "encrypted_key": key,
                "status": "unverified",
                "capabilities": {},
                "error": None,
                "revision": revision + 1,
            },
        )
        if not saved:
            raise AppError("模型配置已更新，请刷新后重新保存", "revision_conflict", status=409)
        return self.model_view()

    async def seed_demo(self):
        if self.settings.mode != "demo":
            raise AppError("当前服务不处于样例模式", "demo_disabled")
        with self.db.session() as session:
            if session.scalar(select(Account.id).limit(1)) is None:
                session.add_all(
                    [
                        Account(
                            id="demo_qq",
                            upstream_name="demo_qq",
                            label="学习与课程",
                            email="research.demo@qq.com",
                            provider="qq",
                            host="imap.qq.com",
                            secret_set=True,
                            status="verified",
                        ),
                        Account(
                            id="demo_163",
                            upstream_name="demo_163",
                            label="课题组与实验室",
                            email="research.demo@163.com",
                            provider="163",
                            host="imap.163.com",
                            secret_set=True,
                            status="verified",
                        ),
                    ]
                )
        self.db.set(
            "model",
            {
                "base_url": "",
                "name": "样例规则模拟",
                "status": "verified",
                "capabilities": {
                    "structured_output": True,
                    "tool_calling": True,
                    "simulated": True,
                },
            },
        )
        self.db.set("onboarding", {"step": 6, "complete": True})
        await self.mailboxes.sync()
        if not self.db.get("demo_digest_seeded"):
            self.tasks.enqueue("digest", {}, key="demo:first-digest")
            self.db.set("demo_digest_seeded", True)
        return {"mode": "demo"}

    async def progress(self, task_id, title):
        self.tasks.event(task_id, "stage", {"phase": title})

    async def generate_digest(self, task):
        return await self.digests.generate(task)

    def reply_source(self, mail_id, digest_id=None):
        if digest_id:
            return self.digests.source(digest_id, mail_id)
        return self.mailboxes.cached_mail(mail_id)

    async def draft_reply(self, task):
        payload = task["payload"]
        source = self.reply_source(payload["mail_id"], payload.get("digest_id"))
        await self.progress(task["id"], "正在核对原邮件与相关往来")
        warnings = []
        try:
            thread = self.mailboxes.thread(
                source["id"], [source["account_id"]], include_inactive=True
            )
        except AppError as exc:
            if exc.code != "mail_not_found":
                raise
            thread = {"linked": []}
            warnings.append("相关往来已不可用，本次依据摘要保存的原邮件起草。")
        related = [mail for mail in thread["linked"] if mail["id"] != source["id"]]
        if len(related) > 6:
            warnings.append("相关往来较多，本次参考最近的关联邮件。")
        related = related[-6:]

        def context(mail, limit):
            body = mail["body"]
            return {
                **{key: mail.get(key) for key in ("subject", "sender", "sent_at", "received_at")},
                "body": body[:limit],
                "body_truncated": mail["body_truncated"] or len(body) > limit,
            }

        original = context(source, 12000)
        linked = [context(mail, 4000) for mail in related]
        if any(mail["body_truncated"] for mail in [original, *linked]):
            warnings.append("部分邮件仅有正文片段，请核对原文中的细节。")
        if source.get("observed_state", "unknown") != "current":
            warnings.append("原邮件来自历史或尚未确认的缓存，请核对是否已有更新。")
        await self.progress(task["id"], "正在起草回复")
        writing = ReplyDraftWriting.model_validate(
            await self.intelligence.draft_reply(payload["intent"], original, linked)
        )
        subject = " ".join(source["subject"].split()) or "（无主题）"
        if not subject.lower().startswith(("re:", "re：", "回复:", "回复：", "答复:", "答复：")):
            subject = "Re: " + subject
        return {
            "subject": subject,
            "body": writing.body,
            "clarification": writing.clarification,
            "related_count": len(related),
            "warnings": warnings,
        }

    async def answer(self, task):
        conversation_id = task["payload"]["conversation_id"]
        with self.db.session() as session:
            existing = session.scalar(
                select(Message).where(Message.task_id == task["id"], Message.role == "assistant")
            )
            if existing:
                return {"message_id": existing.id, "conversation_id": conversation_id}
            history = [
                record(m)
                for m in session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.desc())
                    .limit(12)
                )
            ][::-1]
        ids = task["payload"].get("account_ids") or None
        stale = [
            a["id"]
            for a in self.mailboxes.selected(ids)
            if not a["last_sync_at"]
            or datetime.fromisoformat(a["last_sync_at"])
            < datetime.now(timezone.utc) - timedelta(minutes=5)
        ]
        if stale:
            await self.mailboxes.sync(stale, lambda title: self.progress(task["id"], title))
        tools = QueryTools(self, ids, task["id"])
        answer = await self.intelligence.answer(
            task["payload"]["question"],
            history,
            tools,
            lambda title: self.progress(task["id"], title),
        )
        answer = validate_query_answer(answer, tools.evidence)
        sources = [tools.evidence[i] for i in answer["mail_ids"] if i in tools.evidence]
        coverage = tools.coverage()
        warning = [
            f"{a['label']}：{a['error'] or '缓存覆盖尚未确认'}"
            for a in coverage
            if not a["sync_complete"]
        ]
        if any(m["observed_state"] != "current" for m in sources):
            warning.append("本次依据包含历史缓存或尚未确认的邮件，不能证明它们仍在当前同步文件夹中")
        content, presentation = present_query_answer(answer, tools.evidence, warning)
        with self.db.session() as session:
            row = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=content,
                presentation=presentation,
                sources=[
                    {
                        k: m[k]
                        for k in (
                            "id",
                            "subject",
                            "sender",
                            "account_label",
                            "sent_at",
                            "received_at",
                            "body_truncated",
                            "folder",
                            "observed_state",
                            "last_observed_at",
                        )
                    }
                    for m in sources
                ],
                task_id=task["id"],
            )
            session.add(row)
            session.flush()
            return {"message_id": row.id, "conversation_id": conversation_id}

    async def execute(self, task):
        kind = task["kind"]
        if kind == "account_test":
            return await self.accounts.test(task["payload"]["account_id"])
        if kind == "account_folders":
            await self.progress(task["id"], "正在读取邮箱文件夹")
            return await self.mailboxes.folders(
                task["payload"]["account_id"], task["payload"]["revision"]
            )
        if kind == "model_test":
            tested = self.db.get("model", {})
            try:
                capabilities = await self.intelligence.probe()
                with self.model_lock:
                    profile = self.db.get("model", {})
                    if profile.get("revision", 0) != tested.get("revision", 0):
                        raise AppError(
                            "测试期间模型配置已更改，请重新测试", "revision_conflict", status=409
                        )
                    saved = self.db.compare_set(
                        "model",
                        tested.get("revision", 0),
                        profile
                        | {"status": "verified", "capabilities": capabilities, "error": None},
                    )
                    if not saved:
                        raise AppError(
                            "测试期间模型配置已更改，请重新测试", "revision_conflict", status=409
                        )
                return capabilities
            except AppError as exc:
                with self.model_lock:
                    profile = self.db.get("model", {})
                    if profile.get("revision", 0) == tested.get("revision", 0):
                        self.db.compare_set(
                            "model",
                            tested.get("revision", 0),
                            profile | {"status": "error", "error": exc.message},
                        )
                raise
        if kind == "sync":
            return {
                "accounts": await self.mailboxes.sync(
                    task["payload"].get("account_ids"),
                    lambda title: self.progress(task["id"], title),
                )
            }
        if kind == "digest":
            result = await self.generate_digest(task)
            context = task["payload"].get("feishu", {})
            if context.get("notify") and (result.get("empty") or result.get("partial")):
                text = (
                    "本次同步范围尚未确认，暂时无法给出完整摘要；请检查邮箱连接后重试。"
                    if result.get("partial")
                    else "已检查本次汇总范围，没有符合条件的邮件。"
                )
                self.feishu.queue_delivery(
                    "digest_status",
                    task["id"],
                    text,
                    reply_to=context["message_id"],
                    binding_id=context["binding_id"],
                )
            if (
                task["payload"].get("deliver")
                and result.get("digest_id")
                and not result.get("empty")
                and not result.get("partial")
                and self.feishu.state().get("enabled")
            ):
                context = task["payload"].get("feishu", {})
                self.feishu.queue_digest(
                    result["digest_id"],
                    reply_to=context.get("message_id", ""),
                    binding_id=context.get("binding_id"),
                    range_start=result.get("range_start"),
                    range_end=result.get("range_end"),
                )
            return result
        if kind == "query":
            result = await self.answer(task)
            if context := task["payload"].get("feishu"):
                self.feishu.queue_answer(result["message_id"], context)
            return result
        if kind == "reply_draft":
            return await self.draft_reply(task)
        if kind == "feishu_authorize":
            return await self.feishu.authorize(task)
        if kind == "feishu_send":
            return await self.feishu.send(task)
        raise AppError("暂不支持这个任务类型", "unknown_task")

    async def close(self):
        try:
            await self.feishu.cli.close()
        finally:
            try:
                await self.provider.close()
            finally:
                self.db.close()
                if self.owns_lease:
                    self.lease.close()
