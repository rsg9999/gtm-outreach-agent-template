"""`run-loop`: one idempotent Step 7 tick. NEVER sends; only drafts().create.

Each row: cache the draft subject, detect a manual send via the Gmail API, record it
and schedule the next follow-up, then stage a due follow-up as a reply draft. Per-row
errors are isolated to the row's Step7 Error column so one bad row can't crash the tick.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta

import click

from src.lib.config import load_config
from src.lib.followups import select_bump
from src.lib.gmail import create_reply_draft, get_draft_subject, _get_gmail_service
from src.lib.models import StagedRow
from src.lib.profile import load_followup_pools
from src.lib.send_detect import PollingSendDetector, SendEvent
from src.lib.sheets import ensure_step7_headers, read_queue, update_row

log = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"Replied", "Closed", "Done"}


def _gmail_service():
    """Indirection so tests patch this instead of the network."""
    return _get_gmail_service()


def make_detector():
    """The Phase-1 detector. Swap for PushSendDetector later without touching the loop."""
    return PollingSendDetector()


_STEP_SENT_COLUMN = {"email_1": "Email 1 Sent", "followup_1": "Email 2 Sent", "followup_2": "Email 3 Sent"}
_STEP_STATUS = {"email_1": "Email 1 Sent", "followup_1": "Follow-up 1 Sent", "followup_2": "Follow-up 2 Sent"}
_STEP_LABEL = {"followup_1": "Send Follow-up 1", "followup_2": "Send Follow-up 2"}


def record_send_fields(row: StagedRow, event: SendEvent, cfg) -> dict:
    """Field changes (keyed by SHEET_HEADERS name) to persist when a send is detected."""
    fields: dict = {
        "Status": _STEP_STATUS[event.step],
        _STEP_SENT_COLUMN[event.step]: event.sent_at,
        "Last Gmail Message ID": event.message_id,
    }
    if event.step == "email_1":
        fields["Gmail Message ID"] = event.message_id
        fields["Gmail Thread ID"] = event.thread_id
        fields["Gmail Draft ID"] = ""  # consumed
        fields["Next Action"] = "Send Follow-up 1"
        fields["Next Action Date"] = event.sent_at + timedelta(days=cfg.followup_1_days)
    else:
        fields["Follow-up Sent?"] = True
        fields["Follow-up Date"] = event.sent_at
        fields["Followup Draft ID"] = ""  # consumed
        if event.step == "followup_1":
            fields["Next Action"] = "Send Follow-up 2"
            fields["Next Action Date"] = event.sent_at + timedelta(days=cfg.followup_2_days)
        else:  # followup_2 — terminal, no more follow-ups
            fields["Next Action"] = "Done"
    return fields


def followup_step(row: StagedRow) -> str | None:
    """Which follow-up is next for this row, or None if none applies."""
    if row.email_1_sent and row.email_2_sent is None:
        return "followup_1"
    if row.email_2_sent and row.email_3_sent is None:
        return "followup_2"
    return None


def followup_due(row: StagedRow, *, now: datetime) -> bool:
    """True when a follow-up should be staged: a step applies, the due date has passed,
    no follow-up draft is already waiting, and the thread is known."""
    if followup_step(row) is None:
        return False
    if row.followup_draft_id:        # already staged, waiting for manual send
        return False
    if not row.gmail_thread_id:      # need a thread to reply into
        return False
    if row.next_action_date is None or now < row.next_action_date:
        return False
    return True


def _cache_subject_fields(row: StagedRow, service) -> dict:
    """If the first-email draft is still around and its subject isn't cached, cache it."""
    if row.email_1_sent is None and row.gmail_draft_id and not row.gmail_subject:
        subject = get_draft_subject(row.gmail_draft_id, service=service)
        if subject:
            row.gmail_subject = subject  # so detection later this tick can use it
            return {"Gmail Subject": subject}
    return {}


def _stage_followup_fields(row: StagedRow, pools: dict, service) -> dict:
    """Stage a follow-up reply draft and return the field change. Caller checks due-ness."""
    step = followup_step(row)
    pool = pools.get(step or "", [])
    if not pool:
        return {"Step7 Error": f"empty follow-up pool for {step}"}
    body = select_bump(pool, row.email or row.contact_name, step)
    draft_id = create_reply_draft(
        thread_id=row.gmail_thread_id,
        to=row.email,
        subject=row.gmail_subject or row.role,
        body=body,
        in_reply_to=None,
        references=None,
        service=service,
    )
    return {"Followup Draft ID": draft_id, "Next Action": _STEP_LABEL[step]}


def run_tick(*, now: datetime | None = None, dry_run: bool = False) -> None:
    now = now or datetime.now()
    cfg = load_config()
    service = _gmail_service()
    detector = make_detector()
    pools = load_followup_pools() if cfg.enable_followups else {}

    for row_number, row in read_queue():
        if row.status in _TERMINAL_STATUSES or row.replied:
            continue
        changed: dict = {}
        try:
            changed.update(_cache_subject_fields(row, service))
            event: SendEvent | None = detector.detect(row, service)
            if event is not None:
                # A send was just detected; record it and schedule the next follow-up
                # (written below). Do NOT also stage a follow-up this same tick — the
                # row's in-memory next_action_date is still the prior (often past) value,
                # and the next follow-up is by definition in the future. The next tick
                # stages it once the row reflects the freshly-written Next Action Date.
                changed.update(record_send_fields(row, event, cfg))
            elif cfg.enable_followups and followup_due(row, now=now):
                changed.update(_stage_followup_fields(row, pools, service))
        except Exception as exc:  # isolate the row, keep the tick going
            changed = {"Step7 Error": f"{type(exc).__name__}: {exc}"[:300]}
            log.warning("row %d failed: %s", row_number, exc)
        if changed:
            if dry_run:
                click.echo(f"[dry-run] row {row_number}: {changed}")
            else:
                update_row(row_number, changed)


@click.command()
@click.option("--dry-run", is_flag=True, default=False, help="Print planned writes; change nothing.")
@click.option("--init-headers", is_flag=True, default=False, help="Add the Step 7 columns to the tab, then exit.")
def main(dry_run: bool, init_headers: bool) -> None:
    """One tick of the Step 7 send-detection + follow-up loop. Never sends."""
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    if init_headers:
        ensure_step7_headers()
        click.echo("Step 7 headers ensured.")
        sys.exit(0)
    log.info("run-loop tick: dry_run=%s", dry_run)
    run_tick(dry_run=dry_run)
    sys.exit(0)


if __name__ == "__main__":
    main()
