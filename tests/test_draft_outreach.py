"""Tests for src/lib/draft_outreach.py — artifact-driven, two-mode drafter."""
from __future__ import annotations

from datetime import datetime

import pytest

from src.lib.draft_outreach import (
    DraftError,
    build_dynamic_block,
    build_static_block,
    draft_outreach,
    parse_outreach_response,
)
from src.lib.models import Contact, EmailDraft, LinkedInDraft, ParsedJob, ParsedPost
from src.lib.voice_rules import VoiceConfig


# Reusable helpers for building responses with the new email + linkedin JSON shape.
def _li_block() -> str:
    return (
        '"linkedin": {'
        '"connection_note": "Saw your post on LinkedIn about the role. Builder, GTM operator. Would love to connect.", '
        '"dm": "Thanks for connecting. Saw your post about the role. At Acme Labs I built the self-serve funnel from zero '
        'to 10k signups. Would love 15 min if you are open."'
        '}'
    )


def _make_job() -> ParsedJob:
    return ParsedJob(
        company_name="Acme",
        company_domain="acme.example",
        role_title="Growth Marketing Manager",
        location="Remote",
        jd_body="Hire a growth marketer who can run paid, SEO, and PLG. Technical, SQL, A/B tests.",
        job_url="https://jobs.example.com/acme/growth",
        source_site="ashby",
    )


def _make_post() -> ParsedPost:
    return ParsedPost(
        author_name="Jordan Avery",
        profile_slug="javery",
        post_url="https://www.linkedin.com/posts/javery_hiring-7234",
        post_snippet="We are hiring across finance, growth, and partnerships in 2026. Looking for builders.",
        fetched_at=datetime(2026, 5, 4),
    )


def _make_contact(role_priority: int = 2, source: str = "clay_search") -> Contact:
    return Contact(
        name="Jordan Avery",
        title="Founder",
        company="Acme",
        linkedin_url="https://www.linkedin.com/in/javery",
        email="jordan@acme.example",
        role_priority=role_priority,
        source=source,
    )


def test_build_static_block_includes_profile_pack():
    """Static block must include the profile pack so prompt cache hits across contacts."""
    block = build_static_block(VoiceConfig(signature="Alex"))
    assert "voice.md" in block.lower() or "voice rules" in block.lower()
    assert "past_drafts" in block.lower() or "example" in block.lower()
    # Mode instructions are in the static block (cached)
    assert "post mode" in block.lower()
    assert "jd mode" in block.lower()


def test_build_dynamic_block_post_mode_includes_snippet():
    contact = _make_contact(role_priority=1, source="post_author")
    post = _make_post()
    block = build_dynamic_block(contact=contact, job=None, post=post)
    assert "post mode" in block.lower() or "mode: post" in block.lower()
    assert "We are hiring across finance" in block
    assert "Jordan Avery" in block


def test_build_dynamic_block_jd_mode_includes_jd_body():
    contact = _make_contact(role_priority=2, source="clay_search")
    job = _make_job()
    block = build_dynamic_block(contact=contact, job=job, post=None)
    assert "jd mode" in block.lower() or "mode: jd" in block.lower()
    assert "Growth Marketing Manager" in block
    assert "paid, SEO, and PLG" in block


def test_build_dynamic_block_post_mode_locked_snippet():
    """When post.post_snippet is None (locked LI page), the dynamic block tells the
    model to degrade to a credentialed pitch — never fabricate."""
    contact = _make_contact(role_priority=1, source="post_author")
    locked_post = ParsedPost(
        author_name="Sam Carter",
        profile_slug="samcarter",
        post_url="https://www.linkedin.com/posts/samcarter_x-7299",
        post_snippet=None,
        fetched_at=datetime(2026, 5, 4),
    )
    block = build_dynamic_block(contact=contact, job=None, post=locked_post)
    # Must instruct the model not to fabricate
    assert "do not fabricate" in block.lower() or "no fabrication" in block.lower() or "honest" in block.lower()


def test_parse_outreach_response_extracts_email_and_linkedin():
    raw = (
        '{"email": {"subject": "the growth role at Acme", "body": "Hi Jordan,\\n\\nApplied today...\\n\\nAlex"}, '
        + _li_block()
        + '}'
    )
    email, li = parse_outreach_response(raw)
    assert isinstance(email, EmailDraft)
    assert isinstance(li, LinkedInDraft)
    assert email.subject == "the growth role at Acme"
    assert "Hi Jordan" in email.body
    assert email.word_count > 0
    assert li.connection_note.startswith("Saw your post")
    assert "10k signups" in li.dm


def test_parse_outreach_response_strips_fences():
    raw = (
        '```json\n{"email": {"subject": "x", "body": "Hi y. Body. Alex"}, '
        + _li_block()
        + '}\n```'
    )
    email, li = parse_outreach_response(raw)
    assert email.subject == "x"
    assert li.dm  # non-empty


def test_draft_outreach_jd_mode_passes_voice_rules(monkeypatch):
    """End-to-end: mock the Anthropic call to return a clean draft; verify drafter accepts it."""
    fake_response = (
        '{"email": {"subject": "the growth role at Acme", "body": '
        '"Hi Jordan,\\n\\nJust applied for the Growth Marketing Manager role at Acme. '
        'At Acme Labs I built the self-serve funnel from zero to 10k signups, then ran the lifecycle and paid '
        'experiments end to end to keep activation climbing quarter over quarter. The JD reads like that exact '
        'playbook, so the timing felt worth a note. Would love 15 min if you are open. Happy to send a Loom of one of the systems first.\\n\\n'
        'Alex"}, '
        + _li_block()
        + '}'
    )
    monkeypatch.setattr("src.lib.draft_outreach.call_claude_cached", lambda blocks, model, temperature=0.7: fake_response)
    contact = _make_contact()
    email, li = draft_outreach(contact, job=_make_job(), post=None)
    assert email.subject == "the growth role at Acme"
    assert email.body.strip().endswith("Alex")
    assert "Hi Jordan" in email.body  # salutation
    assert email.word_count >= 50
    assert email.word_count <= 110
    # LI surfaces are present and within length caps
    assert 0 < len(li.connection_note) < 300
    assert 0 < len(li.dm) < 500


def test_draft_outreach_voice_failure_triggers_regen(monkeypatch):
    """First response has an em dash (banned); second is clean. Drafter should regen and accept the second."""
    bad = (
        '{"email": {"subject": "x", "body": "Hi Jordan, I came across your profile. — Alex"}, '
        + _li_block()
        + '}'
    )
    good = (
        '{"email": {"subject": "the growth role at Acme", "body": "Hi Jordan,\\n\\nApplied today for the Growth Marketing Manager role at Acme. '
        'At Acme Labs I shipped the self-serve funnel from zero to 10k signups and ran the lifecycle experiments that kept activation climbing every quarter. '
        'The JD reads like that exact playbook. Would love 15 min if you are open.\\n\\nAlex"}, '
        + _li_block()
        + '}'
    )
    responses = iter([bad, good])
    monkeypatch.setattr("src.lib.draft_outreach.call_claude_cached", lambda blocks, model, temperature=0.7: next(responses))
    email, li = draft_outreach(_make_contact(), job=_make_job(), post=None)
    assert email.subject == "the growth role at Acme"
    assert li.dm


def test_draft_outreach_raises_after_three_failures(monkeypatch):
    fake_bad = (
        '{"email": {"subject": "Application for X at Y", "body": "I am writing to leverage..."}, '
        + _li_block()
        + '}'
    )
    monkeypatch.setattr("src.lib.draft_outreach.call_claude_cached", lambda blocks, model, temperature=0.7: fake_bad)
    with pytest.raises(DraftError):
        draft_outreach(_make_contact(), job=_make_job(), post=None, max_attempts=3)


def test_draft_outreach_requires_job_or_post():
    """Drafter is artifact-driven; with neither artifact, it raises."""
    with pytest.raises(ValueError):
        draft_outreach(_make_contact(), job=None, post=None)
