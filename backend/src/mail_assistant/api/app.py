from __future__ import annotations

import asyncio
import hmac
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from .. import __version__
from ..application.workspace import Workspace
from ..config import Settings
from ..domain.errors import AppError
from ..domain.models import (
    Account,
    Conversation,
    Digest,
    Mail,
    Message,
    Session,
    Setting,
    Task,
    TaskEvent,
    stamp,
)
from ..domain.schemas import (
    AccountInput,
    AccountView,
    BootstrapRequest,
    ConversationView,
    DigestView,
    DigestInput,
    FeishuInput,
    LoginRequest,
    MailView,
    MessageView,
    ModelInput,
    ModelView,
    QuestionInput,
    ScheduleInput,
    TaskInput,
    TaskView,
    FeishuBindInput,
    FeishuTargetInput,
    FeishuView,
    DeliveryRetryInput,
    DeliveryView,
)
from ..infrastructure.database import record
from ..infrastructure.security import password_hash, password_matches, token_hash
from ..worker import Worker

COOKIE = "mail_assistant_session"


def create_app(settings=None, *, embedded_worker=False):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        workspace = Workspace(settings)
        app.state.workspace = workspace
        worker = Worker(workspace) if embedded_worker else None
        runner = asyncio.create_task(worker.run()) if worker else None
        try:
            yield
        finally:
            if worker:
                worker.stop.set()
                await runner
            await workspace.close()

    app = FastAPI(title="来信 · 多邮箱智能助手", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def origin_check(request, call_next):
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
            allowed = {request.headers.get("host"), "127.0.0.1:5173", "localhost:5173"}
            if urlparse(origin).netloc not in allowed:
                return JSONResponse(
                    {
                        "detail": {
                            "code": "origin_rejected",
                            "message": "请求来源与当前工作台不一致，请重新打开页面",
                        }
                    },
                    status_code=403,
                )
        return await call_next(request)

    @app.exception_handler(AppError)
    async def application_error(request, error):
        return JSONResponse(
            {"detail": {"code": error.code, "message": error.message}}, status_code=error.status
        )

    def workspace(request: Request):
        return request.app.state.workspace

    def authenticated(request, w):
        token = request.cookies.get(COOKIE)
        if not token:
            return False
        with w.db.session() as session:
            row = session.get(Session, token_hash(token))
            return bool(row and row.expires_at > stamp())

    def owner(request: Request, w=Depends(workspace)):
        if not authenticated(request, w):
            raise AppError("请先登录工作台", "unauthenticated", status=401)
        return w

    def sign_in(response, request, w):
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(days=7)
        with w.db.session() as session:
            session.add(
                Session(
                    token_hash=token_hash(token),
                    expires_at=expires.isoformat(timespec="milliseconds"),
                )
            )
        response.set_cookie(
            COOKIE,
            token,
            max_age=7 * 86400,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )

    @app.get("/api/v1/health")
    def health(w=Depends(workspace)):
        state = w.db.get("worker", {})
        alive = (
            state.get("status") == "running"
            and bool(state.get("heartbeat_at"))
            and datetime.fromisoformat(state["heartbeat_at"])
            > datetime.now(timezone.utc) - timedelta(seconds=20)
        )
        return {
            "status": "ok",
            "version": __version__,
            "mode": settings.mode,
            "worker": {"alive": alive, "heartbeat_at": state.get("heartbeat_at")},
        }

    @app.get("/api/v1/auth/status")
    def auth_status(request: Request, w=Depends(workspace)):
        return {
            "initialized": bool(w.db.get("owner")),
            "authenticated": authenticated(request, w),
            "mode": settings.mode,
        }

    @app.post("/api/v1/auth/bootstrap")
    def bootstrap(
        form: BootstrapRequest, request: Request, response: Response, w=Depends(workspace)
    ):
        if w.db.get("owner"):
            raise AppError("工作台已经初始化，请直接登录", "already_initialized", status=409)
        if not hmac.compare_digest(form.token, w.secrets.bootstrap_token):
            raise AppError(
                "初始化凭证不正确，请查看服务启动说明", "invalid_bootstrap_token", status=403
            )
        try:
            with w.db.session() as session:
                session.add(
                    Setting(key="owner", value={"password_hash": password_hash(form.password)})
                )
        except IntegrityError:
            raise AppError(
                "工作台已经初始化，请直接登录", "already_initialized", status=409
            ) from None
        sign_in(response, request, w)
        return {"ok": True}

    @app.post("/api/v1/auth/login")
    def login(form: LoginRequest, request: Request, response: Response, w=Depends(workspace)):
        current = w.db.get("owner", {})
        if not password_matches(form.password, current.get("password_hash")):
            raise AppError("密码不正确，请重新输入", "invalid_password", status=401)
        sign_in(response, request, w)
        return {"ok": True}

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, response: Response, w=Depends(owner)):
        with w.db.session() as session:
            row = session.get(Session, token_hash(request.cookies.get(COOKIE, "")))
            if row:
                session.delete(row)
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.post("/api/v1/demo/start")
    async def demo_start(request: Request, response: Response, w=Depends(workspace)):
        if settings.mode != "demo":
            raise AppError("请使用独立的演示服务体验样例", "demo_disabled", status=404)
        if not w.db.get("owner"):
            w.db.set("owner", {"password_hash": password_hash(secrets.token_urlsafe(32))})
        await w.seed_demo()
        sign_in(response, request, w)
        return {"ok": True, "mode": "demo"}

    @app.get("/api/v1/onboarding")
    def onboarding(w=Depends(owner)):
        return w.db.get("onboarding", {"step": 1, "complete": False})

    @app.put("/api/v1/onboarding")
    def save_onboarding(value: dict, w=Depends(owner)):
        step = max(1, min(6, int(value.get("step", 1))))
        complete = bool(value.get("complete"))
        if complete and (
            w.model_view()["status"] != "verified"
            or not any(a["status"] == "verified" and a["enabled"] for a in w.accounts.list())
        ):
            raise AppError("请先验证模型和至少一个邮箱", "onboarding_not_ready")
        w.db.set("onboarding", {"step": step, "complete": complete})
        return w.db.get("onboarding")

    @app.get("/api/v1/settings/model", response_model=ModelView)
    def model(w=Depends(owner)):
        return w.model_view()

    @app.put("/api/v1/settings/model", response_model=ModelView)
    def save_model(form: ModelInput, w=Depends(owner)):
        return w.save_model(form)

    @app.post("/api/v1/settings/model/test", response_model=TaskView, status_code=202)
    def test_model(w=Depends(owner)):
        return w.tasks.enqueue("model_test", lane="interactive", max_attempts=1)

    @app.get("/api/v1/accounts", response_model=list[AccountView])
    def accounts(w=Depends(owner)):
        return w.accounts.list()

    @app.post("/api/v1/accounts", response_model=AccountView, status_code=201)
    async def add_account(form: AccountInput, w=Depends(owner)):
        return await w.accounts.save(form)

    @app.put("/api/v1/accounts/{account_id}", response_model=AccountView)
    async def update_account(account_id: str, form: AccountInput, w=Depends(owner)):
        return await w.accounts.save(form, account_id)

    @app.delete("/api/v1/accounts/{account_id}")
    async def remove_account(account_id: str, expected_revision: int, w=Depends(owner)):
        return await w.accounts.remove(account_id, expected_revision)

    @app.post("/api/v1/accounts/{account_id}/disable")
    async def disable_account(account_id: str, w=Depends(owner)):
        await w.accounts.disable(account_id)
        return {"ok": True, "message": "账号已停用，历史邮件仍保留"}

    @app.post("/api/v1/accounts/{account_id}/test", response_model=TaskView, status_code=202)
    def account_test(account_id: str, w=Depends(owner)):
        w.accounts.get(account_id)
        return w.tasks.enqueue(
            "account_test", {"account_id": account_id}, lane="interactive", max_attempts=1
        )

    @app.post("/api/v1/accounts/{account_id}/folders", response_model=TaskView, status_code=202)
    def account_folders(account_id: str, w=Depends(owner)):
        account = w.accounts.get(account_id)
        if not account["enabled"]:
            raise AppError("请先启用邮箱，再读取文件夹", "account_disabled")
        return w.tasks.enqueue(
            "account_folders",
            {"account_id": account_id, "revision": account["revision"]},
            lane="interactive",
            max_attempts=1,
        )

    @app.get("/api/v1/settings/schedule", response_model=ScheduleInput)
    def schedule(w=Depends(owner)):
        return ScheduleInput(**w.db.get("schedule", {}))

    @app.put("/api/v1/settings/schedule", response_model=ScheduleInput)
    def save_schedule(form: ScheduleInput, w=Depends(owner)):
        w.mailboxes.selected(form.account_ids or None)
        if form.enabled and (
            w.model_view()["status"] != "verified"
            or not any(a["status"] == "verified" and a["enabled"] for a in w.accounts.list())
        ):
            raise AppError("启用定时计划前，请验证模型和至少一个邮箱", "schedule_not_ready")
        w.db.set("schedule", form.model_dump())
        return form

    @app.post("/api/v1/sync", response_model=TaskView, status_code=202)
    def sync(form: TaskInput, w=Depends(owner)):
        w.mailboxes.selected(form.account_ids or None)
        return w.tasks.enqueue("sync", form.model_dump())

    @app.get("/api/v1/mails")
    def mails(
        q: str = "",
        account_id: str = "",
        offset: int = 0,
        limit: int = 30,
        cache_state: str = "all",
        w=Depends(owner),
    ):
        return w.mailboxes.search(
            account_ids=[account_id] if account_id else None,
            keywords=q.split() if q else [],
            offset=offset,
            limit=limit,
            cache_state=cache_state,
        )

    @app.get("/api/v1/mails/{mail_id}", response_model=MailView)
    def mail(mail_id: str, w=Depends(owner)):
        with w.db.session() as session:
            row = session.get(Mail, mail_id)
            if not row:
                raise AppError("邮件不存在", "mail_not_found", status=404)
            account = session.get(Account, row.account_id)
            return w.mailboxes.present(row, account.label, account.folder, account.enabled)

    @app.get("/api/v1/mails/{mail_id}/thread")
    def thread(mail_id: str, w=Depends(owner)):
        return w.mailboxes.thread(mail_id, include_inactive=True)

    @app.get("/api/v1/digests", response_model=list[DigestView])
    def digests(w=Depends(owner)):
        return w.digests.list()

    @app.get("/api/v1/digests/{digest_id}", response_model=DigestView)
    def digest(digest_id: str, w=Depends(owner)):
        with w.db.session() as session:
            row = session.get(Digest, digest_id)
            if not row:
                raise AppError("摘要不存在", "digest_not_found", status=404)
            return w.digests.view(row, session)

    @app.post("/api/v1/digests", response_model=TaskView, status_code=202)
    def create_digest(form: DigestInput, w=Depends(owner)):
        return w.digests.enqueue(form)

    @app.get("/api/v1/digests/{digest_id}/sources/{mail_id}", response_model=MailView)
    def digest_source(digest_id: str, mail_id: str, w=Depends(owner)):
        with w.db.session() as session:
            digest = session.get(Digest, digest_id)
            if not digest or not any(i["mail_id"] == mail_id for i in digest.items):
                raise AppError("这封邮件不属于该摘要", "source_not_found", status=404)
            source = next((m for m in digest.sources if m["id"] == mail_id), None)
            if source:
                return source
            mail = session.get(Mail, mail_id)
            if not mail:
                raise AppError("找不到这封邮件", "source_not_found", status=404)
            account = session.get(Account, mail.account_id)
            return w.mailboxes.present(mail, account.label, account.folder, account.enabled)

    @app.get("/api/v1/conversations", response_model=list[ConversationView])
    def conversations(w=Depends(owner)):
        with w.db.session() as session:
            return [
                record(c)
                for c in session.scalars(
                    select(Conversation)
                    .where(Conversation.channel == "web")
                    .order_by(Conversation.created_at.desc())
                )
            ]

    @app.get("/api/v1/conversations/{conversation_id}/messages", response_model=list[MessageView])
    def messages(conversation_id: str, w=Depends(owner)):
        with w.db.session() as session:
            return [
                record(m)
                for m in session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at)
                )
            ]

    @app.post("/api/v1/questions", response_model=TaskView, status_code=202)
    def ask(form: QuestionInput, w=Depends(owner)):
        return w.questions.submit(form)

    @app.get("/api/v1/tasks", response_model=list[TaskView])
    def tasks(w=Depends(owner)):
        with w.db.session() as session:
            return [
                record(t)
                for t in session.scalars(select(Task).order_by(Task.created_at.desc()).limit(100))
            ]

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskView)
    def task(task_id: str, w=Depends(owner)):
        with w.db.session() as session:
            row = session.get(Task, task_id)
            if not row:
                raise AppError("任务不存在", "task_not_found", status=404)
            return record(row)

    @app.post("/api/v1/tasks/{task_id}/retry", response_model=TaskView)
    def retry(task_id: str, w=Depends(owner)):
        result = w.tasks.retry(task_id)
        if not result:
            raise AppError("只有失败的任务可以重试", "task_not_retryable")
        return result

    @app.get("/api/v1/tasks/{task_id}/events")
    async def events(task_id: str, request: Request, after: int = 0, w=Depends(owner)):
        task(task_id, w)

        async def stream():
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
            while not await request.is_disconnected():
                with w.db.session() as session:
                    rows = [
                        record(e)
                        for e in session.scalars(
                            select(TaskEvent)
                            .where(TaskEvent.task_id == task_id, TaskEvent.id > cursor)
                            .order_by(TaskEvent.id)
                            .limit(100)
                        )
                    ]
                    status = session.get(Task, task_id).status
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\nevent: {row['kind']}\ndata: {json.dumps(row['data'], ensure_ascii=False)}\n\n"
                if status in {"succeeded", "failed", "cancelled"} and len(rows) < 100:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/overview")
    def overview(w=Depends(owner)):
        with w.db.session() as session:
            mail_count = session.scalar(select(func.count()).select_from(Mail))
            recent_tasks = [
                record(t)
                for t in session.scalars(select(Task).order_by(Task.created_at.desc()).limit(5))
            ]
        return {
            "accounts": w.accounts.list(),
            "mail_count": mail_count,
            "latest_digest": w.digests.latest(),
            "tasks": recent_tasks,
            "schedule": ScheduleInput(**w.db.get("schedule", {})).model_dump(),
            "worker": health(w)["worker"],
            "model": w.model_view(),
            "onboarding": w.db.get("onboarding", {"step": 1, "complete": False}),
            "mode": settings.mode,
        }

    @app.get("/api/v1/settings/feishu", response_model=FeishuView)
    def feishu(w=Depends(owner)):
        return w.feishu.view()

    @app.put("/api/v1/settings/feishu", response_model=FeishuView)
    def save_feishu(form: FeishuInput, w=Depends(owner)):
        return w.feishu.set_enabled(form.enabled, form.binding_id)

    @app.post("/api/v1/settings/feishu/bind", response_model=TaskView, status_code=202)
    def bind_feishu(form: FeishuBindInput, w=Depends(owner)):
        return w.feishu.request_binding(form)

    @app.post("/api/v1/settings/feishu/disconnect", response_model=FeishuView)
    def disconnect_feishu(w=Depends(owner)):
        return w.feishu.disconnect()

    @app.post("/api/v1/settings/feishu/pair", response_model=FeishuView)
    def pair_feishu(w=Depends(owner)):
        return w.feishu.pair()

    @app.post("/api/v1/settings/feishu/reconnect", response_model=FeishuView)
    def reconnect_feishu(w=Depends(owner)):
        return w.feishu.reconnect()

    @app.post("/api/v1/settings/feishu/test", response_model=TaskView, status_code=202)
    def test_feishu(form: FeishuTargetInput, w=Depends(owner)):
        return w.feishu.test_send(form.binding_id)

    @app.get("/api/v1/settings/feishu/qr/{identity}")
    def feishu_qr(identity: str, w=Depends(owner)):
        if identity not in {item["qr_id"] for item in w.feishu.state().get("urls", [])}:
            raise AppError("二维码已过期，请刷新绑定状态", "feishu_qr_expired", status=404)
        # Only the current state's SHA-256 identifiers can resolve a file.
        if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
            raise HTTPException(404)
        path = w.settings.feishu_dir / "qr" / (identity + ".png")
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/deliveries", response_model=list[DeliveryView])
    def deliveries(w=Depends(owner)):
        return w.feishu.deliveries()

    @app.post("/api/v1/deliveries/{delivery_id}/retry", response_model=TaskView, status_code=202)
    def retry_delivery(delivery_id: str, form: DeliveryRetryInput, w=Depends(owner)):
        return w.feishu.retry_delivery(delivery_id, form.allow_uncertain)

    @app.post("/api/v1/digests/{digest_id}/send", response_model=TaskView, status_code=202)
    def send_digest(digest_id: str, form: FeishuTargetInput, w=Depends(owner)):
        return w.feishu.queue_digest(digest_id, binding_id=form.binding_id)

    dist = settings.web_dist or Path(__file__).resolve().parents[4] / "apps" / "web" / "dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (dist / path).resolve()
        if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse(
            {"message": "前端尚未构建；开发时运行 apps/web 的 pnpm dev", "api": "/docs"}
        )

    return app
