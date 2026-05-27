"""Tests for src/lib/sheets.py — Google Sheets staging (Step 6 scope)."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock


from src.lib.models import StagedRow
from src.lib.sheets import (
    SHEET_HEADERS,
    _row_to_values,
    append_row,
    ensure_headers,
)


def _row() -> StagedRow:
    return StagedRow(
        date_added=datetime(2026, 5, 5, 10, 0),
        company="Acme",
        role="Growth Marketing Manager",
        job_url="https://example.com/x",
        contact_name="Jordan Avery",
        title="Founder",
        email="jordan@acme.example",
        linkedin="https://www.linkedin.com/in/javery/",
        next_action_date=datetime(2026, 5, 6, 7, 30),
        gmail_draft_id="draft_123",
        linkedin_connection_note="Saw you went agency to infra. Want to connect.",
        linkedin_dm="DM body...",
    )


# --------------------------------------------------------------------------- #
# _row_to_values                                                              #
# --------------------------------------------------------------------------- #

def test_row_to_values_returns_one_value_per_header():
    values = _row_to_values(_row())
    assert len(values) == len(SHEET_HEADERS)


def test_row_to_values_column_order_matches_headers():
    """Date Added column 0, Company column 1, etc."""
    values = _row_to_values(_row())
    company_index = SHEET_HEADERS.index("Company")
    role_index = SHEET_HEADERS.index("Role")
    assert values[company_index] == "Acme"
    assert values[role_index] == "Growth Marketing Manager"


def test_row_to_values_serializes_datetimes_as_iso():
    values = _row_to_values(_row())
    date_added = values[SHEET_HEADERS.index("Date Added")]
    # ISO 8601 with date and time
    assert "2026-05-05" in date_added


def test_row_to_values_replied_field_blank_or_no_when_false():
    """'Replied?' column should be empty or 'No' when StagedRow.replied is False."""
    values = _row_to_values(_row())
    replied = values[SHEET_HEADERS.index("Replied?")]
    assert replied in ("", "No", "FALSE", "false")


# --------------------------------------------------------------------------- #
# ensure_headers                                                              #
# --------------------------------------------------------------------------- #

def test_ensure_headers_writes_when_sheet_is_empty(monkeypatch):
    fake_service = MagicMock()
    fake_service.spreadsheets().values().get().execute.return_value = {"values": []}
    captured = {}

    def fake_update(spreadsheetId, range, valueInputOption, body):
        captured["body"] = body
        return MagicMock(execute=lambda: {"updatedRange": "x"})

    fake_service.spreadsheets().values().update.side_effect = fake_update

    monkeypatch.setattr("src.lib.sheets._get_sheets_service", lambda: fake_service)
    monkeypatch.setattr(
        "src.lib.sheets.load_config",
        lambda: type("C", (), {"sheet_id": "abc", "sheet_tab_name": "Outreach"})(),
    )
    ensure_headers()
    assert captured["body"]["values"] == [list(SHEET_HEADERS)]


def test_ensure_headers_skips_when_first_row_already_set(monkeypatch):
    fake_service = MagicMock()
    fake_service.spreadsheets().values().get().execute.return_value = {
        "values": [list(SHEET_HEADERS)]
    }
    update_called = {"yes": False}
    fake_service.spreadsheets().values().update.side_effect = lambda **kw: update_called.update(yes=True) or MagicMock()

    monkeypatch.setattr("src.lib.sheets._get_sheets_service", lambda: fake_service)
    monkeypatch.setattr(
        "src.lib.sheets.load_config",
        lambda: type("C", (), {"sheet_id": "abc", "sheet_tab_name": "Outreach"})(),
    )
    ensure_headers()
    assert not update_called["yes"]


# --------------------------------------------------------------------------- #
# append_row                                                                  #
# --------------------------------------------------------------------------- #

def test_append_row_calls_sheets_append_with_values(monkeypatch):
    fake_service = MagicMock()
    fake_service.spreadsheets().values().append().execute.return_value = {
        "updates": {"updatedRange": "Outreach!A2:U2"}
    }

    captured = {}

    def fake_append(spreadsheetId, range, valueInputOption, insertDataOption, body):
        captured["body"] = body
        captured["spreadsheetId"] = spreadsheetId
        return MagicMock(execute=lambda: {"updates": {"updatedRange": "Outreach!A2:U2"}})

    fake_service.spreadsheets().values().append.side_effect = fake_append

    monkeypatch.setattr("src.lib.sheets._get_sheets_service", lambda: fake_service)
    monkeypatch.setattr(
        "src.lib.sheets.load_config",
        lambda: type("C", (), {"sheet_id": "abc", "sheet_tab_name": "Outreach"})(),
    )
    row_num = append_row(_row())
    assert row_num == 2
    assert "values" in captured["body"]
    assert len(captured["body"]["values"]) == 1
    assert len(captured["body"]["values"][0]) == len(SHEET_HEADERS)
