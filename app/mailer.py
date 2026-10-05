"""Transactional account email (password reset); never used for customer mailboxes.

Delivery order: Resend over HTTPS (Railway's recommendation; SMTP is disabled on
its Free, Trial and Hobby plans), then SMTP, then a local outbox folder meant
only for development and tests. Nothing is sent without explicit configuration.
"""
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
import logging
import os
from pathlib import Path
import re
import secrets
import smtplib
import ssl

import httpx

logger = logging.getLogger(__name__)
RESEND_API = "https://api.resend.com/emails"
_ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}")


class MailNotConfigured(Exception):
    pass


class MailDeliveryError(Exception):
    pass


def _configuration():
    return {key: os.environ.get(key, "").strip() for key in (
        "FILO_MAIL_FROM", "FILO_RESEND_API_KEY", "FILO_SMTP_HOST", "FILO_SMTP_PORT",
        "FILO_SMTP_USERNAME", "FILO_SMTP_PASSWORD", "FILO_SMTP_SECURITY", "FILO_MAIL_OUTBOX_DIR",
    )}


def _sender(config):
    name, address = parseaddr(config["FILO_MAIL_FROM"])
    if not _ADDRESS.fullmatch(address or "") or any(char in config["FILO_MAIL_FROM"] for char in "\r\n"):
        return None
    return formataddr((name or "Spazelia", address))


def mail_backend(config=None):
    config = config or _configuration()
    if not _sender(config):
        return None
    if config["FILO_RESEND_API_KEY"]:
        return "resend"
    if config["FILO_SMTP_HOST"]:
        return "smtp"
    if config["FILO_MAIL_OUTBOX_DIR"]:
        return "outbox"
    return None


def mail_configured():
    return mail_backend() is not None


def send_email(to, subject, text, html=None, idempotency_key=None):
    """Send one message; errors never include provider responses or secrets."""
    config = _configuration()
    backend = mail_backend(config)
    if backend is None:
        raise MailNotConfigured("Email delivery is not configured")
    if not _ADDRESS.fullmatch(to or "") or any(char in subject for char in "\r\n"):
        raise MailDeliveryError("Invalid recipient or subject")
    sender = _sender(config)
    if backend == "resend":
        payload = {"from": sender, "to": [to], "subject": subject, "text": text}
        if html:
            payload["html"] = html
        headers = {"Authorization": "Bearer " + config["FILO_RESEND_API_KEY"]}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = httpx.post(RESEND_API, json=payload, headers=headers, timeout=httpx.Timeout(10.0, connect=5.0))
        except httpx.HTTPError as exc:
            raise MailDeliveryError("Resend is unreachable") from exc
        if not response.is_success:
            raise MailDeliveryError(f"Resend answered {response.status_code}")
        return
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=parseaddr(sender)[1].rpartition("@")[2])
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    if backend == "outbox":
        folder = Path(config["FILO_MAIL_OUTBOX_DIR"])
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        (folder / f"{secrets.token_hex(8)}.eml").write_bytes(bytes(message))
        return
    try:
        port = int(config["FILO_SMTP_PORT"] or (465 if config["FILO_SMTP_SECURITY"] == "ssl" else 587))
        context = ssl.create_default_context()
        if config["FILO_SMTP_SECURITY"] == "ssl":
            client = smtplib.SMTP_SSL(config["FILO_SMTP_HOST"], port, timeout=10, context=context)
        else:
            client = smtplib.SMTP(config["FILO_SMTP_HOST"], port, timeout=10)
        with client:
            if config["FILO_SMTP_SECURITY"] != "ssl":
                # Credentials and reset links never travel without TLS.
                client.starttls(context=context)
            if config["FILO_SMTP_USERNAME"]:
                client.login(config["FILO_SMTP_USERNAME"], config["FILO_SMTP_PASSWORD"])
            client.send_message(message)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        raise MailDeliveryError("SMTP delivery failed") from exc


def send_safely(to, subject, text, html=None, idempotency_key=None):
    """Background delivery: failures are logged without the recipient or the link."""
    try:
        send_email(to, subject, text, html, idempotency_key)
    except (MailNotConfigured, MailDeliveryError) as exc:
        logger.error("Account email not delivered: %s", exc)
