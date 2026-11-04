"""Explicitly simulated mail/model adapters for a credential-free walkthrough."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta

from .agent import MailAgent
from .config import Account, Config
from .mailbox import UTC, utc_iso
from .store import Store

NOTICE = "【离线演示】使用样例邮件与规则模拟模型；未连接真实邮箱、大模型或飞书，不代表真实模型效果。"


def seed(store: Store, now: datetime):
    base = now - timedelta(minutes=60)
    fixtures = [
        ("m_demo_meeting_old", "qq", "导师 <advisor@example.edu>", "课题组会议通知", "下周组会暂定周五 14:00，地点 A301。请准备研究进展。", base - timedelta(days=7), "<meeting-original@example.edu>", []),
        ("m_demo_meeting_update", "163", "导师 <advisor@example.edu>", "Re: 课题组会议通知", "更新：原周五 14:00 的组会改为周五 16:00，地点调整为 B202。行动：请确认参会并准备进展。", base, "<meeting-update@example.edu>", ["<meeting-original@example.edu>"]),
        ("m_demo_submission", "163", "课程助教 <ta@example.edu>", "智能体课程材料提交", "行动：请提交项目选题和功能说明。\n截止：明日 18:00\n请在截止前上传到课程平台。", base + timedelta(minutes=10), "<submission@example.edu>", []),
        ("m_demo_news", "qq", "科研资讯 <news@example.edu>", "本周人工智能资讯", "本期介绍多篇人工智能论文与公开讲座。无需回复。", base + timedelta(minutes=20), "<news@example.edu>", []),
    ]
    for index, (mail_id, account, sender, subject, body, received, msgid, refs) in enumerate(fixtures, 1):
        store.put_mail({"id": mail_id, "account": account, "uidvalidity": "demo", "uid": index, "message_id": msgid, "refs": refs, "subject": subject, "sender": sender, "recipient": "学生 <student@example.edu>", "sent_at": utc_iso(received), "received_at": utc_iso(received), "body": body, "body_truncated": 0}, digested=index == 1)
    for account in ("qq", "163"):
        store.set(f"digest_since:{account}", utc_iso(now - timedelta(hours=24)))
        store.set(f"sync_at:{account}", utc_iso(now))


class DemoSync:
    def run(self):
        return [{"account": a, "ok": True, "fetched": 0, "has_more": False} for a in ("qq", "163")]


class DemoModel:
    def call(self, name, args):
        return {"role": "assistant", "content": None, "tool_calls": [{"id": "call_" + uuid.uuid4().hex, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}

    def complete(self, messages, tools=None, *, json_mode=False):
        if json_mode:
            mails = json.loads(messages[-1]["content"])
            items = []
            for mail in mails:
                body = mail["body"]
                action = re.search(r"行动[：:]([^\n]+)", body)
                deadline = re.search(r"截止[：:]([^\n]+)", body)
                items.append({"id": mail["id"], "priority": "high" if deadline else "medium" if action else "low", "summary": mail["subject"], "action": action.group(1) if action else "无需明确行动", "deadline": deadline.group(1) if deadline else None, "reason": "演示规则：存在明确截止时间" if deadline else "演示规则：正文有行动要求" if action else "演示规则：资讯内容"})
            return {"role": "assistant", "content": json.dumps({"items": items}, ensure_ascii=False)}
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        question = messages[last_user]["content"]
        calls = [m for m in messages[last_user + 1:] if m["role"] == "tool"]
        update = any(word in question for word in ("更新", "后来", "最终", "那封", "改"))
        if not calls:
            return self.call("search_mails", {"keywords": ["会议"] if update or "会议" in question or "导师" in question else [], "limit": 20})
        result = json.loads(calls[-1]["content"])
        if "mails" in result and "has_more" in result:
            ids = [m["id"] for m in result["mails"]]
            if not ids:
                return {"role": "assistant", "content": "样例缓存中没有匹配结果。"}
            return self.call("get_thread", {"mail_id": ids[0]}) if update else self.call("read_mails", {"mail_ids": ids[:10]})
        if "linked" in result:
            mails = result["linked"]
            latest = mails[-1]
            content = f'按样例往来邮件，最新通知是：{latest["body"]} [{latest["id"]}]'
        else:
            mails = result.get("mails", [])
            content = "样例邮件查询结果：\n" + "\n".join(f'{m["subject"]}：{m["body"][:250]} [{m["id"]}]' for m in mails)
        return {"role": "assistant", "content": content}


def create_demo(root, database):
    config = Config(root=root, database=database, accounts=[Account("qq", "qq", "demo@qq.com", "DEMO_QQ"), Account("163", "163", "demo@163.com", "DEMO_163")])
    store = Store(database)
    seed(store, datetime.now(UTC))
    return MailAgent(config, store, DemoModel(), DemoSync())
