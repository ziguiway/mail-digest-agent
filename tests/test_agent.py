from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from mail_agent.agent import MailAgent
from mail_agent.config import Account, Config, FeishuConfig, ModelConfig
from mail_agent.demo import DemoModel, DemoSync, seed
from mail_agent.feishu import Feishu, FeishuError, split_text
from mail_agent.mailbox import MailSync, UTC, parse_mail, utc_iso
from mail_agent.model import ChatModel, ModelError
from mail_agent.service import Service, daily_due
from mail_agent.store import Store

NOW = datetime(2026, 11, 3, 7, 30, tzinfo=UTC)


def message_bytes(subject="会议通知", body="请准备研究进展。", msgid="<one@example.edu>"):
    message = EmailMessage()
    message["Subject"], message["From"] = subject, "导师 <advisor@example.edu>"
    message["To"], message["Message-ID"] = "student@example.edu", msgid
    message["Date"] = "Tue, 03 Nov 2026 15:00:00 +0800"
    message.set_content(body)
    return message.as_bytes()


class FakeIMAP:
    capabilities = (b"IMAP4rev1", b"ID")

    def __init__(self, uids=(1, 2), validity="100", fail_uid=None, received=None):
        self.uids, self.validity, self.fail_uid = uids, validity, fail_uid
        self.calls = []
        self.received = received or {}

    def login(self, username, password):
        self.calls.append(("login", username))

    def _simple_command(self, command, *args):
        self.calls.append((command, *args))
        return "OK", [b"ok"]

    def select(self, folder, readonly=False):
        self.calls.append(("select", readonly))
        return "OK", [b"2"]

    def response(self, name):
        return name, [self.validity.encode()]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            # Include old UIDs to exercise the client's own range filtering.
            return "OK", [b" ".join(str(i).encode() for i in self.uids)]
        uid = int(args[0])
        if uid == self.fail_uid:
            return "NO", [b"failure"]
        date = self.received.get(uid, "03-Nov-2026 07:00:00 +0000")
        header = f'{uid} (UID {uid} INTERNALDATE "{date}" BODY[] {{123}}'.encode()
        return "OK", [(header, message_bytes(msgid=f"<mail-{uid}@example.edu>")), b")"]

    def logout(self):
        self.calls.append(("logout",))


class RecordingFeishu:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def deliver(self, text, key, **kwargs):
        self.calls.append((text, key, kwargs))
        if self.fail:
            raise FeishuError("模拟断线")


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "test.sqlite3")
        self.config = Config(root=self.root, database=self.root / "test.sqlite3", accounts=[Account("qq", "qq", "student@qq.com", "TEST_MAIL_CODE"), Account("163", "163", "student@163.com", "TEST_163_CODE")])
        self.agent = MailAgent(self.config, self.store, DemoModel(), DemoSync())

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()


class MailTests(BaseTest):
    def test_mime_html_headers_and_attachments(self):
        message = EmailMessage()
        message["Subject"], message["From"] = "中文通知", "导师 <advisor@example.edu>"
        message.set_content("<style>hidden</style><p>请提交报告</p>", subtype="html")
        message.add_attachment(b"attachment text must not be a body", maintype="application", subtype="octet-stream", filename="report.txt")
        mail = parse_mail(message.as_bytes(), self.config.accounts[0], "1", 1, NOW)
        self.assertEqual(mail["subject"], "中文通知")
        self.assertIn("请提交报告", mail["body"])
        self.assertNotIn("hidden", mail["body"])
        self.assertNotIn("attachment text", mail["body"])

    @patch.dict(os.environ, {"TEST_MAIL_CODE": "fake"})
    def test_uid_incremental_readonly_and_no_seen_flag(self):
        connection = FakeIMAP()
        sync = MailSync(self.config, self.store, connection_factory=lambda *a, **k: connection)
        first = sync.sync_account(self.config.accounts[0], NOW)
        second = sync.sync_account(self.config.accounts[0], NOW)
        self.assertEqual((first["fetched"], second["fetched"]), (2, 0))
        self.assertIn(("select", True), connection.calls)
        self.assertTrue(any(c[0] == "ID" for c in connection.calls))
        self.assertTrue(all("BODY.PEEK[]" in c[-1] for c in connection.calls if c[0] == "FETCH"))
        self.assertEqual(len(self.store.search(["qq"])["mails"]), 2)

    @patch.dict(os.environ, {"TEST_MAIL_CODE": "fake"})
    def test_failed_fetch_keeps_retry_position(self):
        connection = FakeIMAP(fail_uid=2)
        sync = MailSync(self.config, self.store, connection_factory=lambda *a, **k: connection)
        with self.assertRaises(RuntimeError):
            sync.sync_account(self.config.accounts[0], NOW)
        self.assertEqual(self.store.sync_state("qq")["last_uid"], 1)
        connection.fail_uid = None
        self.assertEqual(sync.sync_account(self.config.accounts[0], NOW)["fetched"], 1)
        self.assertEqual(self.store.sync_state("qq")["last_uid"], 2)

    @patch.dict(os.environ, {"TEST_MAIL_CODE": "fake"})
    def test_uidvalidity_reset_rescans_without_duplicate_mail(self):
        connection = FakeIMAP()
        sync = MailSync(self.config, self.store, connection_factory=lambda *a, **k: connection)
        sync.sync_account(self.config.accounts[0], NOW)
        connection.validity = "101"
        self.assertEqual(sync.sync_account(self.config.accounts[0], NOW)["fetched"], 2)
        self.assertEqual(len(self.store.search(["qq"])["mails"]), 2)
        self.assertEqual(self.store.sync_state("qq")["uidvalidity"], "101")

    @patch.dict(os.environ, {"TEST_MAIL_CODE": "fake"})
    def test_bootstrap_backfill_searchable_but_not_notified(self):
        connection = FakeIMAP(received={1: "20-Oct-2026 07:00:00 +0000"})
        sync = MailSync(self.config, self.store, connection_factory=lambda *a, **k: connection)
        sync.sync_account(self.config.accounts[0], NOW)
        self.assertEqual(len(self.store.search(["qq"])["mails"]), 2)
        self.assertEqual(len(self.store.pending(["qq"])[0]), 1)

    def test_pagination_and_disabled_account_scope(self):
        seed(self.store, NOW)
        first = self.store.search(["qq", "163"], limit=2)
        second = self.store.search(["qq", "163"], limit=2, offset=2)
        self.assertTrue(first["has_more"])
        self.assertFalse({m["id"] for m in first["mails"]} & {m["id"] for m in second["mails"]})
        self.assertIsNone(self.store.read("m_demo_submission", ["qq"]))

    def test_cross_account_thread_and_subject_candidate_distinction(self):
        seed(self.store, NOW)
        original = self.store.read("m_demo_meeting_old", ["qq"])
        candidate = {k: v for k, v in original.items() if k not in {"norm_subject", "digested"}}
        candidate.update(id="m_unrelated", uid=99, message_id="<unrelated@example.edu>", refs=[])
        self.store.put_mail(candidate)
        result = self.store.thread("m_demo_meeting_old", ["qq", "163"])
        self.assertEqual({m["id"] for m in result["linked"]}, {"m_demo_meeting_old", "m_demo_meeting_update"})
        self.assertEqual([m["id"] for m in result["subject_candidates"]], ["m_unrelated"])


class AgentTests(BaseTest):
    def test_digest_failed_delivery_reuses_draft_and_does_not_skip(self):
        seed(self.store, NOW)
        transport = RecordingFeishu(fail=True)
        service = Service(self.agent, transport)
        with self.assertRaises(FeishuError):
            service.send_daily()
        self.assertEqual(len(self.store.pending(["qq", "163"])[0]), 3)
        transport.fail = False
        service.send_daily()
        self.assertEqual(transport.calls[0][1], transport.calls[1][1])
        self.assertEqual(transport.calls[0][0], transport.calls[1][0])
        self.assertFalse(self.store.pending(["qq", "163"])[0])

    def test_preview_and_malformed_summary_do_not_acknowledge(self):
        seed(self.store, NOW)
        self.agent.digest(DemoSync().run(), now=NOW)
        self.assertIsNone(self.store.pending_digest())
        self.assertEqual(len(self.store.pending(["qq", "163"])[0]), 3)
        with patch.object(self.agent.model, "complete", return_value={"content": '{"items":[]}'}):
            with self.assertRaises(ModelError):
                self.agent.digest(DemoSync().run(), persist=True, now=NOW)
        self.assertIsNone(self.store.pending_digest())

    def test_multiturn_query_reads_updated_thread(self):
        seed(self.store, NOW)
        self.agent.ask("找一下导师的会议通知", now=NOW)
        answer = self.agent.ask("那封邮件后来有没有更新？", now=NOW)
        self.assertIn("16:00", answer)
        self.assertIn("B202", answer)
        self.assertIn("get_thread", [t["tool"] for t in self.agent.last_trace])
        self.assertEqual(len(self.store.get("history:terminal")), 2)

    def test_partial_sync_visible_and_does_not_advance_failed_account(self):
        seed(self.store, NOW)
        results = [{"account": "qq", "ok": True, "has_more": False}, {"account": "163", "ok": False, "error": "连接失败"}]
        before = self.store.get("digest_since:163")
        digest = self.agent.digest(results, persist=True, now=NOW)
        self.assertIn("新邮件可能未覆盖", digest["body"])
        self.store.commit_digest(digest)
        self.assertEqual(self.store.get("digest_since:163"), before)
        answer = self.agent.ask("会议通知", results=results, now=NOW)
        self.assertIn("新邮件可能未覆盖", answer)

    def test_date_filter_uses_shanghai_midnight(self):
        self.assertEqual(self.agent._date_bound("2026-11-03"), "2026-11-02T16:00:00+00:00")

    def test_daily_schedule_before_at_after_and_restart(self):
        zone = ZoneInfo("Asia/Shanghai")
        self.assertFalse(daily_due(datetime(2026, 11, 3, 8, 59, tzinfo=zone), "09:00", None))
        self.assertTrue(daily_due(datetime(2026, 11, 3, 9, 0, tzinfo=zone), "09:00", None))
        self.assertFalse(daily_due(datetime(2026, 11, 3, 12, 0, tzinfo=zone), "09:00", "2026-11-03"))
        self.assertTrue(daily_due(datetime(2026, 11, 4, 12, 0, tzinfo=zone), "09:00", "2026-11-03"))

    def test_event_reply_retry_dedup_and_restart_persistence(self):
        seed(self.store, NOW)
        event = {"message_id": "om_test", "chat_id": "oc_test", "content": "会议通知"}
        self.store.queue_event(event)
        transport = RecordingFeishu(fail=True)
        service = Service(self.agent, transport)
        with self.assertRaises(FeishuError):
            service.handle_event(event)
        self.assertFalse(self.store.handled("om_test"))
        transport.fail = False
        service.handle_event(event)
        service.handle_event(event)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[0], transport.calls[1])
        self.assertEqual(len(self.store.get("history:oc_test")), 1)
        self.assertEqual(self.store.queued_events(), [])


class FeishuTests(unittest.TestCase):
    def test_successful_chunks_not_replayed_after_partial_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "receipts.sqlite3")
            calls = []
            failing = True
            def run(command, **kwargs):
                calls.append(command)
                if len(calls) == 2 and failing:
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr='{"ok":false,"error":{"type":"network"}}')
                return subprocess.CompletedProcess(command, 0, stdout='{"ok":true}', stderr="")
            client = Feishu(FeishuConfig(receive_id="ou_test"), run=run, store=store)
            try:
                with self.assertRaises(FeishuError):
                    client.deliver("中文" * 2000, "persistent-delivery")
                failing = False
                client.deliver("中文" * 2000, "persistent-delivery")
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[1], calls[2])
                self.assertNotEqual(calls[0], calls[2])
            finally:
                store.close()

    def test_cli_envelope_stdin_payload_bot_identity_and_stable_keys(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout='{"ok":true,"data":{}}', stderr="")
        client = Feishu(FeishuConfig(receive_id="ou_test"), run=run)
        client.deliver("中文摘要\n" * 1000, "digest:test")
        first_keys = [c[0][c[0].index("--idempotency-key") + 1] for c in calls]
        count = len(calls)
        client.deliver("中文摘要\n" * 1000, "digest:test")
        self.assertGreater(count, 1)
        self.assertEqual(first_keys, [c[0][c[0].index("--idempotency-key") + 1] for c in calls[count:]])
        self.assertIn("--as", calls[0][0])
        self.assertIn("bot", calls[0][0])
        payload = json.loads(calls[0][1]["input"])
        self.assertIn("中文摘要", payload["zh_cn"]["content"][0][0]["text"])
        with patch.object(client, "run", return_value=subprocess.CompletedProcess([], 0, stdout='{"ok":false}', stderr="")):
            with self.assertRaises(FeishuError):
                client.deliver("失败不能标记成功", "failure")

    def test_compact_event_content_is_plain_text_and_owner_is_filtered(self):
        client = Feishu(FeishuConfig(receive_id="ou_me"))
        event = {"sender_id": "ou_me", "sender_type": "user", "message_id": "om_a", "message_type": "text", "chat_type": "p2p", "content": "查一下今天的邮件"}
        self.assertTrue(client.accepts(event))
        self.assertFalse(client.accepts(event | {"sender_type": "bot"}))
        self.assertFalse(client.accepts(event | {"sender_id": "ou_other"}))

    def test_event_listener_waits_for_ready_marker_and_reads_ndjson(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "fake-lark"
            executable.write_text(f"#!{sys.executable}\nimport sys,json\nprint('[event] ready event_key=im.message.receive_v1',file=sys.stderr,flush=True)\nprint(json.dumps({{'message_id':'om_ready','content':'中文正文'}}),flush=True)\n")
            executable.chmod(0o755)
            client = Feishu(FeishuConfig(cli=str(executable)))
            with client.listen() as lines:
                event = json.loads(next(lines))
                self.assertEqual(event["content"], "中文正文")


class HTTPIntegrationTests(BaseTest):
    def test_real_http_transport_tool_loop_and_json_digest(self):
        seed(self.store, NOW)
        requests = []
        simulated = DemoModel()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), body))
                response = simulated.complete(body["messages"], body.get("tools"), json_mode="response_format" in body)
                data = json.dumps({"choices": [{"finish_reason": "tool_calls" if response.get("tool_calls") else "stop", "message": response}]}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = ModelConfig(base_url=f"http://127.0.0.1:{server.server_port}/v1", name="test-model", api_key_env="TEST_MODEL_KEY")
            with patch.dict(os.environ, {"TEST_MODEL_KEY": "fake-api-key"}):
                self.agent.model = ChatModel(config)
                digest = self.agent.digest(DemoSync().run(), now=NOW)
                answer = self.agent.ask("会议有没有更新？", now=NOW)
            self.assertIn("高优先级", digest["body"])
            self.assertIn("16:00", answer)
            self.assertTrue(all(r[0] == "/v1/chat/completions" for r in requests))
            self.assertTrue(all(r[1] == "Bearer fake-api-key" for r in requests))
            self.assertTrue(any(r[2].get("tools") for r in requests))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
