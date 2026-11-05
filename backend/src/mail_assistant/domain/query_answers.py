from pydantic import ValidationError

from .errors import AppError
from .schemas import QueryAnswer, QueryPresentation


HANDLING_LABELS = {
    "action": "需要处理",
    "confirm": "待确认",
    "waiting": "等待反馈",
    "notice": "按需查看",
}


def validate_query_answer(value, evidence):
    try:
        answer = QueryAnswer.model_validate(value)
    except ValidationError as exc:
        raise AppError("模型回答或事项表格格式不完整，请重试", "model_output_error") from exc
    ids = set(answer.mail_ids)
    row_ids = [row.mail_id for row in answer.rows]
    if ids - set(evidence) or set(row_ids) - ids or (row_ids and ids - set(row_ids)):
        raise AppError("回答引用了未读取的邮件，结果未保存，请重试", "invalid_sources")
    if len(row_ids) != len(set(row_ids)):
        raise AppError("模型返回了重复邮件事项，结果未保存，请重试", "model_output_error")
    answer.mail_ids = list(dict.fromkeys(answer.mail_ids))
    return answer.model_dump()


def present_query_answer(answer, evidence, warnings):
    """Keep a complete text fallback for history and Feishu; table data is additive."""
    if not answer["rows"]:
        prefix = "覆盖提示：" + "；".join(warnings) + "\n\n" if warnings else ""
        return prefix + answer["answer"], None
    notes = list(warnings)
    if any(evidence[row["mail_id"]].get("body_truncated") for row in answer["rows"]):
        notes.append("部分邮件仅有正文片段，相关判断以当前可见内容为限，请核对原文。")
    notes = list(dict.fromkeys(notes))
    presentation = QueryPresentation(summary=answer["answer"], rows=answer["rows"], notes=notes)
    lines = [answer["answer"]]
    for index, row in enumerate(answer["rows"], 1):
        source = evidence[row["mail_id"]]
        lines.append(
            f"{index}. 【{HANDLING_LABELS[row['handling']]}】{source['subject']}：{row['action']}"
        )
    if notes:
        lines.append("说明：" + "；".join(notes))
    return "\n\n".join(lines), presentation.model_dump()
