# Step 7 Phase 1 — Manual-send detection + pooled follow-ups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `run-loop` tick that detects (via the Gmail API) emails the user sent by hand, records them to the Google Sheet, and stages deterministic follow-up bump drafts — without ever sending anything itself.

**Architecture:** One idempotent CLI tick reads contact rows from a Sheet tab, and per row: caches the draft subject, asks a pluggable `SendDetector` whether a staged draft was sent (polling the Gmail API now; a `SendEvent` seam lets Pub/Sub push drop in later), records the send + schedules the next follow-up, and stages a due follow-up as a Gmail *reply draft* drawn from a per-user pool. All Gmail/Sheets calls are mocked in tests.

**Tech Stack:** Python 3.11, click (CLI), pydantic (models), google-api-python-client (Gmail/Sheets), pytest + pytest-mock, ruff.

**Spec:** [docs/superpowers/specs/2026-05-29-step7-phase1-send-detection-and-followups-design.md](../specs/2026-05-29-step7-phase1-send-detection-and-followups-design.md)

**Conventions for every task:** run tests with `uv run pytest`; lint with `uv run ruff check src tests`; the existing suite must stay green. Commit messages end with the repo's trailer:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `src/lib/models.py` | `StagedRow` gains 9 Step-7 fields | Modify |
| `src/lib/sheets.py` | `SHEET_HEADERS` +9; `_values_to_row`, `read_queue`, `update_row`, `ensure_step7_headers` | Modify |
| `src/lib/gmail.py` | fetch primitives: `list_draft_ids`, `get_draft_subject`, `get_message_meta`, `search_sent`, `create_reply_draft` | Modify |
| `src/lib/send_detect.py` | `SendEvent`, `SendDetector` Protocol, `PollingSendDetector`, `SendDetectionError` | Create |
| `src/lib/followups.py` | `select_bump` (stable hashlib selection) | Create |
| `src/lib/profile.py` | `load_followup_pools()` | Modify |
| `src/lib/config.py` | `step7_sheet_tab`, `enable_followups` | Modify |
| `src/loop.py` | the tick: cache-subject → detect → record → stage-followup, per-row isolation, `--dry-run`, `--init-headers` | Modify (replace stub) |
| `Profile.example/thread_followups.md` | shipped fictional bump-pool template | Create |
| `.env.example` | document `STEP7_SHEET_TAB`, `ENABLE_FOLLOWUPS` | Modify |
| `tests/test_*.py` | tests per task | Create/Modify |

**Deferred to Phase 2 (do NOT build here):** `get_message_body`, full `walk_thread`, reply detection, OOO/bounce, LLM reply drafts, `Reply Draft ID` population, `backfill-step7`, Slack digest, launchd. The `Reply Draft ID` *column* is added now (schema stability) but stays blank.

---

## Task 1: Extend `StagedRow` + `SHEET_HEADERS` with the 9 Step-7 columns

**Files:**
- Modify: `src/lib/models.py` (StagedRow, end of class ~line 91)
- Modify: `src/lib/sheets.py` (`SHEET_HEADERS` ~line 16-40, `_row_to_values` ~line 53-80)
- Test: `tests/test_sheets.py`

- [ ] **Step 1: Write failing tests** in `tests/test_sheets.py` (append):

```python
def test_headers_include_step7_columns():
    for col in (
        "Gmail Message ID", "Gmail Subject", "Gmail Thread ID", "Last Gmail Message ID",
        "Followup Draft ID", "Reply Draft ID", "Step7 Error", "Follow-up Sent?", "Follow-up Date",
    ):
        assert col in SHEET_HEADERS


def test_row_to_values_maps_step7_fields():
    from datetime import datetime
    row = _row()
    row.gmail_message_id = "m1"
    row.gmail_thread_id = "t1"
    row.followup_sent = True
    row.followup_date = datetime(2026, 5, 10, 9, 0)
    values = _row_to_values(row)
    assert values[SHEET_HEADERS.index("Gmail Message ID")] == "m1"
    assert values[SHEET_HEADERS.index("Gmail Thread ID")] == "t1"
    assert values[SHEET_HEADERS.index("Follow-up Sent?")] == "Yes"
    assert "2026-05-10" in values[SHEET_HEADERS.index("Follow-up Date")]
    assert len(values) == len(SHEET_HEADERS)
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_sheets.py::test_headers_include_step7_columns tests/test_sheets.py::test_row_to_values_maps_step7_fields -v`
Expected: FAIL (`AssertionError` / new columns absent).

- [ ] **Step 3: Add fields to `StagedRow`** in `src/lib/models.py`, immediately after `linkedin_inmail_body` (the last field):

```python
    # --- Step 7 tracking (manual-send detection + follow-ups) ---
    gmail_message_id: Optional[str] = None
    gmail_subject: Optional[str] = None
    gmail_thread_id: Optional[str] = None
    last_gmail_message_id: Optional[str] = None
    followup_draft_id: Optional[str] = None
    reply_draft_id: Optional[str] = None       # populated in Phase 2
    step7_error: str = ""
    followup_sent: bool = False
    followup_date: Optional[datetime] = None
```

- [ ] **Step 4: Extend `SHEET_HEADERS`** in `src/lib/sheets.py` — add these entries to the tuple, after `"LinkedIn InMail Body"`:

```python
    "Gmail Message ID",
    "Gmail Subject",
    "Gmail Thread ID",
    "Last Gmail Message ID",
    "Followup Draft ID",
    "Reply Draft ID",
    "Step7 Error",
    "Follow-up Sent?",
    "Follow-up Date",
```

- [ ] **Step 5: Map the new fields in `_row_to_values`** — add to the `cells_by_header` dict in `src/lib/sheets.py`, before the closing `}`:

```python
        "Gmail Message ID": row.gmail_message_id or "",
        "Gmail Subject": row.gmail_subject or "",
        "Gmail Thread ID": row.gmail_thread_id or "",
        "Last Gmail Message ID": row.last_gmail_message_id or "",
        "Followup Draft ID": row.followup_draft_id or "",
        "Reply Draft ID": row.reply_draft_id or "",
        "Step7 Error": row.step7_error,
        "Follow-up Sent?": "Yes" if row.followup_sent else "No",
        "Follow-up Date": _iso(row.followup_date),
```

- [ ] **Step 6: Run tests** (new + existing sheets tests)

Run: `uv run pytest tests/test_sheets.py -v`
Expected: PASS (including the pre-existing `test_row_to_values_returns_one_value_per_header`).

- [ ] **Step 7: Commit**

```bash
git add src/lib/models.py src/lib/sheets.py tests/test_sheets.py
git commit -m "feat(step7): add 9 tracking columns to StagedRow + SHEET_HEADERS

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `ensure_step7_headers` — idempotent header migration

**Files:**
- Modify: `src/lib/sheets.py`
- Test: `tests/test_sheets.py`

- [ ] **Step 1: Write failing tests** (append to `tests/test_sheets.py`):

```python
def test_ensure_step7_headers_writes_full_header_when_missing(monkeypatch):
    fake_service = MagicMock()
    # Tab currently has only the legacy first 18 headers.
    fake_service.spreadsheets().values().get().execute.return_value = {
        "values": [list(SHEET_HEADERS[:18])]
    }
    captured = {}

    def fake_update(spreadsheetId, range, valueInputOption, body):
        captured["body"] = body
        return MagicMock(execute=lambda: {"updatedRange": "x"})

    fake_service.spreadsheets().values().update.side_effect = fake_update
    monkeypatch.setattr("src.lib.sheets._get_sheets_service", lambda: fake_service)
    monkeypatch.setattr(
        "src.lib.sheets.load_config",
        lambda: type("C", (), {"sheet_id": "abc", "sheet_tab_name": "Outreach", "step7_sheet_tab": "Outreach"})(),
    )
    ensure_step7_headers()
    assert captured["body"]["values"] == [list(SHEET_HEADERS)]


def test_ensure_step7_headers_noop_when_already_full(monkeypatch):
    fake_service = MagicMock()
    fake_service.spreadsheets().values().get().execute.return_value = {
        "values": [list(SHEET_HEADERS)]
    }
    called = {"yes": False}
    fake_service.spreadsheets().values().update.side_effect = lambda **kw: called.update(yes=True)
    monkeypatch.setattr("src.lib.sheets._get_sheets_service", lambda: fake_service)
    monkeypatch.setattr(
        "src.lib.sheets.load_config",
        lambda: type("C", (), {"sheet_id": "abc", "sheet_tab_name": "Outreach", "step7_sheet_tab": "Outreach"})(),
    )
    ensure_step7_headers()
    assert not called["yes"]
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_sheets.py::test_ensure_step7_headers_writes_full_header_when_missing -v`
Expected: FAIL (`ensure_step7_headers` not defined).

- [ ] **Step 3: Implement** in `src/lib/sheets.py` (after `ensure_headers`):

```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_sheets.py -k ensure_step7 -v`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add src/lib/sheets.py tests/test_sheets.py
git commit -m "feat(step7): ensure_step7_headers migrates legacy tabs idempotently

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `read_queue` + `update_row` (Sheet ↔ StagedRow)

**Files:**
- Modify: `src/lib/sheets.py`
- Test: `tests/test_sheets.py`

- [ ] **Step 1: Write failing tests** (append):

```python
def _full_row_values(overrides=None):
    """Build a values list matching SHEET_HEADERS for a single sheet row."""
    base = {h: "" for h in SHEET_HEADERS}
    base.update({
        "Date Added": "2026-05-05T10:00",
        "Company": "Acme",
        "Role": "Growth Marketing Manager",
        "Contact Name": "Jordan Avery",
        "Email": "jordan@acme.example",
        "Status": "Drafted",
        "Gmail Draft ID": "draft_1",
    })
    base.update(overrides or {})
    return [base[h] for h in SHEET_HEADERS]


def test_read_queue_parses_rows_with_numbers(monkeypatch):
    fake_service = MagicMock()
    fake_service.spreadsheets().values().get().execute.return_value = {
        "values": [list(SHEET_HEADERS), _full_row_values()]
    }
    monkeypatch.setattr("src.lib.sheets._get_sheets_service", lambda: fake_service)
    monkeypatch.setattr(
        "src.lib.sheets.load_config",
        lambda: type("C", (), {"sheet_id": "abc", "sheet_tab_name": "Outreach", "step7_sheet_tab": "Outreach"})(),
    )
    rows = read_queue()
    assert len(rows) == 1
    row_number, row = rows[0]
    assert row_number == 2  # row 1 is headers, data starts at 2
    assert row.company == "Acme"
    assert row.email == "jordan@acme.example"
    assert row.gmail_draft_id == "draft_1"


def test_read_queue_skips_blank_rows(monkeypatch):
    fake_service = MagicMock()
    fake_service.spreadsheets().values().get().execute.return_value = {
        "values": [list(SHEET_HEADERS), [""] * len(SHEET_HEADERS), _full_row_values()]
    }
    monkeypatch.setattr("src.lib.sheets._get_sheets_service", lambda: fake_service)
    monkeypatch.setattr(
        "src.lib.sheets.load_config",
        lambda: type("C", (), {"sheet_id": "abc", "sheet_tab_name": "Outreach", "step7_sheet_tab": "Outreach"})(),
    )
    rows = read_queue()
    assert [n for n, _ in rows] == [3]  # blank row 2 skipped


def test_update_row_batches_named_columns(monkeypatch):
    fake_service = MagicMock()
    captured = {}

    def fake_batch(spreadsheetId, body):
        captured["body"] = body
        return MagicMock(execute=lambda: {})

    fake_service.spreadsheets().values().batchUpdate.side_effect = fake_batch
    monkeypatch.setattr("src.lib.sheets._get_sheets_service", lambda: fake_service)
    monkeypatch.setattr(
        "src.lib.sheets.load_config",
        lambda: type("C", (), {"sheet_id": "abc", "sheet_tab_name": "Outreach", "step7_sheet_tab": "Outreach"})(),
    )
    from datetime import datetime
    update_row(2, {"Status": "Email 1 Sent", "Email 1 Sent": datetime(2026, 5, 6, 8, 0), "Follow-up Sent?": True})
    data = captured["body"]["data"]
    ranges = {d["range"]: d["values"][0][0] for d in data}
    assert ranges["Outreach!I2"] == "Email 1 Sent"      # Status is column I (index 8)
    assert "2026-05-06" in ranges["Outreach!M2"]         # Email 1 Sent is column M (index 12)
    assert ranges["Outreach!AE2"] == "Yes"               # Follow-up Sent? is index 30 -> AE
```

> Column-letter sanity for the assertions: `Status` index 8 → `I`; `Email 1 Sent` index 12 → `M`; `Follow-up Sent?` index 30 → `AE` (Z=25, AA=26, … AE=30). Verify indices against `SHEET_HEADERS` order before trusting them.

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_sheets.py -k "read_queue or update_row" -v`
Expected: FAIL (functions raise `NotImplementedError` / undefined helpers).

- [ ] **Step 3: Implement** in `src/lib/sheets.py`. Add helpers + replace the two placeholder functions:

```python
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
        spreadsheetId=cfg.sheet_id, range=f"{tab}!A2:ZZ"
    ).execute()
    out: list[tuple[int, StagedRow]] = []
    for offset, values in enumerate(resp.get("values", [])):
        if not any(cell.strip() for cell in values):
            continue  # blank row
        out.append((offset + 2, _values_to_row(values)))
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
```

Also ensure `datetime` is imported at the top of `sheets.py` (it already imports `from datetime import datetime`).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_sheets.py -v`
Expected: PASS (all, including the three new).

- [ ] **Step 5: Commit**

```bash
git add src/lib/sheets.py tests/test_sheets.py
git commit -m "feat(step7): implement read_queue + update_row with StagedRow round-trip

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Gmail fetch primitives

**Files:**
- Modify: `src/lib/gmail.py`
- Test: `tests/test_gmail.py`

- [ ] **Step 1: Write failing tests** (append to `tests/test_gmail.py`):

```python
def test_list_draft_ids_collects_ids(monkeypatch):
    from src.lib import gmail
    svc = MagicMock()
    svc.users().drafts().list().execute.return_value = {"drafts": [{"id": "d1"}, {"id": "d2"}]}
    assert gmail.list_draft_ids(service=svc) == {"d1", "d2"}


def test_get_draft_subject_reads_subject_header(monkeypatch):
    from src.lib import gmail
    svc = MagicMock()
    svc.users().drafts().get().execute.return_value = {
        "message": {"payload": {"headers": [{"name": "Subject", "value": "the growth role at Acme"}]}}
    }
    assert gmail.get_draft_subject("d1", service=svc) == "the growth role at Acme"


def test_get_message_meta_parses_thread_and_date(monkeypatch):
    from src.lib import gmail
    svc = MagicMock()
    svc.users().messages().get().execute.return_value = {
        "id": "m1", "threadId": "t1", "internalDate": "1778054400000"  # 2026-05-06 08:00 UTC
    }
    meta = gmail.get_message_meta("m1", service=svc)
    assert meta["message_id"] == "m1"
    assert meta["thread_id"] == "t1"
    assert meta["internal_date"].year == 2026


def test_search_sent_returns_message_ids(monkeypatch):
    from src.lib import gmail
    from datetime import datetime
    svc = MagicMock()
    captured = {}

    def fake_list(userId, q, **kw):
        captured["q"] = q
        return MagicMock(execute=lambda: {"messages": [{"id": "m1"}, {"id": "m2"}]})

    svc.users().messages().list.side_effect = fake_list
    ids = gmail.search_sent("jordan@acme.example", "the growth role at Acme",
                            after=datetime(2026, 5, 5), service=svc)
    assert ids == ["m1", "m2"]
    assert "in:sent" in captured["q"]
    assert "jordan@acme.example" in captured["q"]
    assert "2026/05/05" in captured["q"]


def test_create_reply_draft_threads_and_returns_id(monkeypatch):
    from src.lib import gmail
    svc = MagicMock()
    captured = {}

    def fake_create(userId, body):
        captured["body"] = body
        return MagicMock(execute=lambda: {"id": "fdraft_1"})

    svc.users().drafts().create.side_effect = fake_create
    monkeypatch.setattr("src.lib.gmail.load_config",
                        lambda: type("C", (), {"sender_email": "you@example.com", "resume_path": __import__("pathlib").Path("/nope")})())
    draft_id = gmail.create_reply_draft(
        thread_id="t1", to="jordan@acme.example", subject="the growth role at Acme",
        body="just bumping this up for you.", in_reply_to="<m1@mail>", references="<m1@mail>",
        service=svc,
    )
    assert draft_id == "fdraft_1"
    assert captured["body"]["message"]["threadId"] == "t1"
    assert "raw" in captured["body"]["message"]
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_gmail.py -k "list_draft_ids or get_draft_subject or get_message_meta or search_sent or create_reply_draft" -v`
Expected: FAIL (functions undefined).

- [ ] **Step 3: Implement** in `src/lib/gmail.py`. Add near the top: `from datetime import datetime`. Append these functions (and replace the `has_reply` placeholder is NOT required — leave it as a Phase 2 placeholder):

```python
def list_draft_ids(service=None) -> set[str]:
    """All current Gmail draft IDs for the user (paginated)."""
    service = service or _get_gmail_service()
    ids: set[str] = set()
    token = None
    while True:
        resp = service.users().drafts().list(userId="me", pageToken=token).execute()
        ids.update(d["id"] for d in resp.get("drafts", []))
        token = resp.get("nextPageToken")
        if not token:
            return ids


def _header(headers: list[dict], name: str) -> str | None:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


def get_draft_subject(draft_id: str, service=None) -> str | None:
    """Subject of a staged draft, cached before the draft can disappear on send."""
    service = service or _get_gmail_service()
    resp = service.users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
    headers = resp.get("message", {}).get("payload", {}).get("headers", [])
    return _header(headers, "Subject")


def get_message_meta(message_id: str, service=None) -> dict:
    """Lightweight message metadata: ids + naive-local sent datetime + Message-ID header."""
    service = service or _get_gmail_service()
    resp = service.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["Message-ID", "Subject", "From", "To"],
    ).execute()
    internal_ms = int(resp.get("internalDate", "0"))
    headers = resp.get("payload", {}).get("headers", [])
    return {
        "message_id": resp.get("id", message_id),
        "thread_id": resp.get("threadId"),
        "internal_date": datetime.fromtimestamp(internal_ms / 1000),
        "rfc_message_id": _header(headers, "Message-ID"),
    }


def search_sent(to: str, subject: str | None, after: datetime | None = None, service=None) -> list[str]:
    """Message IDs in Sent matching recipient (+ optional subject, + optional after-date)."""
    service = service or _get_gmail_service()
    q = f"in:sent to:{to}"
    if subject:
        q += f' subject:"{subject}"'
    if after:
        q += f" after:{after:%Y/%m/%d}"
    resp = service.users().messages().list(userId="me", q=q).execute()
    return [m["id"] for m in resp.get("messages", [])]


def create_reply_draft(
    *, thread_id: str, to: str, subject: str, body: str,
    in_reply_to: str | None = None, references: str | None = None, service=None,
) -> str:
    """Stage a reply draft inside an existing thread. NEVER sends. Returns the draft ID."""
    service = service or _get_gmail_service()
    cfg = load_config()
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = cfg.sender_email
    msg["Subject"] = reply_subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(_plain_to_html(body), subtype="html")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    result = service.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": thread_id}}
    ).execute()
    draft_id = result["id"]
    log.info("Gmail follow-up reply draft staged: id=%s thread=%s to=%s", draft_id, thread_id, to)
    return draft_id
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_gmail.py -v`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/lib/gmail.py tests/test_gmail.py
git commit -m "feat(step7): Gmail fetch primitives (drafts, sent search, message meta, reply draft)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `send_detect.py` — the detector seam + polling implementation

**Files:**
- Create: `src/lib/send_detect.py`
- Test: `tests/test_send_detect.py`

- [ ] **Step 1: Write failing tests** in `tests/test_send_detect.py`:

```python
"""Tests for the Step 7 send-detection seam (polling implementation)."""
from __future__ import annotations

from datetime import datetime

import pytest

from src.lib.models import StagedRow
from src.lib.send_detect import PollingSendDetector, SendDetectionError, SendEvent


def _row(**over) -> StagedRow:
    base = dict(
        date_added=datetime(2026, 5, 5, 10, 0), company="Acme", role="GMM",
        job_url="https://x", contact_name="Jordan Avery", title="Founder",
        email="jordan@acme.example", status="Drafted", gmail_draft_id="draft_1",
        gmail_subject="the growth role at Acme",
    )
    base.update(over)
    return StagedRow(**base)


def _gmail_stub(monkeypatch, *, open_drafts, sent_ids, metas):
    import src.lib.gmail as g
    monkeypatch.setattr(g, "list_draft_ids", lambda service=None: set(open_drafts))
    monkeypatch.setattr(g, "search_sent", lambda to, subject, after=None, service=None: list(sent_ids))
    monkeypatch.setattr(g, "get_message_meta", lambda mid, service=None: metas[mid])


def test_no_event_when_draft_still_open(monkeypatch):
    _gmail_stub(monkeypatch, open_drafts={"draft_1"}, sent_ids=[], metas={})
    assert PollingSendDetector().detect(_row(), service=None) is None


def test_email1_detected_when_draft_gone_and_sent_match(monkeypatch):
    metas = {"m1": {"message_id": "m1", "thread_id": "t1", "internal_date": datetime(2026, 5, 6, 8, 0)}}
    _gmail_stub(monkeypatch, open_drafts=set(), sent_ids=["m1"], metas=metas)
    ev = PollingSendDetector().detect(_row(), service=None)
    assert ev == SendEvent(message_id="m1", thread_id="t1", sent_at=datetime(2026, 5, 6, 8, 0), step="email_1")


def test_gone_with_no_match_raises(monkeypatch):
    _gmail_stub(monkeypatch, open_drafts=set(), sent_ids=[], metas={})
    with pytest.raises(SendDetectionError):
        PollingSendDetector().detect(_row(), service=None)


def test_followup1_detected_in_thread_excludes_known(monkeypatch):
    row = _row(
        status="Email 1 Sent", email_1_sent=datetime(2026, 5, 6, 8, 0),
        gmail_message_id="m1", last_gmail_message_id="m1", gmail_thread_id="t1",
        gmail_draft_id=None, followup_draft_id="fdraft_1",
    )
    metas = {
        "m1": {"message_id": "m1", "thread_id": "t1", "internal_date": datetime(2026, 5, 6, 8, 0)},
        "m2": {"message_id": "m2", "thread_id": "t1", "internal_date": datetime(2026, 5, 10, 9, 0)},
    }
    _gmail_stub(monkeypatch, open_drafts=set(), sent_ids=["m1", "m2"], metas=metas)
    ev = PollingSendDetector().detect(row, service=None)
    assert ev.message_id == "m2" and ev.step == "followup_1" and ev.thread_id == "t1"
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_send_detect.py -v`
Expected: FAIL (`src.lib.send_detect` does not exist).

- [ ] **Step 3: Implement** `src/lib/send_detect.py`:

```python
"""Detect — via the Gmail API — that the user sent a staged draft by hand.

The loop depends only on `SendDetector.detect(row, service) -> SendEvent | None`.
Phase 1 ships `PollingSendDetector`; a Pub/Sub `PushSendDetector` can implement the
same contract later without touching the loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from src.lib import gmail
from src.lib.models import StagedRow


class SendDetectionError(RuntimeError):
    """A staged draft disappeared but no matching Sent message was found."""


@dataclass(frozen=True)
class SendEvent:
    message_id: str
    thread_id: str
    sent_at: datetime
    step: str  # "email_1" | "followup_1" | "followup_2"


class SendDetector(Protocol):
    def detect(self, row: StagedRow, service) -> SendEvent | None: ...


def _target(row: StagedRow):
    """Return (step, draft_id, known_ids, thread_id, subject, after) for the active step."""
    known = {x for x in (row.gmail_message_id, row.last_gmail_message_id) if x}
    if row.email_1_sent is None:
        return ("email_1", row.gmail_draft_id, set(), None, row.gmail_subject, row.date_added)
    if row.email_2_sent is None and row.followup_draft_id:
        return ("followup_1", row.followup_draft_id, known, row.gmail_thread_id, row.gmail_subject, row.email_1_sent)
    if row.email_3_sent is None and row.followup_draft_id:
        return ("followup_2", row.followup_draft_id, known, row.gmail_thread_id, row.gmail_subject, row.email_2_sent)
    return (None, None, set(), None, None, None)


class PollingSendDetector:
    """Option A: poll Drafts + Sent through the Gmail API."""

    def detect(self, row: StagedRow, service) -> SendEvent | None:
        step, draft_id, known_ids, thread_id, subject, after = _target(row)
        if draft_id is None:
            return None
        if draft_id in gmail.list_draft_ids(service=service):
            return None  # not sent yet
        candidates = gmail.search_sent(to=row.email, subject=subject, after=after, service=service)
        matches = []
        for mid in candidates:
            if mid in known_ids:
                continue
            meta = gmail.get_message_meta(mid, service=service)
            if thread_id and meta["thread_id"] != thread_id:
                continue
            matches.append((meta["internal_date"], meta["message_id"], meta["thread_id"]))
        if not matches:
            raise SendDetectionError("draft removed; no matching Sent message")
        matches.sort(key=lambda t: t[0])
        sent_at, mid, tid = matches[0]
        return SendEvent(message_id=mid, thread_id=tid, sent_at=sent_at, step=step)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_send_detect.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add src/lib/send_detect.py tests/test_send_detect.py
git commit -m "feat(step7): SendEvent seam + PollingSendDetector

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Follow-up pool loading + stable selection

**Files:**
- Create: `src/lib/followups.py`
- Modify: `src/lib/profile.py`
- Test: `tests/test_followups.py`

- [ ] **Step 1: Write failing tests** in `tests/test_followups.py`:

```python
"""Tests for the deterministic follow-up bump pool."""
from __future__ import annotations

from src.lib.followups import select_bump
from src.lib.profile import parse_followup_pools

_SAMPLE = """## Email 2
- just bumping this up for you.
- floating this back up in case it got buried.

## Email 3
- last bump from me on this. no worries if the timing's off.
"""


def test_parse_followup_pools_splits_sections():
    pools = parse_followup_pools(_SAMPLE)
    assert pools["followup_1"] == ["just bumping this up for you.",
                                   "floating this back up in case it got buried."]
    assert pools["followup_2"] == ["last bump from me on this. no worries if the timing's off."]


def test_select_bump_is_stable_per_contact_and_step():
    pool = ["a", "b", "c", "d"]
    first = select_bump(pool, "jordan@acme.example", "followup_1")
    again = select_bump(pool, "jordan@acme.example", "followup_1")
    assert first == again  # deterministic across calls/processes
    assert first in pool


def test_select_bump_varies_across_contacts():
    pool = ["a", "b", "c", "d", "e", "f", "g", "h"]
    picks = {select_bump(pool, f"person{i}@x.example", "followup_1") for i in range(8)}
    assert len(picks) > 1  # not all identical
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_followups.py -v`
Expected: FAIL (`src.lib.followups` / `parse_followup_pools` undefined).

- [ ] **Step 3: Implement** `src/lib/followups.py`:

```python
"""Deterministic follow-up bump selection. No LLM, no voice gate."""
from __future__ import annotations

import hashlib


def select_bump(pool: list[str], contact_email: str, step: str) -> str:
    """Pick a pool line that is stable for a given (contact, step) across processes.

    Uses hashlib (not the per-process-salted builtin hash) so re-running the loop
    selects the same line — required for idempotency.
    """
    if not pool:
        raise ValueError("follow-up pool is empty")
    key = f"{contact_email}|{step}".encode("utf-8")
    idx = int(hashlib.sha256(key).hexdigest(), 16) % len(pool)
    return pool[idx]
```

- [ ] **Step 4: Implement `parse_followup_pools` + `load_followup_pools`** in `src/lib/profile.py` (append):

```python
def parse_followup_pools(text: str) -> dict[str, list[str]]:
    """Parse a thread_followups.md into {'followup_1': [...], 'followup_2': [...]}.

    '## Email 2' -> followup_1 (first bump), '## Email 3' -> followup_2 (final note).
    Lines starting with '- ' are pool entries.
    """
    section_map = {"email 2": "followup_1", "email 3": "followup_2"}
    pools: dict[str, list[str]] = {"followup_1": [], "followup_2": []}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("##"):
            current = section_map.get(line.lstrip("#").strip().lower())
        elif line.startswith("- ") and current:
            pools[current].append(line[2:].strip())
    return pools


def load_followup_pools() -> dict[str, list[str]]:
    """Load the per-user bump pool from Profile/thread_followups.md."""
    cfg = load_config()
    text = _read(cfg.profile_dir, "thread_followups.md")
    return parse_followup_pools(text)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_followups.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Commit**

```bash
git add src/lib/followups.py src/lib/profile.py tests/test_followups.py
git commit -m "feat(step7): deterministic follow-up pool loading + selection

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Config — `step7_sheet_tab` + `enable_followups`

**Files:**
- Modify: `src/lib/config.py`
- Test: `tests/test_config_step7.py`

- [ ] **Step 1: Write failing tests** in `tests/test_config_step7.py`:

```python
"""Step 7 config additions."""
from __future__ import annotations

from src.lib.config import load_config


def test_step7_tab_falls_back_to_sheet_tab(monkeypatch):
    monkeypatch.delenv("STEP7_SHEET_TAB", raising=False)
    monkeypatch.setenv("SHEET_TAB_NAME", "Outreach")
    cfg = load_config()
    assert cfg.step7_sheet_tab == "Outreach"
    assert cfg.enable_followups is True


def test_step7_tab_override_and_followups_off(monkeypatch):
    monkeypatch.setenv("STEP7_SHEET_TAB", "Outreach_Step7_Test")
    monkeypatch.setenv("ENABLE_FOLLOWUPS", "false")
    cfg = load_config()
    assert cfg.step7_sheet_tab == "Outreach_Step7_Test"
    assert cfg.enable_followups is False
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_config_step7.py -v`
Expected: FAIL (`Config` has no `step7_sheet_tab`).

- [ ] **Step 3: Implement** in `src/lib/config.py`:

Add two fields to the `Config` dataclass (after `sheet_tab_name`):
```python
    step7_sheet_tab: str
    enable_followups: bool
```

In `load_config`, compute the tab name once and reuse it. Replace the `sheet_tab_name=...` line with:
```python
        sheet_tab_name=_env("SHEET_TAB_NAME", "Outreach"),
        step7_sheet_tab=_env("STEP7_SHEET_TAB", "") or _env("SHEET_TAB_NAME", "Outreach"),
        enable_followups=_env_bool("ENABLE_FOLLOWUPS", True),
```
(Place `step7_sheet_tab` and `enable_followups` in the `Config(...)` call in the same order as declared in the dataclass.)

- [ ] **Step 4: Run tests** (new + existing config-dependent suites)

Run: `uv run pytest tests/test_config_step7.py -v && uv run pytest -q`
Expected: PASS (new tests pass; full suite stays green).

- [ ] **Step 5: Commit**

```bash
git add src/lib/config.py tests/test_config_step7.py
git commit -m "feat(step7): config for STEP7_SHEET_TAB + ENABLE_FOLLOWUPS

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Ship the example pool + document env vars

**Files:**
- Create: `Profile.example/thread_followups.md`
- Modify: `.env.example`
- Test: `tests/test_followups.py` (add an example-file smoke test)

- [ ] **Step 1: Write failing test** (append to `tests/test_followups.py`):

```python
def test_example_pool_file_parses_nonempty():
    from pathlib import Path
    from src.lib.config import REPO_ROOT
    from src.lib.profile import parse_followup_pools
    text = (REPO_ROOT / "Profile.example" / "thread_followups.md").read_text(encoding="utf-8")
    pools = parse_followup_pools(text)
    assert pools["followup_1"] and pools["followup_2"]
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_followups.py::test_example_pool_file_parses_nonempty -v`
Expected: FAIL (file does not exist).

- [ ] **Step 3: Create** `Profile.example/thread_followups.md` (fictional, generic — keep it free of any real persona detail, consistent with the repo's privacy guarantees):

```markdown
# Thread follow-up bumps (example)

One-line bumps the loop stages as reply drafts when a follow-up is due. Edit freely;
keep them short and human. Email 2 = first nudge, Email 3 = graceful final note.

## Email 2
- just bumping this up for you.
- bringing this back to the top of your inbox.
- floating this back up in case it got buried.
- following up here in case this slipped by.

## Email 3
- last bump from me on this. no worries if the timing's off.
- closing the loop on my end. happy to reconnect whenever it makes sense.
- I will leave it here for now. would still love to connect down the line.
```

- [ ] **Step 4: Document env vars** in `.env.example` — add under the "Send schedule (Step 7)" area:

```bash
# Step 7 loop. STEP7_SHEET_TAB selects which tab the run-loop reads/writes.
# RECOMMENDED during rollout: point this at a COPY of your tab (e.g. Outreach_Step7_Test)
# so the loop can't touch real rows; flip it to your real tab once proven.
STEP7_SHEET_TAB=
ENABLE_FOLLOWUPS=true
```

- [ ] **Step 5: Run tests + privacy scan**

Run: `uv run pytest tests/test_followups.py -v && python3 scripts/pre_publish_scan.py`
Expected: PASS + `[OK] scan clean`.

- [ ] **Step 6: Commit**

```bash
git add Profile.example/thread_followups.md .env.example tests/test_followups.py
git commit -m "feat(step7): ship example follow-up pool + document env vars

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Recording sends + scheduling (pure helpers in `loop.py`)

**Files:**
- Modify: `src/loop.py`
- Test: `tests/test_loop.py`

These are pure functions (no Gmail/Sheets calls) that compute the field changes. Keeping
them pure makes them trivial to test and keeps the tick wiring (Task 10) thin.

- [ ] **Step 1: Write failing tests** in `tests/test_loop.py`:

```python
"""Step 7 loop helpers + tick."""
from __future__ import annotations

from datetime import datetime

from src.lib.models import StagedRow
from src.lib.send_detect import SendEvent
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
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_loop.py -v`
Expected: FAIL (`record_send_fields` / `followup_due` / `followup_step` undefined).

- [ ] **Step 3: Implement** the pure helpers in `src/loop.py` (above `main`):

```python
from datetime import datetime, timedelta

from src.lib.models import StagedRow
from src.lib.send_detect import SendEvent

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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_loop.py -v`
Expected: PASS (all six helper tests).

- [ ] **Step 5: Commit**

```bash
git add src/loop.py tests/test_loop.py
git commit -m "feat(step7): pure record-send + follow-up-scheduling helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: The `run-loop` tick (wiring, isolation, dry-run, --init-headers)

**Files:**
- Modify: `src/loop.py`
- Test: `tests/test_loop.py`

- [ ] **Step 1: Write failing tests** (append to `tests/test_loop.py`):

```python
import src.loop as loop_mod


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
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_loop.py -k tick -v`
Expected: FAIL (`run_tick`, `make_detector`, import names undefined).

- [ ] **Step 3: Implement the tick** in `src/loop.py`. Replace the file's imports + `main` with the full wiring (keep the helpers from Task 9). The top of the file becomes:

```python
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
```

Keep the Task-9 helpers (`record_send_fields`, `followup_step`, `followup_due`, the
`_STEP_*` maps). Then add the tick + subject caching + follow-up staging + CLI:

```python
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
    return {"Followup Draft ID": draft_id, "Next Action": f"Send {step.replace('_', ' ').title()}"}


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
                changed.update(record_send_fields(row, event, cfg))
                # reflect the send on the in-memory row so we don't also stage this tick
                _apply_to_row(row, event)
            if cfg.enable_followups and followup_due(row, now=now):
                changed.update(_stage_followup_fields(row, pools, service))
        except Exception as exc:  # isolate the row, keep the tick going
            changed = {"Step7 Error": f"{type(exc).__name__}: {exc}"[:300]}
            log.warning("row %d failed: %s", row_number, exc)
        if changed:
            if dry_run:
                click.echo(f"[dry-run] row {row_number}: {changed}")
            else:
                update_row(row_number, changed)


def _apply_to_row(row: StagedRow, event: SendEvent) -> None:
    """Mirror a detected send onto the in-memory row so follow-up logic sees fresh state."""
    setattr(row, {"email_1": "email_1_sent", "followup_1": "email_2_sent", "followup_2": "email_3_sent"}[event.step], event.sent_at)
    if event.step == "email_1":
        row.gmail_message_id = event.message_id
        row.gmail_thread_id = event.thread_id
        row.gmail_draft_id = None
    else:
        row.followup_draft_id = None
    row.last_gmail_message_id = event.message_id


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
```

> Note: after a same-tick send detection, `_apply_to_row` updates the in-memory row so
> `followup_due` does **not** also stage a follow-up in the same tick (it would compute a
> brand-new `next_action_date` in the future). The next tick handles staging.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_loop.py -v`
Expected: PASS (all tick + helper tests).

- [ ] **Step 5: Full suite + lint**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: all tests pass; `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add src/loop.py tests/test_loop.py
git commit -m "feat(step7): run-loop tick — cache/detect/record/stage with per-row isolation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Docs + final verification

**Files:**
- Modify: `README.md` (status checklist), `CLAUDE.md` (commands)

- [ ] **Step 1: Update `README.md`** — in "What v1 does NOT do", change the Step 7 line to reflect Phase 1 landing:

```markdown
- Step 7 Phase 1 (manual-send detection + pooled follow-ups) is built; reply tracking,
  OOO/bounce handling, LLM reply drafts, and Step 8 (launchd) are still deferred.
```

- [ ] **Step 2: Add commands to `CLAUDE.md`** under the Commands block:

```bash
uv run run-loop                 # Step 7: one tick (detect manual sends, stage follow-ups; never sends)
uv run run-loop --dry-run       # print planned writes, change nothing
uv run run-loop --init-headers  # add the 9 Step 7 columns to the tab
```

- [ ] **Step 3: Final verification**

Run: `uv run pytest -q && uv run ruff check src tests && python3 scripts/pre_publish_scan.py`
Expected: all tests pass; lint clean; `[OK] scan clean`.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs(step7): mark Phase 1 built; document run-loop commands

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-review notes (author)

- **Spec coverage:** Gmail fetch layer → Task 4 (send-detection subset; `get_message_body`/`walk_thread` explicitly deferred, consistent with the spec's Phase-1 success criteria which need neither). Sheet read/write + migration → Tasks 1–3. Manual-send detection (seam + polling) → Tasks 1, 5. Pooled follow-ups → Tasks 6, 8, 9, 10. Tick + isolation + dry-run + `--init-headers` → Task 10. Config + example files → Tasks 7, 8. Status lifecycle + cadence (§6.1/§6.2) → Task 9 helpers. Idempotency → `followup_due` (no double-stage), `_apply_to_row` (no same-tick double action), stable `select_bump`. Guardrail → no `send` call anywhere; only `drafts().create`.
- **Type consistency:** `SendEvent(message_id, thread_id, sent_at, step)` used identically in Tasks 5, 9, 10. `update_row(n, fields)` and `read_queue() -> list[tuple[int, StagedRow]]` consistent across Tasks 3, 10. `record_send_fields`/`followup_due`/`followup_step` signatures match between Tasks 9 and 10. Column names in `record_send_fields` all exist in `SHEET_HEADERS` (Task 1).
- **Known deviation from spec (intentional, YAGNI):** `get_message_body` and full `walk_thread` are NOT built in Phase 1 (no Phase-1 caller). They land in Phase 2 with reply/OOO detection. The `Reply Draft ID` column is created but left blank until Phase 2.
