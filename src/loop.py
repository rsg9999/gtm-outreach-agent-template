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

import click

log = logging.getLogger(__name__)


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
