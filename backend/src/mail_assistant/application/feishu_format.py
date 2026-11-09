"""Render concise post notifications without changing the saved analysis or delivery."""

from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from ..domain.query_answers import HANDLING_LABELS
from ..domain.schemas import QueryPresentation


def line(value):
    return " ".join(str(value or "").split())


def source_line(source):
    origin = " · ".join(line(source[key]) for key in ("account_label", "sender") if source.get(key))
    return "来源：" + origin if origin else "来源信息未保留"


def notices_section(entries):
    """Only informational rows are aggregated; never limit actionable rows."""
    if not entries:
        return []
    if len(entries) == 1:
        entry = entries[0]
        return [
            f"【按需查看】{line(entry['title'])}\n下一步：{line(entry['action'])}\n"
            + source_line(entry["source"])
        ]
    titles = "、".join(line(entry["title"]) for entry in entries[:3])
    if len(entries) > 3:
        titles += "等"
    return [f"【按需查看】其他通知 · {len(entries)} 封\n{titles}，可按需查询详情。"]


def footer(sources, notes=()):
    labels = list(
        dict.fromkeys(line(s["account_label"]) for s in sources if s.get("account_label"))
    )
    origin = "、".join(labels)
    blocks = (
        [f"邮件依据：{origin + ' · ' if origin else ''}本次引用 {len(sources)} 封邮件。"]
        if sources
        else []
    )
    warnings = list(notes)
    if any(s.get("body_truncated") for s in sources) and not any(
        "片段" in note or "截断" in note for note in warnings
    ):
        warnings.append("部分邮件仅有正文片段，相关判断以当前可见内容为限，请核对原文。")
    blocks.extend("说明：" + line(note) for note in dict.fromkeys(warnings) if note)
    return blocks


def query_notification(content, presentation, sources):
    sources = list({source["id"]: source for source in sources}.values())
    by_id = {source["id"]: source for source in sources}
    try:
        table = QueryPresentation.model_validate(presentation)
    except ValidationError:
        table = None
    # Older or incompatible answers retain their full text, rather than
    # inferring new classifications from natural-language paragraphs.
    if (
        table is None
        or not table.rows
        or len({row.mail_id for row in table.rows}) != len(table.rows)
        or {row.mail_id for row in table.rows} != set(by_id)
    ):
        provenance = [
            " · ".join(line(source[key]) for key in ("account_label", "subject") if source.get(key))
            for source in sources
        ]
        blocks = ["来信提醒", content]
        if provenance:
            blocks.append("邮件依据：\n" + "\n".join(provenance))
        blocks.extend(footer(sources)[1:])
        return "\n\n".join(blocks)

    counts = Counter(row.handling for row in table.rows)
    order = tuple(HANDLING_LABELS)
    overview = " · ".join(
        f"{counts[kind]} {'封' if kind == 'notice' else '项'}{HANDLING_LABELS[kind]}"
        for kind in order
        if counts[kind]
    )
    blocks = ["来信提醒", table.summary, overview]
    notices = []
    for kind in order:
        for row in table.rows:
            if row.handling != kind:
                continue
            source = by_id[row.mail_id]
            title = source.get("subject") or "（无主题）"
            if kind == "notice":
                notices.append({"title": title, "action": row.action, "source": source})
            else:
                blocks.append(
                    f"【{HANDLING_LABELS[kind]}】{line(title)}\n下一步：{line(row.action)}\n"
                    + source_line(source)
                )
    blocks.extend(notices_section(notices))
    blocks.extend(footer(sources, table.notes))
    return "\n\n".join(blocks)


def digest_notification(digest, sources, *, range_start=None, range_end=None):
    by_id = {source["id"]: source for source in sources}
    items = digest["items"]
    counts = Counter(item["priority"] for item in items)
    priorities = {"high": "优先处理", "medium": "需要关注", "low": "信息通知"}
    overview = " · ".join(
        f"{counts[key]} 项{label}" for key, label in priorities.items() if counts[key]
    )
    blocks = [digest["title"], f"本次汇总 {len(items)} 封邮件 · {overview}"]
    if range_start and range_end:
        zone = ZoneInfo(digest["timezone"])
        start = datetime.fromisoformat(range_start).astimezone(zone)
        end = datetime.fromisoformat(range_end).astimezone(zone)
        blocks.append(
            f"范围：{start:%m/%d %H:%M}—{end:%m/%d %H:%M}（{digest['timezone']}）｜版本 {digest['revision']}"
        )
    notices = []
    for priority, label in priorities.items():
        for item in items:
            if item["priority"] != priority:
                continue
            source = by_id.get(item["mail_id"], {})
            if priority == "low" and not item.get("deadline"):
                notices.append(
                    {"title": item["summary"], "action": item["action"], "source": source}
                )
                continue
            block = f"【{label}】{line(item['summary'])}\n下一步：{line(item['action'])}"
            if item.get("deadline"):
                block += "\n截止：" + line(item["deadline"])
            blocks.append(block + "\n" + source_line(source))
    blocks.extend(notices_section(notices))
    notes = []
    for coverage in digest["coverage"]:
        warnings = list(coverage.get("warnings", []))
        if coverage.get("error"):
            warnings.append(coverage["error"])
        if warnings:
            notes.append("覆盖提示：" + coverage.get("label", "邮箱") + "：" + "；".join(warnings))
    blocks.extend(footer(sources, notes))
    return "\n\n".join(blocks)
