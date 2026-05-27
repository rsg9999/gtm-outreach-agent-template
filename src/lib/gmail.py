"""Step 6: Gmail draft creation. Step 7 will add send + reply detection."""
from __future__ import annotations

import base64
import html
import logging
import mimetypes
from email.message import EmailMessage
from pathlib import Path

from googleapiclient.discovery import build

from src.lib.config import load_config
from src.lib.google_auth import load_credentials
from src.lib.models import Contact, EmailDraft


def _plain_to_html(body: str) -> str:
    """Convert a plain-text email body to an HTML version that renders correctly
    in Gmail's compose UI.

    Gmail compose strips `<p>` margins, so paragraphs run together visually. The
    structure Gmail itself emits when you press enter twice is:
      <div>paragraph 1</div>
      <div><br></div>
      <div>paragraph 2</div>
      <div><br></div>
    That pattern preserves spacing in both compose and on send.
    """
    paragraphs = [p.strip() for p in body.strip().split("\n\n") if p.strip()]
    parts: list[str] = []
    for i, p in enumerate(paragraphs):
        escaped = html.escape(p).replace("\n", "<br>")
        parts.append(f"<div>{escaped}</div>")
        if i < len(paragraphs) - 1:
            parts.append("<div><br></div>")
    return "".join(parts)

log = logging.getLogger(__name__)


def _get_gmail_service():
    """Return an authorized Gmail API service object. Wrapped so tests can mock cleanly."""
    creds = load_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _attach_file(msg: EmailMessage, path: Path) -> None:
    """Attach a file to an EmailMessage. Detects MIME type from extension."""
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is None:
        mime_type = "application/octet-stream"
    maintype, subtype = mime_type.split("/", 1)
    data = path.read_bytes()
    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)


def _build_mime_message(
    to: str,
    sender: str,
    subject: str,
    body: str,
    *,
    attachment_path: Path | None = None,
) -> str:
    """Build a MIME message and return it base64url-encoded for Gmail API.

    If `attachment_path` is provided AND the file exists, attach it. If the path
    is set but the file is missing, log a warning and proceed without — better to
    send a draft without attachment than to fail the whole stage.
    """
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = sender
    msg["Subject"] = subject
    # HTML-only body. We previously used multipart/alternative (plain + html), but
    # Gmail's compose UI re-derives plain text from HTML on draft open/edit by
    # hard-wrapping at ~70 chars. Those hard wraps then leak through on some
    # mobile rendering. HTML-only avoids the round-trip and keeps the compose UI
    # in rich-text mode.
    msg.set_content(_plain_to_html(body), subtype="html")
    if attachment_path is not None:
        if attachment_path.exists() and attachment_path.is_file():
            _attach_file(msg, attachment_path)
        else:
            log.warning("resume attachment not found at %s; staging draft without it", attachment_path)
    raw = msg.as_bytes()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def stage_draft(contact: Contact, draft: EmailDraft) -> str:
    """Create a Gmail draft addressed to `contact.email`. Attaches the resume from
    `cfg.resume_path` if that file exists. Returns the Gmail draft ID."""
    cfg = load_config()
    if not contact.email:
        raise ValueError(f"contact {contact.name!r} has no email; cannot stage Gmail draft")
    if not cfg.sender_email:
        raise ValueError("SENDER_EMAIL is not set in .env; cannot stage Gmail drafts")

    service = _get_gmail_service()
    raw = _build_mime_message(
        to=contact.email,
        sender=cfg.sender_email,
        subject=draft.subject,
        body=draft.body,
        attachment_path=cfg.resume_path,
    )
    result = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    draft_id = result["id"]
    attached = cfg.resume_path.exists() and cfg.resume_path.is_file()
    log.info(
        "Gmail draft staged: id=%s for contact=%s subject=%r resume=%s",
        draft_id, contact.email, draft.subject,
        cfg.resume_path.name if attached else "MISSING",
    )
    return draft_id


def send_draft(draft_id: str) -> str:
    """Step 7. Placeholder."""
    raise NotImplementedError("send_draft is implemented in Step 7.")


def has_reply(thread_id: str, our_address: str) -> bool:
    """Step 7. Placeholder."""
    raise NotImplementedError("has_reply is implemented in Step 7.")
