from __future__ import annotations

import asyncio
from sqlalchemy import select, update
from ..domain.errors import AppError
from ..domain.models import Account, Mail, Coverage, Task, TaskEvent, Setting, new_id, stamp
from ..domain.schemas import AccountInput, AccountView
from ..infrastructure.database import record


class Accounts:
    def __init__(self, db, manager, mode="live"):
        self.db, self.manager, self.mode = db, manager, mode
        self.locks = {}

    def view(self, account, coverage=None):
        data = record(account)
        if coverage and coverage.cursor.get("folder") == account.folder:
            data["sync_method"] = coverage.cursor.get("sync_method")
            data["sync_diagnostics"] = coverage.cursor.get("diagnostics", [])
        return AccountView.model_validate(data).model_dump()

    def list(self, *, include_removed=False):
        with self.db.session() as session:
            statement = select(Account).order_by(Account.created_at)
            if not include_removed:
                statement = statement.where(Account.removed_at.is_(None))
            return [self.view(a, session.get(Coverage, a.id)) for a in session.scalars(statement)]

    def get(self, account_id, *, include_removed=False):
        with self.db.session() as session:
            account = session.get(Account, account_id)
            if not account or (account.removed_at and not include_removed):
                raise AppError("找不到这个邮箱账号", "account_not_found", status=404)
            return record(account)

    async def save(self, form: AccountInput, account_id=None):
        async with self.locks.setdefault(account_id or "create", asyncio.Lock()):
            return await self._save(form, account_id)

    async def _save(self, form: AccountInput, account_id=None):
        data = form.model_dump(exclude={"password", "expected_revision"})
        data["host"] = data["host"] or {"qq": "imap.qq.com", "163": "imap.163.com"}.get(
            data["provider"], ""
        )
        if not data["host"]:
            raise AppError("请填写 IMAP 服务器地址", "invalid_host")
        if account_id:
            old = self.get(account_id)
            if old["status"] == "removing":
                raise AppError("连接正在移除，请先重试完成移除", "account_removal_pending")
            if form.expected_revision != old["revision"]:
                raise AppError("配置已更新，请刷新页面后保存", "revision_conflict", status=409)
            # Changing the actual mailbox must get a fresh identity and cache.
            if data["email"] != old["email"] or data["host"] != old["host"]:
                raise AppError(
                    "更换邮箱地址或服务器时请添加新账号，以保留来源与同步记录",
                    "account_identity_change",
                )
            if self.mode == "live":
                shown = await self.manager.update(old | data, form.password)
                self.db.set("mailbox_config_generation", {"id": new_id()})
            changed_connection = bool(form.password) or any(
                data[k] != old[k] for k in ("port", "folder")
            )
            with self.db.session() as session:
                row = session.get(Account, account_id)
                for key, value in data.items():
                    setattr(row, key, value)
                row.revision += 1
                row.secret_set = row.secret_set or bool(form.password)
                if self.mode == "live":
                    row.upstream_revision = shown["revision"]
                if changed_connection:
                    row.status, row.error, row.sync_complete = "unverified", None, False
                if data["folder"] != old["folder"]:
                    row.last_sync_at = None
                    session.execute(
                        update(Mail)
                        .where(Mail.account_id == account_id)
                        .values(observed_state="historical")
                    )
                    if coverage := session.get(Coverage, account_id):
                        session.delete(coverage)
                return AccountView.model_validate(record(row)).model_dump()
        if not form.password and self.mode == "live":
            raise AppError("请填写邮箱客户端授权码", "missing_credential")
        new = {"id": new_id(), **data}
        new["upstream_name"] = "acct_" + new["id"]
        if self.mode == "live":
            await self.manager.add(new, form.password)
            shown = await self.manager.set_enabled(new["upstream_name"], data["enabled"])
            self.db.set("mailbox_config_generation", {"id": new_id()})
        with self.db.session() as session:
            row = Account(**new, secret_set=bool(form.password) or self.mode == "demo")
            if self.mode == "live":
                row.upstream_revision = shown["revision"]
            session.add(row)
            session.flush()
            return AccountView.model_validate(record(row)).model_dump()

    async def disable(self, account_id):
        async with self.locks.setdefault(account_id, asyncio.Lock()):
            return await self._disable(account_id)

    async def _disable(self, account_id):
        account = self.get(account_id)
        if self.mode == "live":
            shown = await self.manager.set_enabled(account["upstream_name"], False)
            self.db.set("mailbox_config_generation", {"id": new_id()})
        # The upstream name remains stable so re-enabling can reuse it.
        with self.db.session() as session:
            row = session.get(Account, account_id)
            row.enabled = False
            row.revision += 1
            if self.mode == "live":
                row.upstream_revision = shown["revision"]
        return self.get(account_id)

    async def test(self, account_id):
        account = self.get(account_id)
        if account["status"] == "removing":
            raise AppError("连接正在移除，请先重试完成移除", "account_removal_pending")
        try:
            if self.mode == "live":
                await self.manager.test(account["upstream_name"])
            with self.db.session() as session:
                row = session.get(Account, account_id)
                if row.revision != account["revision"]:
                    raise AppError("测试期间配置已更改，请重新测试", "revision_conflict")
                row.status, row.error = "verified", None
            return {
                "account_id": account_id,
                "status": "verified",
                "simulated": self.mode == "demo",
            }
        except AppError as exc:
            with self.db.session() as session:
                row = session.get(Account, account_id)
                if row.revision == account["revision"]:
                    row.status, row.error = "error", exc.message
            raise

    async def remove(self, account_id, expected_revision):
        async with self.locks.setdefault(account_id, asyncio.Lock()):
            account = self.get(account_id, include_removed=True)
            if account["removed_at"]:
                return self.db.get("account_removal:" + account_id, {"removed": True})
            if account["revision"] != expected_revision:
                raise AppError("配置已更新，请刷新后重新确认移除", "revision_conflict", status=409)
            # Persist intent before calling the external catalog. A restart or
            # unknown CLI outcome leaves a disabled, visible, retryable record.
            with self.db.session() as session:
                row = session.get(Account, account_id)
                row.enabled, row.status, row.error = False, "removing", None
                row.revision += 1
                session.execute(
                    update(Mail)
                    .where(Mail.account_id == account_id)
                    .values(observed_state="historical")
                )
                for task in session.scalars(select(Task).where(Task.status == "queued")):
                    if task.payload.get("account_id") == account_id or account_id in (
                        task.payload.get("account_ids") or []
                    ):
                        task.status, task.phase = "cancelled", "来源连接已移除，任务已取消"
                        task.error, task.finished_at = "请重新选择可用邮箱后提交", stamp()
                        session.add(
                            TaskEvent(
                                task_id=task.id,
                                kind="done",
                                data={"status": "cancelled", "phase": task.phase},
                            )
                        )
                generation = session.get(Setting, "mailbox_config_generation")
                if generation:
                    generation.value = {"id": new_id()}
                else:
                    session.add(Setting(key="mailbox_config_generation", value={"id": new_id()}))
            try:
                outcome = (
                    await self.manager.remove(account["upstream_name"])
                    if self.mode == "live"
                    else {"cleanup_required": 0}
                )
            except AppError as exc:
                with self.db.session() as session:
                    session.get(Account, account_id).error = "连接已停止使用，移除尚未确认，请重试"
                raise AppError(
                    "连接已停止使用，移除尚未确认，请重试",
                    "account_removal_pending",
                    retryable=True,
                ) from exc
            warning = (
                "邮箱凭据清理尚未确认，请在连接组件中检查"
                if outcome.get("cleanup_required") != 0
                else None
            )
            report = {"removed": True, "account_id": account_id, "warning": warning}
            with self.db.session() as session:
                row = session.get(Account, account_id)
                row.removed_at, row.status, row.secret_set = stamp(), "removed", False
                row.error, row.sync_complete = warning, False
                session.add(Setting(key="account_removal:" + account_id, value=report))
                session.get(Setting, "mailbox_config_generation").value = {"id": new_id()}
            return report
