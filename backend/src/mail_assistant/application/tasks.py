from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from ..domain.models import Task, TaskEvent, stamp
from ..infrastructure.database import record


class Tasks:
    def __init__(self, db):
        self.db = db

    def enqueue(self, kind, payload=None, *, lane="background", key=None, max_attempts=3):
        try:
            with self.db.session() as session:
                return self.enqueue_in(
                    session, kind, payload, lane=lane, key=key, max_attempts=max_attempts
                )
        except IntegrityError:
            if not key:
                raise
            with self.db.session() as session:
                existing = session.scalar(select(Task).where(Task.idempotency_key == key))
                if not existing:
                    raise
                return record(existing)

    def enqueue_in(
        self, session, kind, payload=None, *, lane="background", key=None, max_attempts=3
    ):
        """Allow messages and their durable task to commit in one transaction."""
        if key:
            existing = session.scalar(select(Task).where(Task.idempotency_key == key))
            if existing:
                return record(existing)
        task = Task(
            kind=kind,
            payload=payload or {},
            lane=lane,
            idempotency_key=key,
            max_attempts=max_attempts,
        )
        session.add(task)
        session.flush()
        session.add(TaskEvent(task_id=task.id, kind="stage", data={"phase": "等待执行"}))
        return record(task)

    def claim(self, lane):
        now = stamp()
        with self.db.session() as session:
            candidate = session.scalar(
                select(Task.id)
                .where(Task.lane == lane, Task.status == "queued", Task.available_at <= now)
                .order_by(Task.created_at)
                .limit(1)
            )
            if candidate is None:
                return None
            result = session.execute(
                update(Task)
                .where(Task.id == candidate, Task.status == "queued")
                .values(
                    status="running", started_at=now, phase="开始执行", attempts=Task.attempts + 1
                )
            )
            if result.rowcount != 1:
                return None
            session.add(TaskEvent(task_id=candidate, kind="stage", data={"phase": "开始执行"}))
            return record(session.get(Task, candidate))

    def event(self, task_id, kind, data):
        with self.db.session() as session:
            if kind == "stage":
                task = session.get(Task, task_id)
                task.phase = data["phase"]
            session.add(TaskEvent(task_id=task_id, kind=kind, data=data))

    def finish(self, task_id, result):
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task.status == "cancelled":
                return
            task.status, task.phase, task.result = "succeeded", "执行完成", result
            task.finished_at, task.error, task.error_code = stamp(), None, None
            session.add(
                TaskEvent(
                    task_id=task_id, kind="result", data={"status": "succeeded", "result": result}
                )
            )

    def fail(self, task_id, error):
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task.status == "cancelled":
                return
            task.error = getattr(error, "message", "执行未完成，请检查连接设置后重试")
            task.error_code = getattr(error, "code", "internal_error")
            retry = getattr(error, "retryable", False) and task.attempts < task.max_attempts
            if retry:
                task.status, task.phase = "queued", "稍后重试"
                task.available_at = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=(10, 30, 120)[min(task.attempts - 1, 2)])
                ).isoformat(timespec="milliseconds")
            else:
                task.status, task.phase, task.finished_at = "failed", "执行失败", stamp()
            session.add(
                TaskEvent(
                    task_id=task_id,
                    kind="error",
                    data={"message": task.error, "code": task.error_code, "will_retry": retry},
                )
            )

    def retry(self, task_id):
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if not task or task.status != "failed":
                return None
            task.status, task.phase, task.attempts, task.available_at = (
                "queued",
                "等待重试",
                0,
                stamp(),
            )
            task.error, task.error_code, task.finished_at = None, None, None
            session.add(TaskEvent(task_id=task_id, kind="stage", data={"phase": "等待重试"}))
            return record(task)

    def recover(self):
        with self.db.session() as session:
            for task in session.scalars(select(Task).where(Task.status == "running")):
                task.status, task.phase, task.available_at = "queued", "服务恢复，等待继续", stamp()
                session.add(TaskEvent(task_id=task.id, kind="stage", data={"phase": task.phase}))
