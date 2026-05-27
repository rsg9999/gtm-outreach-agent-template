"""Tests for src/lib/find_contacts.py — title inference only."""
from __future__ import annotations

from src.lib.find_contacts import _dedupe_case_insensitive, infer_titles
from src.lib.models import ParsedJob


def _make_job() -> ParsedJob:
    return ParsedJob(
        company_name="Acme",
        role_title="GTM Engineer",
        jd_body="Hire a GTM engineer who knows Clay.",
        job_url="https://example.com/role",
    )


def test_dedupe_case_insensitive_keeps_first_form():
    assert _dedupe_case_insensitive(["Head of GTM", "head of gtm", "VP Growth"]) == [
        "Head of GTM",
        "VP Growth",
    ]


def test_dedupe_case_insensitive_drops_blanks():
    assert _dedupe_case_insensitive([" ", "", "Head of GTM"]) == ["Head of GTM"]


def test_infer_titles_uses_haiku_and_returns_max_six(monkeypatch):
    """Mock the Anthropic call. Verify the result is deduped and capped at 6."""
    fake_payload = '["Head of GTM", "head of gtm", "VP Growth", "Director of RevOps", "GTM Lead", "CMO", "Head of Marketing", "Founder"]'
    monkeypatch.setattr("src.lib.find_contacts.call_claude", lambda prompt, model: fake_payload)
    titles = infer_titles(_make_job())
    assert len(titles) <= 6
    assert "Head of GTM" in titles
    # dedupe killed the lowercase variant
    assert "head of gtm" not in titles
