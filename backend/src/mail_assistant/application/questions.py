from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from ..domain.errors import AppError
from ..domain.models import Conversation, Message, Setting, Task
from ..infrastructure.database import record


class Questions:
    """One durable submission path shared by web and Feishu."""

    def __init__(self, db, tasks, mailboxes):
        self.db, self.tasks, self.mailboxes = db, tasks, mailboxes

    def submit(self, form, *, channel="web", request_key=None, conversation_key=None, context=None):
        self.mailboxes.selected(form.account_ids or None)
        key = request_key or ("web-query:" + form.request_id if form.request_id else None)

        def replay(row):
            payload = row.payload
            if (
                payload["question"] != form.question
                or sorted(payload.get("account_ids", [])) != sorted(form.account_ids)
                or (form.conversation_id and form.conversation_id != payload["conversation_id"])
            ):
                raise AppError(
                    "这个请求标识已用于另一条问题，请重新提交", "request_conflict", status=409
                )
            return record(row)

        try:
            with self.db.session() as session:
                if key:
                    existing = session.scalar(select(Task).where(Task.idempotency_key == key))
                    if existing:
                        return replay(existing)
                stored = session.get(Setting, conversation_key) if conversation_key else None
                conversation_id = form.conversation_id or (
                    stored.value.get("id") if stored else None
                )
                conversation = (
                    session.get(Conversation, conversation_id) if conversation_id else None
                )
                if form.conversation_id and (not conversation or conversation.channel != channel):
                    raise AppError("对话不存在，请新建对话", "conversation_not_found", status=404)
                if not conversation:
                    conversation = Conversation(title=form.question[:60], channel=channel)
                    session.add(conversation)
                    session.flush()
                    if conversation_key:
                        if stored:
                            stored.value = {"id": conversation.id}
                        else:
                            session.add(
                                Setting(key=conversation_key, value={"id": conversation.id})
                            )
                task = self.tasks.enqueue_in(
                    session,
                    "query",
                    {
                        **form.model_dump(exclude={"request_id"}),
                        "conversation_id": conversation.id,
                        **(context or {}),
                    },
                    lane="interactive",
                    key=key,
                )
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        role="user",
                        content=form.question,
                        sources=[],
                        task_id=task["id"],
                    )
                )
                return task
        except IntegrityError:
            if not key:
                raise
            with self.db.session() as session:
                existing = session.scalar(select(Task).where(Task.idempotency_key == key))
                if not existing:
                    raise
                return replay(existing)
