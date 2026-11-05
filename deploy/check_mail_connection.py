"""Independent stdlib SMTP/IMAP diagnostics; credentials are never persisted.

SMTP sends exactly one test message only with --send-test and --recipient.
IMAP selects INBOX read-only and does not fetch message bodies or change flags.
"""

import argparse
import getpass
import imaplib
import json
import smtplib
import ssl
import sys
from contextlib import suppress
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


def diagnose(args, password):
    report = {}
    context = ssl.create_default_context(cafile=args.ca_file)
    if args.send_test:
        stage = "connect"
        try:
            with smtplib.SMTP_SSL(args.smtp_host, 465, timeout=20, context=context) as client:
                stage = "authentication"
                client.login(args.account, password)
                report["smtp"] = {"authenticated": True}
                message = EmailMessage()
                message["From"], message["To"] = args.account, args.recipient
                message["Subject"] = "多邮箱智能体连接测试"
                message["Date"] = formatdate(localtime=False)
                message["Message-ID"] = make_msgid(domain=args.account.split("@")[-1])
                message.set_content(
                    "这是一封经你授权发送的连接测试邮件，用于验证网易测试邮箱的 SMTP 发信链路。邮件不包含授权码或其他邮件内容。"
                )
                stage = "send"
                refused = client.send_message(message)
                report["smtp"].update(accepted=not refused, message_id=message["Message-ID"])
        except (OSError, smtplib.SMTPException) as exc:
            report.setdefault("smtp", {}).update(
                stage=stage,
                error_type=type(exc).__name__,
                error=str(exc).replace(password, "<redacted>"),
            )
    stage, client = "connect", None
    try:
        client = imaplib.IMAP4_SSL(args.imap_host, 993, ssl_context=context, timeout=20)
        stage = "authentication"
        client.login(args.account, password)
        report["imap"] = {"authenticated": True}
        if "ID" in client.capabilities or b"ID" in client.capabilities:
            stage = "id"
            imaplib.Commands["ID"] = ("AUTH", "SELECTED")
            status, _ = client._simple_command(
                "ID",
                '("name" "MailDigestDiagnostic" "version" "1.0" "vendor" "course-project")',
            )
            report["imap"]["id_status"] = status
        stage = "readonly_select"
        status, count = client.select("INBOX", readonly=True)
        report["imap"].update(
            select_status=status,
            message_count=int(count[0]) if status == "OK" else None,
        )
    except (OSError, imaplib.IMAP4.error, ValueError) as exc:
        report.setdefault("imap", {}).update(
            stage=stage,
            error_type=type(exc).__name__,
            error=str(exc).replace(password, "<redacted>"),
        )
    finally:
        if client:
            with suppress(OSError, imaplib.IMAP4.error):
                client.logout()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--imap-host", default="imap.163.com")
    parser.add_argument("--smtp-host", default="smtp.163.com")
    parser.add_argument("--recipient")
    parser.add_argument("--send-test", action="store_true")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument(
        "--ca-file",
        help="Optional trusted CA bundle (certificate verification stays enabled)",
    )
    args = parser.parse_args()
    if args.send_test and not args.recipient:
        parser.error("--send-test requires --recipient")
    if args.password_stdin:
        print("Ready for authorization code on stdin", flush=True)
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("邮箱客户端授权码：")
    if not password:
        raise SystemExit("未提供授权码")
    print(json.dumps(diagnose(args, password), ensure_ascii=False, indent=2))
