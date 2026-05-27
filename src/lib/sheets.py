"""Step 6/7: Google Sheets is the source of truth. Read queue, append rows, update statuses."""
from __future__ import annotations

import logging
import re
from datetime import datetime

from googleapiclient.discovery import build

from src.lib.config import load_config
from src.lib.google_auth import load_credentials
from src.lib.models import StagedRow

log = logging.getLogger(__name__)

SHEET_HEADERS = (
    "Date Added",
    "Company",
    "Role",
    "Job URL",
    "Contact Name",
    "Title",
    "Email",
    "LinkedIn",
    "Status",
    "Last Action Date",
    "Next Action",
    "Next Action Date",
    "Email 1 Sent",
    "Email 2 Sent",
    "Email 3 Sent",
    "Replied?",
    "Reply Date",
    "Notes",
    "Gmail Draft ID",
    "LinkedIn Connection Note",
    "LinkedIn DM",
    "LinkedIn InMail Subject",
    "LinkedIn InMail Body",
)


def _get_sheets_service():
    """Authorized Sheets API service. Wrapped so tests can mock."""
    creds = load_credentials()
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _iso(dt: datetime | None) -> str:
    return "" if dt is None else dt.isoformat(timespec="minutes")


def _row_to_values(row: StagedRow) -> list[str]:
    """Convert a StagedRow to a list of cell values matching SHEET_HEADERS order."""
    cells_by_header: dict[str, str] = {
        "Date Added": _iso(row.date_added),
        "Company": row.company,
        "Role": row.role,
        "Job URL": row.job_url,
        "Contact Name": row.contact_name,
        "Title": row.title,
        "Email": row.email or "",
        "LinkedIn": row.linkedin or "",
        "Status": row.status,
        "Last Action Date": _iso(row.last_action_date),
        "Next Action": row.next_action,
        "Next Action Date": _iso(row.next_action_date),
        "Email 1 Sent": _iso(row.email_1_sent),
        "Email 2 Sent": _iso(row.email_2_sent),
        "Email 3 Sent": _iso(row.email_3_sent),
        "Replied?": "Yes" if row.replied else "No",
        "Reply Date": _iso(row.reply_date),
        "Notes": row.notes,
        "Gmail Draft ID": row.gmail_draft_id or "",
        "LinkedIn Connection Note": row.linkedin_connection_note or "",
        "LinkedIn DM": row.linkedin_dm or "",
        "LinkedIn InMail Subject": row.linkedin_inmail_subject or "",
        "LinkedIn InMail Body": row.linkedin_inmail_body or "",
    }
    return [cells_by_header[h] for h in SHEET_HEADERS]


def ensure_headers() -> None:
    """Write the column header row if the sheet is empty. Safe to call every run."""
    cfg = load_config()
    service = _get_sheets_service()
    rng = f"{cfg.sheet_tab_name}!1:1"
    resp = service.spreadsheets().values().get(spreadsheetId=cfg.sheet_id, range=rng).execute()
    if resp.get("values"):
        return
    service.spreadsheets().values().update(
        spreadsheetId=cfg.sheet_id,
        range=rng,
        valueInputOption="RAW",
        body={"values": [list(SHEET_HEADERS)]},
    ).execute()
    log.info("Sheet headers written: %d columns", len(SHEET_HEADERS))


def append_row(row: StagedRow) -> int:
    """Append a row, return the 1-indexed row number on the sheet."""
    cfg = load_config()
    service = _get_sheets_service()
    values = _row_to_values(row)
    resp = service.spreadsheets().values().append(
        spreadsheetId=cfg.sheet_id,
        range=f"{cfg.sheet_tab_name}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()
    updated_range = resp.get("updates", {}).get("updatedRange", "")
    # Range looks like "Outreach!A2:U2" -> we want 2.
    m = re.search(r"![A-Z]+(\d+):", updated_range)
    row_number = int(m.group(1)) if m else 0
    log.info("Sheet row appended at row=%d for %s", row_number, row.contact_name)
    return row_number


def read_queue() -> list[dict]:
    """Step 7. Placeholder."""
    raise NotImplementedError("read_queue is implemented in Step 7.")


def update_row(row_number: int, fields: dict) -> None:
    """Step 7. Placeholder."""
    raise NotImplementedError("update_row is implemented in Step 7.")
