"""Tests for src/lib/gmail.py — staging Gmail drafts (Step 6 scope only)."""
from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.lib.gmail import _build_mime_message, stage_draft
from src.lib.models import Contact, EmailDraft


def _contact() -> Contact:
    return Contact(
        name="Jordan Avery",
        title="Founder",
        company="Acme",
        email="jordan@acme.example",
        role_priority=2,
    )


def _email() -> EmailDraft:
    return EmailDraft(
        subject="the growth role at Acme",
        body="Body line 1.\n\nBody line 2.\n\nJordan",
        word_count=10,
    )


# --------------------------------------------------------------------------- #
# _build_mime_message                                                         #
# --------------------------------------------------------------------------- #

def test_build_mime_message_sets_to_subject_from_correctly():
    raw_b64 = _build_mime_message(
        to="jordan@acme.example",
        sender="you@example.com",
        subject="the growth role at Acme",
        body="Body.\n\nJordan",
    )
    decoded = base64.urlsafe_b64decode(raw_b64.encode()).decode()
    assert "To: jordan@acme.example" in decoded
    assert "From: you@example.com" in decoded
    assert "Subject: the growth role at Acme" in decoded
    assert "Body." in decoded
    # Body is wrapped in <div> tags for HTML format; check the signature is present.
    assert "Jordan" in decoded


def test_build_mime_message_returns_url_safe_base64():
    raw_b64 = _build_mime_message(
        to="x@example.com", sender="y@example.com", subject="s", body="b\n\nJordan",
    )
    # url-safe base64 uses - and _ instead of + and /, and may have = padding
    assert all(c.isalnum() or c in "-_=" for c in raw_b64)


# --------------------------------------------------------------------------- #
# stage_draft                                                                 #
# --------------------------------------------------------------------------- #

def test_stage_draft_creates_gmail_draft_and_returns_id(monkeypatch):
    fake_service = MagicMock()
    fake_service.users().drafts().create().execute.return_value = {"id": "draft_123"}

    monkeypatch.setattr("src.lib.gmail._get_gmail_service", lambda: fake_service)
    monkeypatch.setattr("src.lib.gmail.load_config", lambda: type("C", (), {"sender_email": "you@example.com", "resume_path": Path("/nonexistent/resume.pdf")})())

    draft_id = stage_draft(_contact(), _email())
    assert draft_id == "draft_123"


def test_stage_draft_raises_if_contact_has_no_email(monkeypatch):
    fake_service = MagicMock()
    monkeypatch.setattr("src.lib.gmail._get_gmail_service", lambda: fake_service)
    monkeypatch.setattr("src.lib.gmail.load_config", lambda: type("C", (), {"sender_email": "you@example.com", "resume_path": Path("/nonexistent/resume.pdf")})())

    contact = _contact()
    contact.email = None
    with pytest.raises(ValueError):
        stage_draft(contact, _email())


def test_stage_draft_raises_if_sender_email_unset(monkeypatch):
    fake_service = MagicMock()
    monkeypatch.setattr("src.lib.gmail._get_gmail_service", lambda: fake_service)
    monkeypatch.setattr("src.lib.gmail.load_config", lambda: type("C", (), {"sender_email": "", "resume_path": Path("/nonexistent/resume.pdf")})())

    with pytest.raises(ValueError):
        stage_draft(_contact(), _email())


# --------------------------------------------------------------------------- #
# Attachment behavior                                                         #
# --------------------------------------------------------------------------- #

def test_build_mime_message_attaches_file_when_path_exists(tmp_path):
    """A real file at attachment_path is added as a MIME attachment."""
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake pdf bytes")

    raw_b64 = _build_mime_message(
        to="x@example.com", sender="y@example.com",
        subject="s", body="b\n\nJordan",
        attachment_path=pdf,
    )
    decoded = base64.urlsafe_b64decode(raw_b64.encode()).decode()
    assert "Content-Disposition: attachment" in decoded
    assert 'filename="resume.pdf"' in decoded
    # MIME type should be detected from .pdf extension
    assert "Content-Type: application/pdf" in decoded


def test_build_mime_message_skips_attachment_when_path_missing(tmp_path):
    """A non-existent attachment_path logs a warning and produces a body-only message."""
    raw_b64 = _build_mime_message(
        to="x@example.com", sender="y@example.com",
        subject="s", body="b\n\nJordan",
        attachment_path=tmp_path / "does-not-exist.pdf",
    )
    decoded = base64.urlsafe_b64decode(raw_b64.encode()).decode()
    assert "Content-Disposition: attachment" not in decoded
    # Body still present (HTML format wraps in <div>; just check signature is in the MIME).
    assert "Jordan" in decoded


def test_build_mime_message_no_attachment_path_gives_plain_message():
    """Without any attachment_path, behavior is identical to pre-attachment code."""
    raw_b64 = _build_mime_message(
        to="x@example.com", sender="y@example.com",
        subject="s", body="b\n\nJordan",
    )
    decoded = base64.urlsafe_b64decode(raw_b64.encode()).decode()
    assert "Content-Disposition: attachment" not in decoded


def test_stage_draft_attaches_resume_from_config(monkeypatch, tmp_path):
    """stage_draft passes cfg.resume_path through to _build_mime_message."""
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    captured = {}

    def fake_create(userId, body):
        captured["raw"] = body["message"]["raw"]
        return MagicMock(execute=lambda: {"id": "draft_with_attachment"})

    fake_service = MagicMock()
    fake_service.users().drafts().create.side_effect = fake_create

    monkeypatch.setattr("src.lib.gmail._get_gmail_service", lambda: fake_service)
    monkeypatch.setattr(
        "src.lib.gmail.load_config",
        lambda: type("C", (), {"sender_email": "you@example.com", "resume_path": pdf})(),
    )

    draft_id = stage_draft(_contact(), _email())
    assert draft_id == "draft_with_attachment"

    decoded = base64.urlsafe_b64decode(captured["raw"].encode()).decode()
    assert 'filename="resume.pdf"' in decoded
    assert "Content-Type: application/pdf" in decoded
