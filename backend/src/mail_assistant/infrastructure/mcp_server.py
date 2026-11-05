"""Pinned upstream MCP plus two read-only observation tools.

The upstream public metadata DTO drops INTERNALDATE and UIDVALIDITY. This small
adapter exposes its provider port; it never opens upstream database tables.
The bounded snapshot can be partial, even when all returned rows were read.
"""

from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field
from mcp_email_server.adapters.metadata import LocalMetadataBackend
from mcp_email_server.app import mcp
from mcp_email_server.stdio import run_bounded_stdio

_retained = OrderedDict()


def access(account_name):
    backend = LocalMetadataBackend()
    account = backend.resolve(account_name)
    return backend.open(account_name, expected_mode=account.mode)


async def observe_snapshot(provider, mailbox, previous=None):
    """Keep raw upstream evidence until serialization; injectable for contract tests."""
    return await provider.mailbox_snapshot(
        mailbox,
        retained_emails=previous.emails if previous else (),
        retained_uidvalidity=previous.state.uidvalidity if previous else None,
    )


def snapshot_payload(snapshot):
    rows, warnings = [], []
    for row in snapshot.emails:
        received = row.get("_internal_date")
        rows.append(
            {
                "email_id": str(row["email_id"]),
                "message_id": row.get("message_id") or "",
                "received_at": received.astimezone(timezone.utc).isoformat()
                if isinstance(received, datetime) and received.tzinfo
                else None,
            }
        )
    if not snapshot.complete:
        warnings.append("上游只返回有界快照，尚未证明覆盖整个邮箱；窗口上限或同步变化可能遗漏邮件")
    elif snapshot.state.message_count != len(rows):
        warnings.append("快照数量与邮箱状态不一致，不能确认完整覆盖")
    uids = sorted(int(row["email_id"]) for row in rows)
    valid_inventory = (
        len(set(uids)) == len(uids)
        and all(0 < uid <= 2**32 - 1 for uid in uids)
        and snapshot.state.message_count == len(uids)
    )
    if not valid_inventory:
        warnings.append("邮件编号清单无法完整核对，请重新同步")
    if snapshot.state.uidvalidity is None:
        warnings.append("邮箱未提供 UIDVALIDITY，邮件编号的有效期尚未确认")
    if any(row["received_at"] is None for row in rows):
        warnings.append("部分邮件缺少服务器收件时间，时间范围无法完整确认")
    membership_complete = bool(
        snapshot.complete and snapshot.state.uidvalidity is not None and valid_inventory
    )
    diagnostics = []
    if snapshot.state.uidnext is None:
        diagnostics.append("服务端未提供 UIDNEXT，使用完整邮件编号清单核对同步前后状态")
    return {
        "emails": rows,
        "membership_complete": membership_complete,
        "coverage_state": "complete"
        if snapshot.complete and not warnings
        else "partial"
        if not snapshot.complete
        else "unknown",
        "warnings": warnings,
        "mailbox_state": asdict(snapshot.state)
        | {
            "uid_digest": hashlib.sha256(
                json.dumps(uids, separators=(",", ":")).encode()
            ).hexdigest(),
            "inventory_complete": membership_complete,
        },
        "sync_method": "uid_inventory" if snapshot.state.uidnext is None else "uid_state",
        "diagnostics": diagnostics,
        "observed_at": snapshot.observed_at.isoformat(),
    }


async def read_snapshot(account_name, mailbox):
    opened = access(account_name)
    if opened.account.allowed_senders:
        raise ValueError("工作台快照尚不支持上游发件人策略，请使用独立的工作台账号目录")
    key = (account_name, mailbox)
    snapshot = await observe_snapshot(opened.provider, mailbox, _retained.get(key))
    _retained[key] = snapshot
    _retained.move_to_end(key)
    while len(_retained) > 8:
        _retained.popitem(last=False)
    return snapshot_payload(snapshot)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
async def workspace_mailbox_snapshot(
    account_name: Annotated[str, Field(min_length=1, max_length=200)],
    mailbox: Annotated[str, Field(min_length=1, max_length=200)] = "INBOX",
) -> dict:
    """Observe bounded UID metadata, server receive times, and explicit coverage."""
    return await read_snapshot(account_name, mailbox)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
async def workspace_mailbox_state(
    account_name: Annotated[str, Field(min_length=1, max_length=200)],
    mailbox: Annotated[str, Field(min_length=1, max_length=200)] = "INBOX",
) -> dict:
    """Re-observe UIDVALIDITY and the UID inventory after body reads.

    Metadata rows may be reused within a real UIDVALIDITY, but the provider
    always obtains a fresh UID list. Equal counts alone cannot verify identity.
    """
    return (await read_snapshot(account_name, mailbox))["mailbox_state"]


if __name__ == "__main__":
    run_bounded_stdio(mcp)
