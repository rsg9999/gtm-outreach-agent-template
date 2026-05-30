"""`run-loop` command: invoked by launchd every 30 minutes.

Each tick:
  1. Read Sheet for rows where Next Action Date <= now and Status not in {Replied, Closed}.
  2. For rows in send window, send queued Gmail draft (with 5-15 min jitter applied at draft-stage time).
  3. After send: update Status, Last Action Date, schedule next follow-up if applicable.
  4. Check Gmail for replies on threads we've sent; mark Replied + Slack alert + stop sequence.
  5. Draft any due follow-ups (D+4 from Email 1 sent, D+5 from Email 2 sent) and stage as Gmail drafts.
  6. At 8am local: post a daily Slack digest (sent / replied / queued today).
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta

import click

from src.lib.models import StagedRow
from src.lib.send_detect import SendEvent

log = logging.getLogger(__name__)

_STEP_SENT_COLUMN = {"email_1": "Email 1 Sent", "followup_1": "Email 2 Sent", "followup_2": "Email 3 Sent"}
_STEP_STATUS = {"email_1": "Email 1 Sent", "followup_1": "Follow-up 1 Sent", "followup_2": "Follow-up 2 Sent"}


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


@click.command()
@click.option("--dry-run", is_flag=True, default=False, help="Print actions instead of sending or writing.")
def main(dry_run: bool) -> None:
    """One tick of the send/reply/follow-up loop."""
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    log.info("run-loop tick: dry_run=%s", dry_run)
    click.echo("run-loop: scaffold present. Behaviors will be implemented in Step 7.")
    sys.exit(0)


if __name__ == "__main__":
    main()
