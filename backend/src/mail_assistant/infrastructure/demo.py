from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from ..domain.schemas import DigestItem


def fixtures(account):
    now = datetime.now(timezone.utc)
    values = [
        (
            "1",
            "课程项目阶段材料提交",
            "课程助教 <course@university.edu.cn>",
            "请提交智能体项目的选题说明和功能清单。\n行动：上传项目说明到课程平台。\n截止：明天 18:00",
            45,
            "<submission@university.edu.cn>",
            "",
        ),
        (
            "2",
            "课题组会议时间更新",
            "课题组导师 <advisor@university.edu.cn>",
            "原定周五 14:00、A301 的组会调整为周五 16:00，地点 B202。\n行动：确认参会，准备本周研究进展。",
            90,
            "<meeting-update@university.edu.cn>",
            "<meeting-old@university.edu.cn>",
        ),
        (
            "3",
            "本周人工智能研究简报",
            "研究生院资讯 <newsletter@university.edu.cn>",
            "本期介绍智能体工具调用、模型评测和公开讲座。仅供阅读，无需回复。",
            140,
            "<newsletter@university.edu.cn>",
            "",
        ),
        (
            "4",
            "课题组会议通知",
            "课题组导师 <advisor@university.edu.cn>",
            "组会暂定下周五 14:00，地点 A301。请准备研究进展。",
            7 * 24 * 60,
            "<meeting-old@university.edu.cn>",
            "",
        ),
        (
            "5",
            "校园邮箱服务通知",
            "信息化办公室 <support@university.edu.cn>",
            "本周六凌晨进行例行邮箱维护。已有邮件不受影响，用户无需操作。",
            230,
            "<maintenance@university.edu.cn>",
            "",
        ),
        (
            "6",
            "实验室设备借用确认",
            "实验室管理员 <lab@university.edu.cn>",
            "设备借用申请已收到。\n行动：请回复确认预计归还日期。",
            280,
            "<equipment@university.edu.cn>",
            "",
        ),
    ]
    use = values[::2] if account["provider"] == "qq" else values[1::2]
    return [
        {
            "email_id": uid,
            "message_id": mid,
            "subject": subject,
            "sender": sender,
            "recipients": [account["email"]],
            "date": (now - timedelta(minutes=minutes + 3)).isoformat(),
            "received_at": (now - timedelta(minutes=minutes)).isoformat(),
            "body": body,
            "references": refs,
            "in_reply_to": refs,
        }
        for uid, subject, sender, body, minutes, mid, refs in use
    ]


class DemoProvider:
    async def list_accounts(self):
        return []

    async def list_folders(self, account):
        return [
            {"name": "INBOX", "delimiter": "/", "flags": [], "selectable": True},
            {"name": "课程与项目", "delimiter": "/", "flags": [], "selectable": True},
            {"name": "归档", "delimiter": "/", "flags": ["\\Noselect"], "selectable": False},
        ]

    async def metadata(self, account, since, before, page=1):
        rows = [m for m in fixtures(account) if since <= m["received_at"] < before]
        return {
            "emails": rows[(page - 1) * 100 : page * 100],
            "total": len(rows),
            "page": page,
            "coverage_state": "complete",
            "warnings": [],
            "observations": fixtures(account),
            "membership_complete": True,
            "mailbox_state": {
                "uidvalidity": 1,
                "uidnext": 7,
                "message_count": len(fixtures(account)),
            },
        }

    async def bodies(self, account, ids):
        return [m for m in fixtures(account) if m["email_id"] in ids]

    async def close(self):
        pass


class DemoIntelligence:
    async def summarize(self, mails, progress=None, *, profile=None, analysis_time=None):
        items = []
        for mail in mails:
            action = re.search(r"行动[：:]([^\n]+)", mail["body"])
            deadline = re.search(r"截止[：:]([^\n]+)", mail["body"])
            items.append(
                DigestItem(
                    mail_id=mail["id"],
                    priority="high" if deadline else "medium" if action else "low",
                    summary=mail["subject"],
                    action=action.group(1) if action else "暂未发现需要处理的事项",
                    deadline=deadline.group(1) if deadline else None,
                    reason="样例规则：邮件有明确截止时间"
                    if deadline
                    else "样例规则：正文要求采取行动"
                    if action
                    else "样例规则：资讯或服务通知",
                )
            )
        return items

    async def draft_reply(self, intent, source, linked):
        latest = max([source, *linked], key=lambda mail: mail["sent_at"])
        if (
            "会议" in source["subject"]
            and intent.startswith("确认参会")
            and "周五 16:00" in latest["body"]
        ):
            body = "老师您好，\n\n我可以参加周五 16:00 的组会。"
            if "PPT" in intent.upper():
                body += "请问需要提前发送汇报 PPT 吗？"
            return {"body": body + "\n\n谢谢老师。"}
        return {"body": f"您好，\n\n{intent}\n\n谢谢。"}

    async def answer(self, question, history, tools, progress=None):
        update = any(word in question for word in ("更新", "后来", "最终", "那封", "改"))
        result = await tools.search_mails(
            ["会议"] if update or "会议" in question or "导师" in question else []
        )
        mails = result["mails"]
        if not mails:
            return {"answer": "样例缓存中没有匹配邮件，可以换一个关键词再试。", "mail_ids": []}
        if update:
            thread = await tools.get_thread(mails[0]["id"])
            latest = thread["linked"][-1]
            return {
                "answer": f"会议已有更新：{latest['body']}\n\n以上是样例往来中的最新通知。",
                "mail_ids": [m["id"] for m in thread["linked"]],
            }
        read = await tools.read_mails([m["id"] for m in mails[:10]])
        if any(word in question for word in ("需要", "处理", "待办", "优先")):
            rows = []
            for mail in read["mails"]:
                match = re.search(r"行动：([^\n]+)", mail["body"])
                needs_action = bool(match) or "请准备" in mail["body"]
                rows.append(
                    {
                        "mail_id": mail["id"],
                        "handling": "action" if needs_action else "notice",
                        "action": match[1]
                        if match
                        else "准备研究进展，核对会议安排。"
                        if needs_action
                        else "按需阅读，无需新增操作。",
                    }
                )
            return {
                "answer": "已检查样例邮箱近期来信，以下是需要关注的事项和下一步建议。",
                "mail_ids": [mail["id"] for mail in read["mails"]],
                "rows": rows,
            }
        return {
            "answer": "根据样例邮件，找到以下信息：\n\n"
            + "\n\n".join(f"{m['subject']}\n{m['body']}" for m in read["mails"]),
            "mail_ids": [m["id"] for m in read["mails"]],
        }

    async def probe(self):
        return {"structured_output": True, "tool_calling": True, "simulated": True}
