from __future__ import annotations

import asyncio
import fcntl
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from .domain.models import Task, stamp
from .domain.schemas import ScheduleInput, DigestInput

LOG = logging.getLogger(__name__)


class Worker:
    def __init__(self, workspace):
        self.workspace = workspace
        self.stop = asyncio.Event()
        self.scheduler = None
        self.lock_handle = None

    async def tick(self):
        w = self.workspace
        w.db.set("worker", {"heartbeat_at": stamp(), "status": "running", "mode": w.settings.mode})
        w.feishu.reconcile_notifications()
        config = ScheduleInput(**w.db.get("schedule", {}))
        now = datetime.now(ZoneInfo(config.timezone))
        active_ids = [
            a["id"]
            for a in w.accounts.list()
            if a["enabled"] and (not config.account_ids or a["id"] in config.account_ids)
        ]
        if not active_ids:
            return
        if config.enabled and now.strftime("%H:%M") >= config.digest_time:
            hour, minute = map(int, config.digest_time.split(":"))
            w.digests.enqueue(
                DigestInput(account_ids=active_ids),
                cutoff=now.replace(hour=hour, minute=minute, second=0, microsecond=0),
                extra={"deliver": True},
                key="daily:" + now.date().isoformat(),
            )
        if not config.sync_enabled:
            return
        slot = int(now.timestamp()) // (config.sync_minutes * 60)
        with w.db.session() as session:
            already_running = session.scalar(
                select(Task.id)
                .where(Task.kind == "sync", Task.status.in_(["queued", "running"]))
                .limit(1)
            )
        if not already_running:
            w.tasks.enqueue("sync", {"account_ids": active_ids}, key=f"sync-slot:{slot}")

    async def lane(self, lane):
        while not self.stop.is_set():
            task = self.workspace.tasks.claim(lane)
            if task:
                try:
                    result = await self.workspace.execute(task)
                    self.workspace.tasks.finish(task["id"], result)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOG.warning("Task %s failed: %s", task["id"], type(exc).__name__)
                    self.workspace.tasks.fail(task["id"], exc)
                    try:
                        self.workspace.feishu.reconcile_notifications()
                    except Exception:
                        LOG.warning("Feishu task feedback deferred until the next scheduler tick")
            else:
                try:
                    await asyncio.wait_for(self.stop.wait(), 0.5)
                except TimeoutError:
                    pass

    async def run(self):
        path = self.workspace.settings.data_dir / "worker.lock"
        with path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("已有 Worker 使用这个数据目录") from exc
            self.workspace.tasks.recover()
            self.scheduler = AsyncIOScheduler(timezone="UTC")
            self.scheduler.add_job(self.tick, "interval", seconds=5, max_instances=1, coalesce=True)
            self.scheduler.start()
            await self.tick()
            lanes = [
                asyncio.create_task(self.lane(name))
                for name in ("interactive", "background", "control", "delivery")
            ]
            lanes.append(asyncio.create_task(self.workspace.feishu.supervise(self.stop)))
            try:
                await self.stop.wait()
            finally:
                self.scheduler.shutdown(wait=False)
                for lane in lanes:
                    lane.cancel()
                await asyncio.gather(*lanes, return_exceptions=True)
                self.workspace.db.set(
                    "worker",
                    {
                        "heartbeat_at": stamp(),
                        "status": "stopped",
                        "mode": self.workspace.settings.mode,
                    },
                )
                fcntl.flock(lock, fcntl.LOCK_UN)
