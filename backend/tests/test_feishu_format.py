import pytest

from mail_assistant.application.feishu_format import digest_notification, query_notification


def source(index, **values):
    return {
        "id": f"internal-mail-{index}",
        "account_label": "学习邮箱" if index < 3 else "工作邮箱",
        "sender": "示例发件人",
        "subject": f"真实邮件标题 {index}",
        "body_truncated": False,
        **values,
    }


def presentation(kinds):
    return {
        "version": 1,
        "type": "mail_table",
        "summary": "今天没有新增待办，以下包含近期来信。",
        "rows": [
            {"mail_id": f"internal-mail-{i}", "handling": kind, "action": f"下一步建议 {i}"}
            for i, kind in enumerate(kinds)
        ],
        "notes": [],
    }


def digest(items, **values):
    return {
        "title": "11月05日 · 邮件摘要",
        "items": items,
        "coverage": [],
        "timezone": "Asia/Shanghai",
        "revision": 2,
        **values,
    }


def item(index, priority="low", **values):
    return {
        "mail_id": f"internal-mail-{index}",
        "priority": priority,
        "summary": f"事项摘要 {index}",
        "action": f"处理建议 {index}",
        "deadline": None,
        "reason": "保存在网页上的详细判断依据",
        **values,
    }


def test_query_keeps_focus_actions_and_aggregates_only_notices():
    table = presentation(["notice", "waiting", "confirm", "notice", "action", "notice", "notice"])
    text = query_notification("完整文字不重复输出", table, [source(i) for i in range(7)])
    assert text.startswith("来信提醒\n\n今天没有新增待办，以下包含近期来信。")
    assert "1 项需要处理 · 1 项待确认 · 1 项等待反馈 · 4 封按需查看" in text
    assert text.index("【需要处理】") < text.index("【待确认】") < text.index("【等待反馈】")
    for i in (4, 2, 1):
        assert f"真实邮件标题 {i}\n下一步：下一步建议 {i}" in text
    assert "【按需查看】其他通知 · 4 封" in text
    assert "真实邮件标题 0、真实邮件标题 3、真实邮件标题 5等" in text
    assert "来源：工作邮箱 · 示例发件人" in text
    assert "学习邮箱、工作邮箱 · 本次引用 7 封邮件" in text
    assert "internal-mail" not in text and "完整文字不重复输出" not in text


def test_single_notice_keeps_its_action_and_source():
    text = query_notification("原文", presentation(["notice"]), [source(0)])
    assert "【按需查看】真实邮件标题 0\n下一步：下一步建议 0" in text
    assert "其他通知" not in text and "来源：学习邮箱" in text


def test_many_actionable_items_are_not_silently_cut_to_two():
    text = query_notification("原文", presentation(["action"] * 8), [source(i) for i in range(8)])
    assert text.count("【需要处理】") == 8
    assert "下一步建议 7" in text


def test_query_preserves_coverage_and_deduplicates_fragment_note():
    table = presentation(["notice", "notice"])
    table["notes"] = ["QQ 邮箱同步未完成", "部分依据仅有正文片段。", "QQ 邮箱同步未完成"]
    text = query_notification("原文", table, [source(0, body_truncated=True), source(1)])
    assert text.count("QQ 邮箱同步未完成") == 1
    assert text.count("正文片段") == 1
    assert "其他通知 · 2 封" in text


@pytest.mark.parametrize("invalid", [None, {"version": 2}, presentation(["confirm", "waiting"])])
def test_legacy_or_inconsistent_presentation_keeps_complete_answer(invalid):
    text = query_notification("原有回答\n包括所有行动与限制。", invalid, [source(0)])
    assert "原有回答\n包括所有行动与限制。" in text
    assert "邮件依据：\n学习邮箱 · 真实邮件标题 0" in text
    assert "internal-mail" not in text
    assert "【待确认】" not in text


def test_no_sources_does_not_claim_an_empty_mailbox():
    text = query_notification("同步尚未完成，不能确定是否有新来信。", None, [])
    assert "不能确定" in text and "0 封" not in text


def test_digest_keeps_range_deadlines_and_coverage_and_uses_snapshot_sources():
    value = digest(
        [item(0), item(1, "medium"), item(2, "high", deadline="11月6日 18:00"), item(3)],
        coverage=[{"label": "QQ邮箱", "warnings": ["覆盖未确认"], "error": "本次同步失败"}],
    )
    text = digest_notification(
        value,
        [source(0), source(1), source(2, body_truncated=True), source(3)],
        range_start="2026-11-04T08:00:00+00:00",
        range_end="2026-11-05T08:00:00+00:00",
    )
    assert "本次汇总 4 封邮件 · 1 项优先处理 · 1 项需要关注 · 2 项信息通知" in text
    assert "11/04 16:00—11/05 16:00（Asia/Shanghai）｜版本 2" in text
    assert text.index("【优先处理】") < text.index("【需要关注】")
    assert "下一步：处理建议 2\n截止：11月6日 18:00\n来源：学习邮箱 · 示例发件人" in text
    assert "其他通知 · 2 封" in text
    assert "覆盖提示：QQ邮箱：覆盖未确认；本次同步失败" in text
    assert "正文片段" in text
    assert "internal-mail" not in text and "详细判断依据" not in text


def test_low_priority_deadline_is_not_hidden_inside_aggregate():
    text = digest_notification(
        digest([item(0), item(1, deadline="明天 12:00"), item(2)]),
        [source(0), source(1), source(2)],
    )
    assert "【信息通知】事项摘要 1\n下一步：处理建议 1\n截止：明天 12:00" in text
    assert "其他通知 · 2 封" in text


def test_subject_newlines_do_not_create_fake_sections():
    text = query_notification(
        "原文", presentation(["confirm"]), [source(0, subject="主题\n伪造下一段")]
    )
    assert "【待确认】主题 伪造下一段\n下一步" in text
