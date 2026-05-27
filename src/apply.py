"""`apply` command — two-phase flow via Click subcommands.

    apply init      First-time setup (Anthropic key, OAuth, Sheet, Profile/).
    apply doctor    Diagnostic checks.

    apply run <job_url> [linkedin_post_url]      Phase 1: parse + save state.
    apply run --resume <run_id> [--dry-run]      Phase 3: draft + stage.

    Phase 2 runs in chat (Claude.ai with Clay MCP), not this CLI.
    See docs/PHASE2.md for the operator manual.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Optional

import click

from src.lib import gmail, parse_post, sheets
from src.lib.config import load_config
from src.lib.draft_outreach import DraftError, draft_outreach
from src.lib.find_contacts import infer_titles
from src.lib.models import Contact, EmailDraft, LinkedInDraft, StagedRow
from src.lib.parse_job import parse_job
from src.lib.run_state import RunState, load_run_state, new_run_id, save_run_state
from src.lib.scheduling import next_send_slot
from src.lib.url_classify import classify_url

log = logging.getLogger(__name__)


@click.group()
def main() -> None:
    """GTM Outreach Agent CLI.

    Subcommands:
      init     First-time setup (Anthropic key, OAuth, Sheet, Profile/).
      doctor   Diagnostic checks for each integration.
      run      Phase 1: parse a job/LinkedIn URL. Phase 3: --resume <run_id>.

    Phase 2 (finding contacts via Clay MCP) is a chat step — see docs/PHASE2.md.
    """


@main.command()
@click.argument("url1", type=str, required=False)
@click.argument("url2", type=str, required=False)
@click.option("--resume", "resume_run_id", type=str, default=None,
              help="Resume a run from state/runs/<run_id>.json (Phase 3 — draft + stage).")
@click.option("--dry-run", is_flag=True, default=False,
              help="In Phase 3, print drafts to stdout instead of staging to Gmail + Sheets.")
def run(
    url1: Optional[str],
    url2: Optional[str],
    resume_run_id: Optional[str],
    dry_run: bool,
) -> None:
    """Phase 1: parse URLs and save run state. Phase 3 (--resume): draft + stage.

    Examples:
        apply run <job_url>
        apply run <linkedin_post_url>
        apply run <job_url> <linkedin_post_url>
        apply run --resume <run_id>
        apply run --resume <run_id> --dry-run
    """
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s | %(message)s")

    if resume_run_id:
        _run_phase_3(resume_run_id, dry_run=dry_run)
        return

    job_url, post_url = _route_urls(url1, url2)
    if not (job_url or post_url):
        click.echo("Error: pass at least one URL (a job page or a LinkedIn post), or --resume <run_id>.", err=True)
        sys.exit(2)

    state = RunState(
        run_id=new_run_id(label="run"),
        parsed_job=None,
        parsed_post=None,
        contacts=[],
        drafts=[],
        status="awaiting_contacts",
    )

    try:
        if job_url:
            click.echo(f"Phase 1: parsing job {job_url}")
            state.parsed_job = parse_job(job_url)
            click.echo(f"  parsed: {state.parsed_job.company_name} - {state.parsed_job.role_title} "
                       f"({state.parsed_job.location or 'no location'})")
            click.echo("Phase 1: inferring hiring-manager titles")
            state.inferred_titles = infer_titles(state.parsed_job)
            click.echo("  titles: " + ", ".join(state.inferred_titles))
            state.run_id = new_run_id(state.parsed_job)

        if post_url:
            click.echo(f"Phase 1: fetching LinkedIn post {post_url}")
            state.parsed_post = parse_post.parse_post(post_url)
            snippet_status = "yes" if state.parsed_post.post_snippet else "NO (locked page)"
            click.echo(f"  author: {state.parsed_post.author_name}; snippet: {snippet_status}")
            if not job_url:
                state.run_id = new_run_id(label=state.parsed_post.profile_slug)
    except Exception as exc:
        log.exception("Phase 1 failed")
        state.status = "failed"
        state.error = str(exc)
        save_run_state(state)
        click.echo(f"FAILED: {exc}", err=True)
        sys.exit(1)

    path = save_run_state(state)

    click.echo("")
    click.echo(f"State saved: {path}")
    click.echo(f"Run id:      {state.run_id}")
    click.echo("")
    click.echo("Next step (Phase 2 — runs in chat, not this CLI):")
    click.echo(f"  Ask Claude in chat: 'find contacts for run {state.run_id}'")
    click.echo("  I'll use the session Clay MCP to find contacts + emails and write them back into the JSON.")
    click.echo("  See docs/phase2-find-contacts.md for the operating manual.")
    click.echo("")
    click.echo("Then return here and run:")
    click.echo(f"  uv run apply run --resume {state.run_id}")


def _route_urls(url1: Optional[str], url2: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Route 1-2 positional URLs into (job_url, post_url) by classify_url."""
    job_url: Optional[str] = None
    post_url: Optional[str] = None
    for u in (url1, url2):
        if not u:
            continue
        kind = classify_url(u)
        if kind == "post" and post_url is None:
            post_url = u
        elif kind == "job" and job_url is None:
            job_url = u
        else:
            click.echo(f"Warning: ignoring duplicate URL of kind {kind!r}: {u}", err=True)
    return job_url, post_url


def _run_phase_3(run_id: str, *, dry_run: bool) -> None:
    """Phase 3: load state, draft, stage Gmail drafts + Sheet rows, flip status to staged."""
    cfg = load_config()
    state = load_run_state(run_id)

    if state.status not in ("ready_to_draft", "staged"):
        click.echo(
            f"Error: state status is {state.status!r}; expected 'ready_to_draft'. "
            "Phase 2 (chat) hasn't run yet — ask Claude in chat to find contacts for this run.",
            err=True,
        )
        sys.exit(2)
    if not state.contacts:
        click.echo("Error: state has no contacts. Did Phase 2 run?", err=True)
        sys.exit(2)
    if state.parsed_job is None and state.parsed_post is None:
        click.echo("Error: state has no parsed_job AND no parsed_post; nothing to draft from.", err=True)
        sys.exit(2)

    click.echo(f"Phase 3: drafting + staging {len(state.contacts)} contact(s)")

    drafts: list[tuple[Contact, EmailDraft, LinkedInDraft]] = []
    for c in state.contacts:
        try:
            if c.source == "post_author" and state.parsed_post is not None:
                email, li = draft_outreach(c, job=None, post=state.parsed_post)
            elif state.parsed_job is not None:
                email, li = draft_outreach(c, job=state.parsed_job, post=None)
            elif state.parsed_post is not None:
                email, li = draft_outreach(c, job=None, post=state.parsed_post)
            else:
                click.echo(f"  ! no artifact for {c.name}; skipping", err=True)
                continue
            drafts.append((c, email, li))
        except DraftError as exc:
            click.echo(f"  ! drafting failed for {c.name}: {exc}", err=True)

    if dry_run:
        click.echo("\n[dry-run] not staging. Drafts:")
        for c, email, li in drafts:
            click.echo(f"--- {c.name} ({c.email or 'no email'}) ---")
            click.echo(f"SUBJECT: {email.subject}")
            click.echo(f"WORDS:   {email.word_count}")
            click.echo(email.body)
            click.echo("")
            click.echo(f"  LI CONNECT ({len(li.connection_note)} chars): {li.connection_note}")
            click.echo(f"  LI DM      ({len(li.dm)} chars): {li.dm}")
            click.echo("")
        click.echo("[dry-run] complete. State not changed.")
        return

    sheets_enabled = bool(cfg.sheet_id)
    if sheets_enabled:
        try:
            sheets.ensure_headers()
        except Exception as exc:
            click.echo(f"  ! Sheet header check failed ({exc}); skipping Sheet integration this run. "
                       f"Common causes: tab name mismatch (current SHEET_TAB_NAME={cfg.sheet_tab_name!r}), "
                       "or sheet not shared with the OAuth account. Gmail drafts will still be staged.",
                       err=True)
            sheets_enabled = False
    else:
        click.echo("  ! SHEET_ID not set; skipping Google Sheet integration. "
                   "Gmail drafts will still be staged. Step 7's send-loop will need a Sheet.")
    next_action_dt = next_send_slot(
        datetime.now(),
        send_days=cfg.send_days,
        window_start=cfg.send_window_start,
        window_end=cfg.send_window_end,
        jitter_min=cfg.send_jitter_min,
        jitter_max=cfg.send_jitter_max,
    )

    state.drafts = []
    for c, email, li in drafts:
        if not c.email:
            click.echo(f"  skipping {c.name}: email_pending (no email)")
            state.drafts.append(_pending_row(c, state, next_action_dt, li=li))
            continue
        try:
            draft_id = gmail.stage_draft(c, email)
        except Exception as exc:
            click.echo(f"  ! gmail draft failed for {c.name}: {exc}", err=True)
            continue
        row = _staged_row(c, state, email, draft_id, next_action_dt, li=li)
        if sheets_enabled:
            try:
                sheets.append_row(row)
            except Exception as exc:
                click.echo(f"  ! sheet append failed for {c.name}: {exc}", err=True)
        state.drafts.append(row)
        click.echo(f"  ✓ {c.name}: gmail_draft={draft_id}")

    if not state.drafts:
        state.status = "failed"
        state.error = "no contacts were staged (all drafting or gmail calls failed)"
    else:
        state.status = "staged"

    save_run_state(state)
    click.echo(f"\nDone. State: {state.status}. Run id: {state.run_id}")


def _staged_row(
    c: Contact, state: RunState, email, draft_id: str, next_action_dt,
    *, li: LinkedInDraft | None = None,
) -> StagedRow:
    job = state.parsed_job
    post = state.parsed_post
    return StagedRow(
        date_added=datetime.now(),
        company=(job.company_name if job else c.company),
        role=(job.role_title if job else "(post outreach)"),
        job_url=(job.job_url if job else (post.post_url if post else "")),
        contact_name=c.name,
        title=c.title,
        email=c.email,
        linkedin=c.linkedin_url,
        status="Drafted",
        next_action="Send Email 1",
        next_action_date=next_action_dt,
        gmail_draft_id=draft_id,
        linkedin_connection_note=(li.connection_note if li else None),
        linkedin_dm=(li.dm if li else None),
    )


def _pending_row(
    c: Contact, state: RunState, next_action_dt,
    *, li: LinkedInDraft | None = None,
) -> StagedRow:
    job = state.parsed_job
    post = state.parsed_post
    return StagedRow(
        date_added=datetime.now(),
        company=(job.company_name if job else c.company),
        role=(job.role_title if job else "(post outreach)"),
        job_url=(job.job_url if job else (post.post_url if post else "")),
        contact_name=c.name,
        title=c.title,
        email=None,
        linkedin=c.linkedin_url,
        status="email_pending",
        next_action="Find email",
        next_action_date=next_action_dt,
        linkedin_connection_note=(li.connection_note if li else None),
        linkedin_dm=(li.dm if li else None),
    )


@main.command()
def init() -> None:
    """First-time setup: Anthropic key, Google OAuth, Sheet, Profile/ scaffold."""
    from src.lib.init_flow import run_init

    sys.exit(run_init())


@main.command()
def doctor() -> None:
    """Diagnostic checks for each integration."""
    from src.lib.doctor import run_doctor

    sys.exit(run_doctor())


if __name__ == "__main__":
    main()
