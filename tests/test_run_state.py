"""Tests for src/lib/run_state.py — the end-of-run record written once by the linear apply pipeline."""
from __future__ import annotations


import pytest

from src.lib.models import Contact, ParsedJob
from src.lib.run_state import (
    RunState,
    load_run_state,
    new_run_id,
    runs_dir,
    save_run_state,
)


def _make_job() -> ParsedJob:
    return ParsedJob(
        company_name="ExampleCo",
        company_domain="exampleco.example",
        role_title="GTM Engineer",
        location="Remote",
        jd_body="Hire a GTM engineer.",
        job_url="https://example.com/role",
        source_site="lever",
    )


def _make_contact(name: str = "Asha Patel", title: str = "Head of GTM") -> Contact:
    return Contact(
        name=name,
        title=title,
        company="ExampleCo",
        role_priority=2,
        source="clay_search",
    )


def test_contact_drops_legacy_research_fields():
    """The new Contact must not carry manual_notes, personalization_hook, recent_li_activity,
    low_confidence, or is_connected — those were the research-mining inputs we killed."""
    c = Contact(name="X", title="Y", company="Z", source="clay_search")
    for field in ("manual_notes", "personalization_hook", "recent_li_activity",
                  "low_confidence", "is_connected"):
        assert not hasattr(c, field), f"Contact should not have field {field!r} after redesign"


def test_parsed_post_round_trips():
    """ParsedPost is a new model added for the post-fetcher output."""
    from src.lib.models import ParsedPost
    from datetime import datetime
    p = ParsedPost(
        author_name="Jordan Avery",
        profile_slug="javery",
        post_url="https://www.linkedin.com/posts/javery_hiring-activity-1234",
        post_snippet="We're hiring across finance, growth, and partnerships in 2026.",
        fetched_at=datetime(2026, 5, 4, 10, 0, 0),
    )
    blob = p.model_dump_json()
    p2 = ParsedPost.model_validate_json(blob)
    assert p2.author_name == "Jordan Avery"
    assert p2.post_snippet.startswith("We're hiring")


def test_new_run_id_includes_timestamp_and_company_slug():
    rid = new_run_id(_make_job())
    # Looks like 20260501T120000-exampleco-gtm-engineer
    assert "exampleco" in rid
    assert "gtm-engineer" in rid


def test_new_run_id_is_unique_per_call():
    a = new_run_id(_make_job())
    b = new_run_id(_make_job())
    # Even with millisecond clocks the suffix differs.
    assert a != b


def test_runs_dir_creates_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR_OVERRIDE", str(tmp_path / "state" / "runs"))
    d = runs_dir()
    assert d.exists()
    assert d.is_dir()


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR_OVERRIDE", str(tmp_path / "state" / "runs"))
    state = RunState(
        run_id="test-run-1",
        parsed_job=_make_job(),
        parsed_post=None,
        inferred_titles=["Head of GTM", "VP Growth"],
        contacts=[_make_contact()],
        drafts=[],
        status="staged",
    )
    path = save_run_state(state)
    assert path.exists()
    loaded = load_run_state("test-run-1")
    assert loaded.run_id == "test-run-1"
    assert loaded.parsed_job.company_name == "ExampleCo"
    assert loaded.parsed_post is None
    assert loaded.inferred_titles == ["Head of GTM", "VP Growth"]
    assert len(loaded.contacts) == 1
    assert loaded.contacts[0].name == "Asha Patel"
    assert loaded.contacts[0].source == "clay_search"
    assert loaded.status == "staged"
    assert loaded.error is None


def test_save_run_state_with_post(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR_OVERRIDE", str(tmp_path / "state" / "runs"))
    from datetime import datetime
    from src.lib.models import ParsedPost
    state = RunState(
        run_id="post-run",
        parsed_job=None,
        parsed_post=ParsedPost(
            author_name="Jordan Avery",
            profile_slug="javery",
            post_url="https://www.linkedin.com/posts/javery_hiring-7234",
            post_snippet="Hiring across growth and partnerships.",
            fetched_at=datetime(2026, 5, 4),
        ),
        contacts=[],
        drafts=[],
        status="staged",
    )
    save_run_state(state)
    loaded = load_run_state("post-run")
    assert loaded.parsed_post.author_name == "Jordan Avery"
    assert loaded.parsed_post.profile_slug == "javery"
    assert loaded.parsed_post.post_snippet == "Hiring across growth and partnerships."
    assert loaded.parsed_job is None
    assert loaded.status == "staged"


def test_save_run_state_failed_carries_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR_OVERRIDE", str(tmp_path / "state" / "runs"))
    state = RunState(
        run_id="fail-run",
        parsed_job=_make_job(),
        contacts=[],
        drafts=[],
        status="failed",
        error="Clay returned no contacts for exampleco.example",
    )
    save_run_state(state)
    loaded = load_run_state("fail-run")
    assert loaded.status == "failed"
    assert "Clay" in loaded.error


def test_load_run_state_raises_for_missing_id(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR_OVERRIDE", str(tmp_path / "state" / "runs"))
    with pytest.raises(FileNotFoundError):
        load_run_state("does-not-exist")
