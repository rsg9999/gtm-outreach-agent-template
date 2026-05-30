"""Tests for the Step 7 send-detection seam (polling implementation)."""
from __future__ import annotations

from datetime import datetime

import pytest

from src.lib.models import StagedRow
from src.lib.send_detect import PollingSendDetector, SendDetectionError, SendEvent


def _row(**over) -> StagedRow:
    base = dict(
        date_added=datetime(2026, 5, 5, 10, 0), company="Acme", role="GMM",
        job_url="https://x", contact_name="Jordan Avery", title="Founder",
        email="jordan@acme.example", status="Drafted", gmail_draft_id="draft_1",
        gmail_subject="the growth role at Acme",
    )
    base.update(over)
    return StagedRow(**base)


def _gmail_stub(monkeypatch, *, open_drafts, sent_ids, metas):
    import src.lib.gmail as g
    monkeypatch.setattr(g, "list_draft_ids", lambda service=None: set(open_drafts))
    monkeypatch.setattr(g, "search_sent", lambda to, subject, after=None, service=None: list(sent_ids))
    monkeypatch.setattr(g, "get_message_meta", lambda mid, service=None: metas[mid])


def test_no_event_when_draft_still_open(monkeypatch):
    _gmail_stub(monkeypatch, open_drafts={"draft_1"}, sent_ids=[], metas={})
    assert PollingSendDetector().detect(_row(), service=None) is None


def test_email1_detected_when_draft_gone_and_sent_match(monkeypatch):
    metas = {"m1": {"message_id": "m1", "thread_id": "t1", "internal_date": datetime(2026, 5, 6, 8, 0)}}
    _gmail_stub(monkeypatch, open_drafts=set(), sent_ids=["m1"], metas=metas)
    ev = PollingSendDetector().detect(_row(), service=None)
    assert ev == SendEvent(message_id="m1", thread_id="t1", sent_at=datetime(2026, 5, 6, 8, 0), step="email_1")


def test_gone_with_no_match_raises(monkeypatch):
    _gmail_stub(monkeypatch, open_drafts=set(), sent_ids=[], metas={})
    with pytest.raises(SendDetectionError):
        PollingSendDetector().detect(_row(), service=None)


def test_followup1_detected_in_thread_excludes_known(monkeypatch):
    row = _row(
        status="Email 1 Sent", email_1_sent=datetime(2026, 5, 6, 8, 0),
        gmail_message_id="m1", last_gmail_message_id="m1", gmail_thread_id="t1",
        gmail_draft_id=None, followup_draft_id="fdraft_1",
    )
    metas = {
        "m1": {"message_id": "m1", "thread_id": "t1", "internal_date": datetime(2026, 5, 6, 8, 0)},
        "m2": {"message_id": "m2", "thread_id": "t1", "internal_date": datetime(2026, 5, 10, 9, 0)},
    }
    _gmail_stub(monkeypatch, open_drafts=set(), sent_ids=["m1", "m2"], metas=metas)
    ev = PollingSendDetector().detect(row, service=None)
    assert ev.message_id == "m2" and ev.step == "followup_1" and ev.thread_id == "t1"
