from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime

from .agent import MailAgent
from .feishu import Feishu, FeishuError

LOG = logging.getLogger(__name__)
DIGEST_COMMANDS = {"/digest", "汇总", "汇总一下", "现在汇总", "现在汇总一下", "邮件摘要", "每日摘要", "汇总新邮件"}


def daily_due(now, scheduled_time: str, last_date: str | None) -> bool:
    scheduled = datetime.strptime(scheduled_time, "%H:%M").time()
    return now.time() >= scheduled and last_date != now.date().isoformat()


class Service:
    def __init__(self, agent: MailAgent, feishu: Feishu):
        self.agent, self.feishu, self.store = agent, feishu, agent.store
        self.stop = threading.Event()
        self.jobs = queue.Queue(maxsize=200)
        self.inflight = set()
        self.lock = threading.Lock()
        self.next_daily_attempt = 0.0

    def enqueue(self, key, event=None):
        with self.lock:
            if key in self.inflight:
                return
            self.inflight.add(key)
        try:
            self.jobs.put_nowait((key, event))
        except queue.Full:
            with self.lock:
                self.inflight.discard(key)
            LOG.warning("任务队列已满，已保存的消息稍后重试")

    def send_daily(self):
        results = self.agent.sync.run()
        digest = self.agent.digest(results, persist=True)
        if self.stop.is_set():
            return
        self.feishu.deliver(digest["body"], "digest:" + digest["id"])
        self.store.commit_digest(digest)
        pending, _ = self.store.pending(self.agent.config.active_accounts, limit=1)
        if not pending and all(r["ok"] and not r.get("has_more") for r in results):
            self.store.set("last_daily_date", datetime.now(self.agent.zone).date().isoformat())
        LOG.info("摘要已发送；本次 %d 封", len(digest["mail_ids"]))

    def handle_event(self, event: dict):
        message_id = event["message_id"]
        if self.store.handled(message_id):
            return
        saved = self.store.get("event_reply:" + message_id)
        digest = None
        if saved:
            answer, digest = saved["answer"], saved.get("digest")
        else:
            question = event["content"].strip()
            try:
                if question in DIGEST_COMMANDS:
                    digest = self.agent.digest(self.agent.sync.run(), persist=True)
                    answer = digest["body"]
                else:
                    answer = self.agent.ask(question, session=event["chat_id"])
            except Exception as error:
                LOG.warning("查询未完成：%s", type(error).__name__)
                answer = f"本次查询未完成：{error}。请稍后重试。"
            self.store.set("event_reply:" + message_id, {"answer": answer, "digest": digest})
        if self.stop.is_set():
            return
        self.feishu.deliver(answer, "reply:" + message_id, reply_to=message_id)
        if digest:
            self.store.commit_digest(digest)
        self.store.mark_handled(message_id)

    def worker(self):
        while not self.stop.is_set():
            try:
                key, event = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if key == "daily":
                    self.send_daily()
                elif not self.store.handled(event["message_id"]):
                    self.handle_event(event)
            except Exception as error:
                LOG.warning("任务未完成，将重试：%s", type(error).__name__)
            finally:
                if key == "daily":
                    self.next_daily_attempt = time.monotonic() + 300
                with self.lock:
                    self.inflight.discard(key)
                self.jobs.task_done()

    def scheduler(self):
        while not self.stop.is_set():
            now = datetime.now(self.agent.zone)
            if daily_due(now, self.agent.config.digest_time, self.store.get("last_daily_date")) and time.monotonic() >= self.next_daily_attempt:
                self.enqueue("daily")
            for event in self.store.queued_events():
                if self.feishu.accepts(event):
                    self.enqueue(event["message_id"], event)
            self.stop.wait(30)

    def serve(self):
        worker = threading.Thread(target=self.worker, daemon=True)
        scheduler = threading.Thread(target=self.scheduler, daemon=True)
        # Do not start scheduled delivery until the incoming-message channel is
        # verified ready, so startup failure does not leave a half-running app.
        try:
            with self.feishu.listen() as lines:
                LOG.info("飞书消息通道已就绪；每日 %s %s 推送", self.agent.config.digest_time, self.agent.config.timezone)
                worker.start()
                scheduler.start()
                for line in lines:
                    if self.stop.is_set():
                        break
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        LOG.warning("忽略无法解析的飞书事件行")
                        continue
                    # CLI already flattened event.content into plain text.
                    if self.feishu.accepts(event) and not self.store.handled(event["message_id"]):
                        self.store.queue_event(event)
                        self.enqueue(event["message_id"], event)
                if not self.stop.is_set():
                    raise FeishuError("飞书事件流结束；请重新启动服务")
        finally:
            self.stop.set()
            if worker.is_alive():
                worker.join(timeout=5)
            if scheduler.is_alive():
                scheduler.join(timeout=2)
