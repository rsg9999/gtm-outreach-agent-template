"""Step 7 loop helpers + tick."""
from __future__ import annotations

from datetime import datetime

from src.lib.models import StagedRow
from src.lib.send_detect import SendEvent
import src.loop as loop_mod
from src.loop import record_send_fields, followup_due, followup_step


def _row(**over) -> StagedRow:
    base = dict(
        date_added=datetime(2026, 5, 5, 10, 0), company="Acme", role="GMM",
        job_url="https://x", contact_name="Jordan Avery", title="Founder",
        email="jordan@acme.example", status="Drafted", gmail_draft_id="draft_1",
        gmail_subject="the growth role at Acme",
    )
    base.update(over)
    return StagedRow(**base)


def test_record_send_fields_email_1(monkeypatch):
    cfg = type("C", (), {"followup_1_days": 4, "followup_2_days": 9})()
    ev = SendEvent(message_id="m1", thread_id="t1", sent_at=datetime(2026, 5, 6, 8, 0), step="email_1")
    fields = record_send_fields(_row(), ev, cfg)
    assert fields["Status"] == "Email 1 Sent"
    assert fields["Gmail Message ID"] == "m1"
    assert fields["Gmail Thread ID"] == "t1"
    assert fields["Last Gmail Message ID"] == "m1"
    assert fields["Email 1 Sent"] == datetime(2026, 5, 6, 8, 0)
    assert fields["Next Action Date"] == datetime(2026, 5, 10, 8, 0)  # +4 days
    assert fields["Next Action"] == "Send Follow-up 1"
    assert fields["Gmail Draft ID"] == ""  # consumed


def test_record_send_fields_followup_1(monkeypatch):
    cfg = type("C", (), {"followup_1_days": 4, "followup_2_days": 9})()
    row = _row(status="Email 1 Sent", email_1_sent=datetime(2026, 5, 6, 8, 0),
               gmail_message_id="m1", last_gmail_message_id="m1", gmail_thread_id="t1",
               gmail_draft_id=None, followup_draft_id="fdraft_1")
    ev = SendEvent(message_id="m2", thread_id="t1", sent_at=datetime(2026, 5, 10, 9, 0), step="followup_1")
    fields = record_send_fields(row, ev, cfg)
    assert fields["Status"] == "Follow-up 1 Sent"
    assert fields["Email 2 Sent"] == datetime(2026, 5, 10, 9, 0)
    assert fields["Follow-up Sent?"] is True
    assert fields["Follow-up Date"] == datetime(2026, 5, 10, 9, 0)
    assert fields["Last Gmail Message ID"] == "m2"
    assert fields["Next Action Date"] == datetime(2026, 5, 19, 9, 0)  # +9 days
    assert fields["Followup Draft ID"] == ""  # consumed


def test_record_send_fields_followup_2_is_terminal(monkeypatch):
    cfg = type("C", (), {"followup_1_days": 4, "followup_2_days": 9})()
    row = _row(status="Follow-up 1 Sent", email_1_sent=datetime(2026, 5, 6, 8, 0),
               email_2_sent=datetime(2026, 5, 10, 9, 0), gmail_thread_id="t1",
               gmail_draft_id=None, followup_draft_id="fdraft_2")
    ev = SendEvent(message_id="m3", thread_id="t1", sent_at=datetime(2026, 5, 19, 9, 0), step="followup_2")
    fields = record_send_fields(row, ev, cfg)
    assert fields["Status"] == "Follow-up 2 Sent"
    assert fields["Email 3 Sent"] == datetime(2026, 5, 19, 9, 0)
    assert fields["Next Action"] == "Done"
    assert "Next Action Date" not in fields  # no further follow-up scheduled


def test_followup_step_and_due():
    # Email 1 sent, follow-up 1 due on/after next_action_date, no draft staged yet
    row = _row(status="Email 1 Sent", email_1_sent=datetime(2026, 5, 6, 8, 0),
               next_action_date=datetime(2026, 5, 10, 8, 0),
               gmail_thread_id="t1", gmail_message_id="m1", gmail_draft_id=None)
    assert followup_step(row) == "followup_1"
    assert followup_due(row, now=datetime(2026, 5, 10, 9, 0)) is True
    assert followup_due(row, now=datetime(2026, 5, 9, 9, 0)) is False  # before due date


def test_followup_not_due_when_already_staged():
    row = _row(status="Email 1 Sent", email_1_sent=datetime(2026, 5, 6, 8, 0),
               next_action_date=datetime(2026, 5, 10, 8, 0),
               gmail_thread_id="t1", gmail_message_id="m1", gmail_draft_id=None,
               followup_draft_id="fdraft_1")
    assert followup_due(row, now=datetime(2026, 5, 11, 9, 0)) is False  # draft already waiting


def _patch_loop(monkeypatch, rows, *, detector_event=None, detector_exc=None):
    """Patch config, queue read, gmail service, detector, pool, and capture update_row calls."""
    cfg = type("C", (), {
        "followup_1_days": 4, "followup_2_days": 9, "enable_followups": True,
        "step7_sheet_tab": "Outreach", "sheet_tab_name": "Outreach",
    })()
    monkeypatch.setattr(loop_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(loop_mod, "_gmail_service", lambda: object())
    monkeypatch.setattr(loop_mod, "read_queue", lambda: list(rows))
    monkeypatch.setattr(loop_mod, "load_followup_pools",
                        lambda: {"followup_1": ["bump a"], "followup_2": ["final a"]})
    monkeypatch.setattr(loop_mod, "get_draft_subject", lambda did, service=None: "the growth role at Acme")

    class _Det:
        def detect(self, row, service):
            if detector_exc:
                raise detector_exc
            return detector_event
    monkeypatch.setattr(loop_mod, "make_detector", lambda: _Det())

    updates = []
    monkeypatch.setattr(loop_mod, "update_row", lambda n, fields: updates.append((n, fields)))
    staged = []
    monkeypatch.setattr(loop_mod, "create_reply_draft",
                        lambda **kw: staged.append(kw) or "fdraft_new")
    return updates, staged


def test_tick_records_detected_email1(monkeypatch):
    from src.lib.send_detect import SendEvent
    row = _row(gmail_subject="")  # subject not cached yet
    ev = SendEvent(message_id="m1", thread_id="t1", sent_at=datetime(2026, 5, 6, 8, 0), step="email_1")
    updates, staged = _patch_loop(monkeypatch, [(2, row)], detector_event=ev)
    loop_mod.run_tick(now=datetime(2026, 5, 6, 9, 0), dry_run=False)
    merged = {k: v for _, f in updates for k, v in f.items()}
    assert merged["Status"] == "Email 1 Sent"
    assert merged["Gmail Subject"] == "the growth role at Acme"  # cached during the tick


def test_tick_stages_due_followup(monkeypatch):
    row = _row(status="Email 1 Sent", email_1_sent=datetime(2026, 5, 6, 8, 0),
               next_action_date=datetime(2026, 5, 10, 8, 0), gmail_thread_id="t1",
               gmail_message_id="m1", last_gmail_message_id="m1", gmail_draft_id=None)
    updates, staged = _patch_loop(monkeypatch, [(2, row)], detector_event=None)
    loop_mod.run_tick(now=datetime(2026, 5, 11, 9, 0), dry_run=False)
    assert len(staged) == 1 and staged[0]["thread_id"] == "t1"
    merged = {k: v for _, f in updates for k, v in f.items()}
    assert merged["Followup Draft ID"] == "fdraft_new"


def test_tick_does_not_stage_followup_same_tick_as_send(monkeypatch):
    """Regression: a row carrying Phase 3's already-past next_action_date must NOT have
    its follow-up bump staged in the same tick its email-1 send is detected."""
    from src.lib.send_detect import SendEvent
    row = _row(next_action_date=datetime(2026, 5, 5, 7, 0))  # Phase 3 send slot, already past
    ev = SendEvent(message_id="m1", thread_id="t1", sent_at=datetime(2026, 5, 6, 8, 0), step="email_1")
    updates, staged = _patch_loop(monkeypatch, [(2, row)], detector_event=ev)
    loop_mod.run_tick(now=datetime(2026, 5, 6, 9, 0), dry_run=False)
    assert staged == []  # no follow-up draft staged this tick
    merged = {k: v for _, f in updates for k, v in f.items()}
    assert merged["Status"] == "Email 1 Sent"
    assert merged["Next Action Date"] == datetime(2026, 5, 10, 8, 0)  # sent + 4 days
    assert "Followup Draft ID" not in merged


def test_tick_isolates_row_errors(monkeypatch):
    updates, staged = _patch_loop(monkeypatch, [(2, _row())], detector_exc=RuntimeError("boom"))
    loop_mod.run_tick(now=datetime(2026, 5, 6, 9, 0), dry_run=False)
    merged = {k: v for _, f in updates for k, v in f.items()}
    assert "boom" in merged["Step7 Error"]


def test_tick_dry_run_writes_nothing(monkeypatch):
    from src.lib.send_detect import SendEvent
    ev = SendEvent(message_id="m1", thread_id="t1", sent_at=datetime(2026, 5, 6, 8, 0), step="email_1")
    updates, staged = _patch_loop(monkeypatch, [(2, _row())], detector_event=ev)
    loop_mod.run_tick(now=datetime(2026, 5, 6, 9, 0), dry_run=True)
    assert updates == [] and staged == []


def test_tick_skips_terminal_rows(monkeypatch):
    updates, staged = _patch_loop(monkeypatch, [(2, _row(status="Replied", replied=True))], detector_event=None)
    loop_mod.run_tick(now=datetime(2026, 5, 6, 9, 0), dry_run=False)
    assert updates == [] and staged == []
