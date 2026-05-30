"""Step 6: Gmail draft creation. Step 7 will add send + reply detection."""
from __future__ import annotations

import base64
import html
import logging
import mimetypes
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from googleapiclient.discovery import build

from src.lib.config import load_config
from src.lib.google_auth import load_credentials
from src.lib.models import Contact, EmailDraft, InboundMessage


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


# --------------------------------------------------------------------------- #
# Step 7 Gmail fetch primitives                                               #
# --------------------------------------------------------------------------- #

def list_draft_ids(service=None) -> set[str]:
    """All current Gmail draft IDs for the user (paginated)."""
    service = service or _get_gmail_service()
    ids: set[str] = set()
    token = None
    while True:
        resp = service.users().drafts().list(userId="me", pageToken=token).execute()
        ids.update(d["id"] for d in resp.get("drafts", []))
        token = resp.get("nextPageToken")
        if not token:
            return ids


def _header(headers: list[dict], name: str) -> str | None:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


def get_draft_subject(draft_id: str, service=None) -> str | None:
    """Subject of a staged draft, cached before the draft can disappear on send."""
    service = service or _get_gmail_service()
    resp = service.users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
    headers = resp.get("message", {}).get("payload", {}).get("headers", [])
    return _header(headers, "Subject")


def get_message_meta(message_id: str, service=None) -> dict:
    """Lightweight message metadata: ids + naive-local sent datetime + Message-ID header."""
    service = service or _get_gmail_service()
    resp = service.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["Message-ID", "Subject", "From", "To"],
    ).execute()
    internal_ms = int(resp.get("internalDate", "0"))
    headers = resp.get("payload", {}).get("headers", [])
    return {
        "message_id": resp.get("id", message_id),
        "thread_id": resp.get("threadId"),
        "internal_date": datetime.fromtimestamp(internal_ms / 1000),
        "rfc_message_id": _header(headers, "Message-ID"),
    }


def search_sent(to: str, subject: str | None, after: datetime | None = None, service=None) -> list[str]:
    """Message IDs in Sent matching recipient (+ optional subject, + optional after-date)."""
    service = service or _get_gmail_service()
    q = f"in:sent to:{to}"
    if subject:
        q += f' subject:"{subject}"'
    if after:
        q += f" after:{after:%Y/%m/%d}"
    resp = service.users().messages().list(userId="me", q=q).execute()
    return [m["id"] for m in resp.get("messages", [])]


def create_reply_draft(
    *, thread_id: str, to: str, subject: str, body: str,
    in_reply_to: str | None = None, references: str | None = None, service=None,
) -> str:
    """Stage a reply draft inside an existing thread. NEVER sends. Returns the draft ID."""
    service = service or _get_gmail_service()
    cfg = load_config()
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = cfg.sender_email
    msg["Subject"] = reply_subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(_plain_to_html(body), subtype="html")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    result = service.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": thread_id}}
    ).execute()
    draft_id = result["id"]
    log.info("Gmail follow-up reply draft staged: id=%s thread=%s to=%s", draft_id, thread_id, to)
    return draft_id


def _extract_text_body(payload: dict) -> str:
    """Decode the text/plain body from a Gmail message payload (full format)."""
    def _decode(b64data: str) -> str:
        return base64.urlsafe_b64decode(b64data.encode("ascii")).decode("utf-8", errors="replace")

    parts = payload.get("parts")
    if parts:
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data")
                if data:
                    return _decode(data)
        # nested multipart: recurse into the first part that has its own parts
        for part in parts:
            if part.get("parts"):
                nested = _extract_text_body(part)
                if nested:
                    return nested
        return ""
    data = payload.get("body", {}).get("data")
    return _decode(data) if data else ""


def get_message_body(message_id: str, service=None) -> tuple[str, int]:
    """Fetch one message in full format; return (decoded text/plain body, internalDate ms)."""
    service = service or _get_gmail_service()
    resp = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    return _extract_text_body(resp["payload"]), int(resp["internalDate"])


def get_latest_inbound(thread_id: str, our_address: str, service=None) -> InboundMessage | None:
    """Return the most recent thread message whose From is NOT our_address, or None."""
    service = service or _get_gmail_service()
    resp = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    ours = our_address.lower()
    inbound = [
        m for m in resp.get("messages", [])
        if ours not in (_header(m["payload"].get("headers", []), "From") or "").lower()
    ]
    if not inbound:
        return None
    latest = max(inbound, key=lambda m: int(m["internalDate"]))
    headers_list = latest["payload"].get("headers", [])
    headers = {h["name"].lower(): h["value"] for h in headers_list}
    return InboundMessage(
        sender=_header(headers_list, "From") or "",
        subject=_header(headers_list, "Subject") or "",
        headers=headers,
        body=_extract_text_body(latest["payload"]),
        internal_date_ms=int(latest["internalDate"]),
    )
