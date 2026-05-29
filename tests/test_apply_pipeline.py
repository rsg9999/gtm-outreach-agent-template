"""End-to-end tests for the two-phase apply CLI.

Phase 1 (apply <url>):
  - parses artifacts (JD via parse_job, post via parse_post)
  - infers titles (JD only)
  - writes run state with status="awaiting_contacts"
  - does NOT touch Clay, Gmail, or Sheets

Phase 2 (chat — me) is not testable from the CLI; it's a manual operator step
that mutates the state JSON outside this process.

Phase 3 (apply --resume <run_id>):
  - reads run state (must be status="ready_to_draft" with contacts populated)
  - drafts per contact
  - stages to Gmail + Sheets (or prints if --dry-run)
  - flips status to "staged"
"""
from __future__ import annotations

from datetime import datetime

import pytest
from click.testing import CliRunner

from src.apply import main
from src.lib.models import Contact, EmailDraft, LinkedInDraft, ParsedJob, ParsedPost
from src.lib.run_state import RunState, save_run_state


def _fake_email_li() -> tuple[EmailDraft, LinkedInDraft]:
    return (
        EmailDraft(
            subject="the growth role at Acme",
            body="Hi Jordan,\n\nJust applied. At Acme Labs I built the self-serve funnel from zero to 10k signups. Would love 15 min.\n\nAlex",
            word_count=22,
        ),
        LinkedInDraft(
            connection_note="Saw your post. Builder, GTM operator. Would love to connect.",
            dm="Thanks for connecting. Saw your post. At Acme Labs I built the self-serve funnel from zero to 10k signups. Would love 15 min.",
        ),
    )


@pytest.fixture
def mock_externals(monkeypatch, tmp_path):
    """Mock parse_job, parse_post, infer_titles, draft_outreach, gmail, sheets.

    apply.py imports parse_job/infer_titles/draft_outreach as functions (patch via src.apply.X)
    and gmail/parse_post/sheets as modules (patch via src.lib.<module>.<func>).
    """
    monkeypatch.setenv("RUNS_DIR_OVERRIDE", str(tmp_path / "state" / "runs"))
    monkeypatch.setenv("SHEET_ID", "test-sheet-id")  # enables Sheets path in apply.py
    monkeypatch.setenv("SENDER_EMAIL", "you@example.com")  # required by gmail.stage_draft

    recorder = {"gmail_drafts": [], "sheet_rows": []}

    monkeypatch.setattr(
        "src.apply.parse_job",
        lambda url: ParsedJob(
            company_name="Acme",
            company_domain="acme.example",
            role_title="Growth Marketing Manager",
            jd_body="Hire a growth marketer.",
            job_url=url,
            source_site="ashby",
        ),
    )
    monkeypatch.setattr("src.apply.infer_titles", lambda job: ["Head of Growth", "VP Marketing"])
    monkeypatch.setattr(
        "src.lib.parse_post.parse_post",
        lambda url: ParsedPost(
            author_name="Jordan Avery",
            profile_slug="javery",
            post_url=url,
            post_snippet="Hiring across growth and partnerships.",
            fetched_at=datetime(2026, 5, 5),
        ),
    )
    monkeypatch.setattr(
        "src.apply.draft_outreach",
        lambda contact, *, job=None, post=None, max_attempts=3: _fake_email_li(),
    )

    def fake_stage_draft(contact, draft):
        recorder["gmail_drafts"].append((contact.email, draft.subject))
        return f"draft-{len(recorder['gmail_drafts'])}"

    def fake_append_row(row):
        recorder["sheet_rows"].append(row)
        return len(recorder["sheet_rows"])

    monkeypatch.setattr("src.lib.gmail.stage_draft", fake_stage_draft)
    monkeypatch.setattr("src.lib.sheets.append_row", fake_append_row)
    monkeypatch.setattr("src.lib.sheets.ensure_headers", lambda: None)
    return recorder


# ---------- Phase 1 tests ----------


def test_phase1_jd_url_writes_awaiting_contacts_state(mock_externals, tmp_path):
    """Phase 1 with a JD URL: parses, infers titles, writes state — no Gmail/Sheets calls."""
    runner = CliRunner()
    result = runner.invoke(main, ["run", "https://jobs.ashbyhq.com/acme/growth"])
    assert result.exit_code == 0, result.output

    # No staging happened
    assert mock_externals["gmail_drafts"] == []
    assert mock_externals["sheet_rows"] == []

    # Exactly one state file was written
    runs = list((tmp_path / "state" / "runs").glob("*.json"))
    assert len(runs) == 1

    from src.lib.run_state import load_run_state
    run_id = runs[0].stem
    state = load_run_state(run_id)
    assert state.status == "awaiting_contacts"
    assert state.parsed_job is not None
    assert state.parsed_job.company_name == "Acme"
    assert state.contacts == []
    assert state.inferred_titles == ["Head of Growth", "VP Marketing"]


def test_phase1_post_url_writes_awaiting_contacts_state(mock_externals, tmp_path):
    """Phase 1 with a post URL: fetches post, writes state with parsed_post."""
    runner = CliRunner()
    result = runner.invoke(main, ["run", "https://www.linkedin.com/posts/javery_hiring-7234"])
    assert result.exit_code == 0, result.output

    assert mock_externals["gmail_drafts"] == []

    runs = list((tmp_path / "state" / "runs").glob("*.json"))
    assert len(runs) == 1

    from src.lib.run_state import load_run_state
    state = load_run_state(runs[0].stem)
    assert state.status == "awaiting_contacts"
    assert state.parsed_post is not None
    assert state.parsed_post.author_name == "Jordan Avery"
    assert state.parsed_job is None


def test_phase1_both_urls_populates_both(mock_externals, tmp_path):
    """Phase 1 with both URLs: parses both."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            "https://jobs.ashbyhq.com/acme/growth",
            "https://www.linkedin.com/posts/javery_hiring-7234",
        ],
    )
    assert result.exit_code == 0, result.output

    runs = list((tmp_path / "state" / "runs").glob("*.json"))
    assert len(runs) == 1

    from src.lib.run_state import load_run_state
    state = load_run_state(runs[0].stem)
    assert state.parsed_job is not None
    assert state.parsed_post is not None


def test_phase1_no_urls_exits_with_error(mock_externals):
    runner = CliRunner()
    result = runner.invoke(main, ["run"])
    assert result.exit_code != 0


# ---------- Phase 3 tests (--resume) ----------


def _seed_ready_state(tmp_path, monkeypatch, *, with_post=False) -> str:
    """Write a state file with contacts already populated (simulating Phase 2 having run)."""
    monkeypatch.setenv("RUNS_DIR_OVERRIDE", str(tmp_path / "state" / "runs"))
    contacts = [
        Contact(
            name="Jordan Avery",
            title="Founder",
            company="Acme",
            linkedin_url="https://www.linkedin.com/in/javery",
            email="jordan@acme.example",
            role_priority=2,
            source="clay_search",
        ),
    ]
    state = RunState(
        run_id="test-resume-1",
        parsed_job=ParsedJob(
            company_name="Acme",
            company_domain="acme.example",
            role_title="Growth Marketing Manager",
            jd_body="Hire a growth marketer.",
            job_url="https://jobs.example.com/acme/growth",
            source_site="ashby",
        ),
        parsed_post=(
            ParsedPost(
                author_name="Jordan Avery",
                profile_slug="javery",
                post_url="https://www.linkedin.com/posts/javery_hiring-7234",
                post_snippet="Hiring across growth and partnerships.",
                fetched_at=datetime(2026, 5, 5),
            ) if with_post else None
        ),
        inferred_titles=["Head of Growth"],
        contacts=contacts,
        drafts=[],
        status="ready_to_draft",
    )
    save_run_state(state)
    return state.run_id


def test_phase3_resume_drafts_and_stages(mock_externals, monkeypatch, tmp_path):
    run_id = _seed_ready_state(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--resume", run_id])
    assert result.exit_code == 0, result.output
    assert len(mock_externals["gmail_drafts"]) == 1
    assert mock_externals["gmail_drafts"][0][0] == "jordan@acme.example"
    assert len(mock_externals["sheet_rows"]) == 1

    from src.lib.run_state import load_run_state
    state = load_run_state(run_id)
    assert state.status == "staged"


def test_phase3_dry_run_does_not_stage(mock_externals, monkeypatch, tmp_path):
    run_id = _seed_ready_state(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--resume", run_id, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert mock_externals["gmail_drafts"] == []
    assert mock_externals["sheet_rows"] == []

    # State stays at ready_to_draft (dry-run doesn't change it)
    from src.lib.run_state import load_run_state
    state = load_run_state(run_id)
    assert state.status == "ready_to_draft"


def test_phase3_refuses_when_status_is_awaiting_contacts(mock_externals, monkeypatch, tmp_path):
    """If Phase 2 hasn't run yet, --resume should refuse to proceed."""
    monkeypatch.setenv("RUNS_DIR_OVERRIDE", str(tmp_path / "state" / "runs"))
    state = RunState(
        run_id="not-ready-yet",
        parsed_job=ParsedJob(
            company_name="Acme",
            role_title="X",
            jd_body="...",
            job_url="https://example.com",
        ),
        contacts=[],
        drafts=[],
        status="awaiting_contacts",
    )
    save_run_state(state)
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--resume", "not-ready-yet"])
    assert result.exit_code != 0
    assert "ready_to_draft" in result.output or "Phase 2" in result.output


def test_phase3_post_author_uses_post_mode(mock_externals, monkeypatch, tmp_path):
    """A contact with source='post_author' is drafted in post mode (post=parsed_post)."""
    captured = {}

    def fake_draft(contact, *, job=None, post=None, max_attempts=3):
        captured["job"] = job
        captured["post"] = post
        return (
            EmailDraft(subject="x", body="Hi y. body. Alex", word_count=4),
            LinkedInDraft(connection_note="hi", dm="yo"),
        )

    monkeypatch.setattr("src.apply.draft_outreach", fake_draft)

    monkeypatch.setenv("RUNS_DIR_OVERRIDE", str(tmp_path / "state" / "runs"))
    contacts = [
        Contact(
            name="Jordan Avery",
            title="Founder",
            company="Acme",
            linkedin_url="https://www.linkedin.com/in/javery",
            email="jordan@acme.example",
            role_priority=1,
            source="post_author",
        ),
    ]
    state = RunState(
        run_id="post-resume",
        parsed_job=None,
        parsed_post=ParsedPost(
            author_name="Jordan Avery",
            profile_slug="javery",
            post_url="https://www.linkedin.com/posts/javery_hiring-7234",
            post_snippet="Hiring across growth.",
            fetched_at=datetime(2026, 5, 5),
        ),
        contacts=contacts,
        drafts=[],
        status="ready_to_draft",
    )
    save_run_state(state)

    runner = CliRunner()
    result = runner.invoke(main, ["run", "--resume", "post-resume", "--dry-run"])
    assert result.exit_code == 0, result.output

    # Drafter was called in post-mode
    assert captured["job"] is None
    assert captured["post"] is not None
    assert captured["post"].author_name == "Jordan Avery"
