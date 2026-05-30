"""Detect — via the Gmail API — that the user sent a staged draft by hand.

The loop depends only on `SendDetector.detect(row, service) -> SendEvent | None`.
Phase 1 ships `PollingSendDetector`; a Pub/Sub `PushSendDetector` can implement the
same contract later without touching the loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from src.lib import gmail
from src.lib.models import StagedRow


class SendDetectionError(RuntimeError):
    """A staged draft disappeared but no matching Sent message was found."""


@dataclass(frozen=True)
class SendEvent:
    message_id: str
    thread_id: str
    sent_at: datetime
    step: str  # "email_1" | "followup_1" | "followup_2"


class SendDetector(Protocol):
    def detect(self, row: StagedRow, service) -> SendEvent | None: ...


def _target(row: StagedRow):
    """Return (step, draft_id, known_ids, thread_id, subject, after) for the active step."""
    known = {x for x in (row.gmail_message_id, row.last_gmail_message_id) if x}
    if row.email_1_sent is None:
        return ("email_1", row.gmail_draft_id, set(), None, row.gmail_subject, row.date_added)
    if row.email_2_sent is None and row.followup_draft_id:
        return ("followup_1", row.followup_draft_id, known, row.gmail_thread_id, row.gmail_subject, row.email_1_sent)
    if row.email_3_sent is None and row.followup_draft_id:
        return ("followup_2", row.followup_draft_id, known, row.gmail_thread_id, row.gmail_subject, row.email_2_sent)
    return (None, None, set(), None, None, None)


class PollingSendDetector:
    """Option A: poll Drafts + Sent through the Gmail API."""

    def detect(self, row: StagedRow, service) -> SendEvent | None:
        step, draft_id, known_ids, thread_id, subject, after = _target(row)
        if draft_id is None:
            return None
        if draft_id in gmail.list_draft_ids(service=service):
            return None  # not sent yet
        candidates = gmail.search_sent(to=row.email, subject=subject, after=after, service=service)
        matches = []
        for mid in candidates:
            if mid in known_ids:
                continue
            meta = gmail.get_message_meta(mid, service=service)
            if thread_id and meta["thread_id"] != thread_id:
                continue
            matches.append((meta["internal_date"], meta["message_id"], meta["thread_id"]))
        if not matches:
            raise SendDetectionError("draft removed; no matching Sent message")
        matches.sort(key=lambda t: t[0])
        sent_at, mid, tid = matches[0]
        return SendEvent(message_id=mid, thread_id=tid, sent_at=sent_at, step=step)
