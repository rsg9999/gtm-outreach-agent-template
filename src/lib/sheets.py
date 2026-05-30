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
    "Gmail Message ID",
    "Gmail Subject",
    "Gmail Thread ID",
    "Last Gmail Message ID",
    "Followup Draft ID",
    "Reply Draft ID",
    "Step7 Error",
    "Follow-up Sent?",
    "Follow-up Date",
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
        "Gmail Message ID": row.gmail_message_id or "",
        "Gmail Subject": row.gmail_subject or "",
        "Gmail Thread ID": row.gmail_thread_id or "",
        "Last Gmail Message ID": row.last_gmail_message_id or "",
        "Followup Draft ID": row.followup_draft_id or "",
        "Reply Draft ID": row.reply_draft_id or "",
        "Step7 Error": row.step7_error,
        "Follow-up Sent?": "Yes" if row.followup_sent else "No",
        "Follow-up Date": _iso(row.followup_date),
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


def _step7_tab(cfg) -> str:
    return getattr(cfg, "step7_sheet_tab", "") or cfg.sheet_tab_name


def ensure_step7_headers() -> None:
    """Ensure the Step 7 tab's header row contains all SHEET_HEADERS (extends legacy tabs).

    Writes only row 1, so existing data rows are untouched; new columns become blank
    for them. Idempotent: a no-op when row 1 already equals SHEET_HEADERS.
    """
    cfg = load_config()
    service = _get_sheets_service()
    tab = _step7_tab(cfg)
    rng = f"{tab}!1:1"
    resp = service.spreadsheets().values().get(spreadsheetId=cfg.sheet_id, range=rng).execute()
    current = (resp.get("values") or [[]])[0]
    if current == list(SHEET_HEADERS):
        return
    service.spreadsheets().values().update(
        spreadsheetId=cfg.sheet_id,
        range=rng,
        valueInputOption="RAW",
        body={"values": [list(SHEET_HEADERS)]},
    ).execute()
    log.info("Step 7 headers ensured on tab %r: %d columns", tab, len(SHEET_HEADERS))


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


def _parse_dt(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _col_letter(index0: int) -> str:
    """0-based column index -> A1 letter (0->A, 25->Z, 26->AA)."""
    s = ""
    n = index0
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            return s


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    return str(value)


def _values_to_row(values: list[str]) -> StagedRow:
    """Inverse of _row_to_values. Pads short rows; tolerant of blank optional cells."""
    padded = list(values) + [""] * (len(SHEET_HEADERS) - len(values))
    c = dict(zip(SHEET_HEADERS, padded))
    return StagedRow(
        date_added=_parse_dt(c["Date Added"]) or datetime(1970, 1, 1),
        company=c["Company"],
        role=c["Role"],
        job_url=c["Job URL"],
        contact_name=c["Contact Name"],
        title=c["Title"],
        email=c["Email"] or None,
        linkedin=c["LinkedIn"] or None,
        status=c["Status"] or "Drafted",
        last_action_date=_parse_dt(c["Last Action Date"]),
        next_action=c["Next Action"] or "Send Email 1",
        next_action_date=_parse_dt(c["Next Action Date"]),
        email_1_sent=_parse_dt(c["Email 1 Sent"]),
        email_2_sent=_parse_dt(c["Email 2 Sent"]),
        email_3_sent=_parse_dt(c["Email 3 Sent"]),
        replied=c["Replied?"].strip().lower() in {"yes", "true"},
        reply_date=_parse_dt(c["Reply Date"]),
        notes=c["Notes"],
        gmail_draft_id=c["Gmail Draft ID"] or None,
        linkedin_connection_note=c["LinkedIn Connection Note"] or None,
        linkedin_dm=c["LinkedIn DM"] or None,
        linkedin_inmail_subject=c["LinkedIn InMail Subject"] or None,
        linkedin_inmail_body=c["LinkedIn InMail Body"] or None,
        gmail_message_id=c["Gmail Message ID"] or None,
        gmail_subject=c["Gmail Subject"] or None,
        gmail_thread_id=c["Gmail Thread ID"] or None,
        last_gmail_message_id=c["Last Gmail Message ID"] or None,
        followup_draft_id=c["Followup Draft ID"] or None,
        reply_draft_id=c["Reply Draft ID"] or None,
        step7_error=c["Step7 Error"],
        followup_sent=c["Follow-up Sent?"].strip().lower() in {"yes", "true"},
        followup_date=_parse_dt(c["Follow-up Date"]),
    )


def read_queue() -> list[tuple[int, StagedRow]]:
    """Read the Step 7 tab's data rows. Returns (1-indexed sheet row number, StagedRow)."""
    cfg = load_config()
    service = _get_sheets_service()
    tab = _step7_tab(cfg)
    resp = service.spreadsheets().values().get(
        spreadsheetId=cfg.sheet_id, range=f"{tab}!A1:ZZ"
    ).execute()
    out: list[tuple[int, StagedRow]] = []
    all_rows = resp.get("values", [])
    for offset, values in enumerate(all_rows[1:], start=1):  # skip header row (row 1)
        if not any(cell.strip() for cell in values):
            continue  # blank row
        out.append((offset + 1, _values_to_row(values)))
    return out


def update_row(row_number: int, fields: dict) -> None:
    """Write specific columns (keyed by SHEET_HEADERS name) on one sheet row."""
    if not fields:
        return
    cfg = load_config()
    service = _get_sheets_service()
    tab = _step7_tab(cfg)
    data = []
    for header, value in fields.items():
        col = _col_letter(SHEET_HEADERS.index(header))
        data.append({"range": f"{tab}!{col}{row_number}", "values": [[_cell(value)]]})
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=cfg.sheet_id,
        body={"valueInputOption": "RAW", "data": data},
    ).execute()
    log.info("Sheet row %d updated: %s", row_number, ", ".join(fields))
