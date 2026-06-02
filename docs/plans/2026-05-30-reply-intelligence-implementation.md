# Reply-Intelligence (Step 7 Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reply intelligence to `run-loop` — each tick, classify the latest inbound message in a thread (genuine reply / out-of-office / bounce) and act: stop follow-ups on a real reply, stage an LLM reply draft in the user's thread voice, defer follow-ups around an OOO return date, and flag bounces — never sending.

**Architecture:** Pure helper modules (`classify.py`, `ooo.py`) feed a new per-row branch in `run_tick`. Gmail gains a full-body fetch + a thread walker that returns the latest inbound message as an `InboundMessage`. Reply drafts mirror `draft_outreach.py` exactly (one Claude call, 3-attempt voice gate) but use a separate **thread voice** and fall back to a deterministic template; transient API failures raise `ReplyGenerationError` so the row retries next tick.

**Tech Stack:** Python 3.11+, Gmail API (`googleapiclient`), Anthropic SDK, `pytest` with mocked Gmail/Anthropic, `click` CLI, `uv`.

**Spec:** `docs/specs/2026-05-30-reply-intelligence-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/lib/config.py` (modify) | 4 new flags on `Config` + `load_config`. |
| `src/lib/models.py` (modify) | `InboundMessage` dataclass (the classifier's input). |
| `src/lib/gmail.py` (modify) | `get_message_body`, `get_latest_inbound`, `_extract_text_body` helper. |
| `src/lib/ooo.py` (new) | `parse_return_date` — pure date parser. |
| `src/lib/classify.py` (new) | `classify_inbound`, `is_bounce`, `is_auto_reply` — pure classifiers. |
| `src/lib/profile.py` (modify) | `ThreadPack` + `load_thread_pack`. |
| `src/lib/voice_rules.py` (modify) | `check_reply` gate (20–100 words). |
| `src/lib/reply_drafts.py` (new) | `generate_reply` + `ReplyGenerationError` + template fallback. |
| `src/loop.py` (modify) | `_handle_inbound` + wiring in `run_tick`. |
| `Profile.example/thread_voice.md`, `thread_drafts.md` (new) | fictional thread-voice reference. |
| `CHANGELOG.md`, `README.md`, `pyproject.toml`, `uv.lock` (modify) | 0.3.0 release docs. |

Test files: `tests/test_config_step7.py` (extend), `tests/test_gmail.py` (extend), `tests/test_ooo.py` (new), `tests/test_classify.py` (new), `tests/test_profile.py` (extend or new), `tests/test_voice_rules.py` (extend), `tests/test_reply_drafts.py` (new), `tests/test_loop.py` (extend).

**Cross-task contract (use these exact names everywhere):**
- `InboundMessage(sender: str, subject: str, headers: dict[str, str], body: str, internal_date_ms: int)` — `headers` keys are **lowercased**.
- `classify_inbound(msg: InboundMessage) -> str` → one of `"genuine"`, `"auto_reply"`, `"bounce"`.
- `parse_return_date(text: str, *, today: date) -> date | None`.
- `check_reply(text: str, *, config: VoiceConfig) -> VoiceCheckResult`.
- `generate_reply(*, inbound_body: str, first_name: str, max_attempts: int = 3) -> str`.
- `get_latest_inbound(thread_id: str, our_address: str, service=None) -> InboundMessage | None`.

**🔒 Privacy (enforced by the pre-push hook):** every fictional email in tests/fixtures MUST use a
scanner-allowlisted domain — `@example.com` or `@acme.example` (never a real-looking domain like
`@acme.com`/`@gmail.com`). Commit only as `rsg9999 <146885908+rsg9999@users.noreply.github.com>`.

---

## Task 1: Config flags

**Files:**
- Modify: `src/lib/config.py`
- Test: `tests/test_config_step7.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_step7.py  (add these tests)
from src.lib.config import load_config


def test_reply_flags_default_true_and_defer_days(monkeypatch):
    for var in ("ENABLE_REPLY_TRACKING", "ENABLE_REPLY_DRAFTS", "REPLY_USE_LLM", "OOO_DEFER_DAYS"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.enable_reply_tracking is True
    assert cfg.enable_reply_drafts is True
    assert cfg.reply_use_llm is True
    assert cfg.ooo_defer_days == 5


def test_reply_flags_env_overrides(monkeypatch):
    monkeypatch.setenv("ENABLE_REPLY_TRACKING", "false")
    monkeypatch.setenv("REPLY_USE_LLM", "0")
    monkeypatch.setenv("OOO_DEFER_DAYS", "3")
    cfg = load_config()
    assert cfg.enable_reply_tracking is False
    assert cfg.reply_use_llm is False
    assert cfg.ooo_defer_days == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_step7.py -k reply_flags -v`
Expected: FAIL — `Config` has no attribute `enable_reply_tracking`.

- [ ] **Step 3: Add the fields to `Config` and `load_config`**

In `src/lib/config.py`, add to the `Config` dataclass right after `enable_followups: bool` (line 44):

```python
    enable_reply_tracking: bool
    enable_reply_drafts: bool
    reply_use_llm: bool
    ooo_defer_days: int
```

And in `load_config()`, add right after the `enable_followups=...` line (line 96):

```python
        enable_reply_tracking=_env_bool("ENABLE_REPLY_TRACKING", True),
        enable_reply_drafts=_env_bool("ENABLE_REPLY_DRAFTS", True),
        reply_use_llm=_env_bool("REPLY_USE_LLM", True),
        ooo_defer_days=int(_env("OOO_DEFER_DAYS", "5")),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config_step7.py -k reply_flags -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/lib/config.py tests/test_config_step7.py
git commit -m "feat(step7): config flags for reply tracking/drafts/LLM + OOO defer days"
```

---

## Task 2: `InboundMessage` model + Gmail full-body fetch + thread walker

**Files:**
- Modify: `src/lib/models.py`, `src/lib/gmail.py`
- Test: `tests/test_gmail.py`

- [ ] **Step 1: Add the `InboundMessage` model to `models.py`**

`models.py` uses **pydantic** `BaseModel` (already imported). Add at the end of `src/lib/models.py`:

```python
class InboundMessage(BaseModel):
    """The latest inbound (not-from-us) message in a thread, normalized for classification."""
    sender: str                      # raw From header, e.g. 'Jane Doe <jane@acme.example>'
    subject: str
    headers: dict[str, str]          # header name (lowercased) -> value
    body: str                        # decoded text/plain body
    internal_date_ms: int            # Gmail internalDate (ms since epoch)
```

- [ ] **Step 2: Write the failing test for `get_message_body` + `get_latest_inbound`**

```python
# tests/test_gmail.py  (add)
import base64
from src.lib import gmail
from src.lib.models import InboundMessage


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii")


class _FakeThreads:
    def __init__(self, resp):
        self._resp = resp
    def get(self, **kwargs):
        class _Exec:
            def __init__(self, r): self._r = r
            def execute(self): return self._r
        return _Exec(self._resp)


class _FakeUsers:
    def __init__(self, thread_resp):
        self._thread_resp = thread_resp
    def threads(self):
        return _FakeThreads(self._thread_resp)


class _FakeService:
    def __init__(self, thread_resp):
        self._u = _FakeUsers(thread_resp)
    def users(self):
        return self._u


def _msg(msg_id, frm, subject, body, internal_ms, extra_headers=None):
    headers = [
        {"name": "From", "value": frm},
        {"name": "Subject", "value": subject},
    ]
    for k, v in (extra_headers or {}).items():
        headers.append({"name": k, "value": v})
    return {
        "id": msg_id,
        "threadId": "T1",
        "internalDate": str(internal_ms),
        "payload": {"headers": headers, "mimeType": "text/plain", "body": {"data": _b64(body)}},
    }


def test_get_latest_inbound_picks_newest_not_from_us():
    thread = {"messages": [
        _msg("m1", "me@example.com", "Re: the role", "my outgoing", 1000),
        _msg("m2", "Jane <jane@acme.example>", "Re: the role", "Sounds good, send times", 3000),
        _msg("m3", "me@example.com", "Re: the role", "following up", 2000),
    ]}
    svc = _FakeService(thread)
    inbound = gmail.get_latest_inbound("T1", "me@example.com", service=svc)
    assert isinstance(inbound, InboundMessage)
    assert inbound.sender == "Jane <jane@acme.example>"
    assert inbound.body == "Sounds good, send times"
    assert inbound.internal_date_ms == 3000
    assert inbound.headers["subject"] == "Re: the role"


def test_get_latest_inbound_returns_none_when_only_our_messages():
    thread = {"messages": [_msg("m1", "me@example.com", "the role", "hi", 1000)]}
    svc = _FakeService(thread)
    assert gmail.get_latest_inbound("T1", "me@example.com", service=svc) is None


def test_get_message_body_decodes_full_message():
    one = _msg("m1", "jane@acme.example", "Re: the role", "Hello there friend", 4242)

    class _Msgs:
        def get(self, **kwargs):
            class _Exec:
                def execute(self_inner): return one
            return _Exec()

    class _U:
        def messages(self): return _Msgs()

    class _S:
        def users(self): return _U()

    body, ms = gmail.get_message_body("m1", service=_S())
    assert body == "Hello there friend"
    assert ms == 4242
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_gmail.py -k latest_inbound -v`
Expected: FAIL — `gmail` has no attribute `get_latest_inbound`.

- [ ] **Step 4: Implement the helpers in `gmail.py`**

Add to `src/lib/gmail.py` (it already has `_header`, `_get_gmail_service`, and imports `base64`):

```python
from src.lib.models import InboundMessage


def _extract_text_body(payload: dict) -> str:
    """Decode the text/plain body from a Gmail message payload (full format)."""
    def _decode(b64data: str) -> str:
        return base64.urlsafe_b64decode(b64data.encode("ascii")).decode("utf-8", errors="replace")

    parts = payload.get("parts")
    if parts:
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data")
                if data:
                    return _decode(data)
        # nested multipart: recurse into the first part that has its own parts
        for part in parts:
            if part.get("parts"):
                nested = _extract_text_body(part)
                if nested:
                    return nested
        return ""
    data = payload.get("body", {}).get("data")
    return _decode(data) if data else ""


def get_message_body(message_id: str, service=None) -> tuple[str, int]:
    """Fetch one message in full format; return (decoded text/plain body, internalDate ms)."""
    service = service or _get_gmail_service()
    resp = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    return _extract_text_body(resp["payload"]), int(resp["internalDate"])


def get_latest_inbound(thread_id: str, our_address: str, service=None) -> InboundMessage | None:
    """Return the most recent thread message whose From is NOT our_address, or None."""
    service = service or _get_gmail_service()
    resp = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    ours = our_address.lower()
    inbound = [
        m for m in resp.get("messages", [])
        if ours not in (_header(m["payload"].get("headers", []), "From") or "").lower()
    ]
    if not inbound:
        return None
    latest = max(inbound, key=lambda m: int(m["internalDate"]))
    headers_list = latest["payload"].get("headers", [])
    headers = {h["name"].lower(): h["value"] for h in headers_list}
    return InboundMessage(
        sender=_header(headers_list, "From") or "",
        subject=_header(headers_list, "Subject") or "",
        headers=headers,
        body=_extract_text_body(latest["payload"]),
        internal_date_ms=int(latest["internalDate"]),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_gmail.py -k "latest_inbound or message_body" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lib/models.py src/lib/gmail.py tests/test_gmail.py
git commit -m "feat(step7): InboundMessage + Gmail full-body fetch + latest-inbound thread walker"
```

---

## Task 3: `ooo.py` — return-date parser

**Files:**
- Create: `src/lib/ooo.py`
- Test: `tests/test_ooo.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ooo.py
from datetime import date
from src.lib.ooo import parse_return_date

TODAY = date(2026, 6, 1)


def test_month_day():
    assert parse_return_date("I'll be back on June 3.", today=TODAY) == date(2026, 6, 3)

def test_day_month():
    assert parse_return_date("Returning 3 June.", today=TODAY) == date(2026, 6, 3)

def test_numeric_md():
    assert parse_return_date("back on 6/9", today=TODAY) == date(2026, 6, 9)

def test_numeric_mdy():
    assert parse_return_date("back 06/09/2026", today=TODAY) == date(2026, 6, 9)

def test_iso():
    assert parse_return_date("returning 2026-06-09", today=TODAY) == date(2026, 6, 9)

def test_ignores_departure_date_takes_return():
    # "out 6/2 through 6/9" -> the return is 6/9, not the departure 6/2
    assert parse_return_date("I am out 6/2 through 6/9.", today=TODAY) == date(2026, 6, 9)

def test_latest_of_many():
    assert parse_return_date("away 6/3, 6/15, and 6/9", today=TODAY) == date(2026, 6, 15)

def test_until_further_notice_is_none():
    assert parse_return_date("Out of office until further notice.", today=TODAY) is None

def test_no_date_is_none():
    assert parse_return_date("Thanks for your email.", today=TODAY) is None

def test_bare_month_day_rolls_to_next_year_if_past():
    # Jan 5 is before today (June 1 2026) -> next occurrence is 2027
    assert parse_return_date("back on January 5", today=TODAY) == date(2027, 1, 5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ooo.py -v`
Expected: FAIL — `No module named 'src.lib.ooo'`.

- [ ] **Step 3: Implement `ooo.py`**

```python
# src/lib/ooo.py
"""Parse an out-of-office return date from message text. Pure, naive-local."""
from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_NO_DATE = re.compile(r"until further notice|indefinitely", re.IGNORECASE)
_MONTH_NAME = "|".join(_MONTHS)
_RE_MONTH_DAY = re.compile(rf"\b({_MONTH_NAME})\s+(\d{{1,2}})\b", re.IGNORECASE)
_RE_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_NAME})\b", re.IGNORECASE)
_RE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_RE_MDY = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_RE_MD = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")


def _roll(year_month_day: tuple[int, int, int] | None, *, today: date, has_year: bool) -> date | None:
    if year_month_day is None:
        return None
    y, m, d = year_month_day
    try:
        candidate = date(y, m, d)
    except ValueError:
        return None
    if not has_year and candidate < today:
        try:
            candidate = date(y + 1, m, d)
        except ValueError:
            return None
    return candidate


def parse_return_date(text: str, *, today: date) -> date | None:
    """Latest plausible return date in `text`, or None. Bare M/D and Month-D roll to the
    next occurrence on/after `today`. 'until further notice' / 'indefinitely' -> None."""
    if _NO_DATE.search(text):
        return None

    found: list[date] = []

    for m in _RE_ISO.finditer(text):
        d = _roll((int(m.group(1)), int(m.group(2)), int(m.group(3))), today=today, has_year=True)
        if d:
            found.append(d)
    for m in _RE_MDY.finditer(text):
        d = _roll((int(m.group(3)), int(m.group(1)), int(m.group(2))), today=today, has_year=True)
        if d:
            found.append(d)
    for m in _RE_MD.finditer(text):
        # skip if this slash-date is actually the M/D/YYYY already captured (overlap): the
        # YYYY group means _RE_MD also matches the leading M/D; dedup by value below.
        d = _roll((today.year, int(m.group(1)), int(m.group(2))), today=today, has_year=False)
        if d:
            found.append(d)
    for m in _RE_MONTH_DAY.finditer(text):
        d = _roll((today.year, _MONTHS[m.group(1).lower()], int(m.group(2))), today=today, has_year=False)
        if d:
            found.append(d)
    for m in _RE_DAY_MONTH.finditer(text):
        d = _roll((today.year, _MONTHS[m.group(2).lower()], int(m.group(1))), today=today, has_year=False)
        if d:
            found.append(d)

    if not found:
        return None
    return max(found)
```

Note on "ignore the departure date / take the latest": both the departure and return dates are
collected; `max(found)` returns the later one, which for OOO phrasing ("out X through Y") is the
return date Y. The `test_ignores_departure_date_takes_return` and `test_latest_of_many` tests pin
this behavior.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ooo.py -v`
Expected: PASS (all 10). If `test_numeric_mdy` double-counts via `_RE_MD`, the `max()` still
returns the correct date because both resolve to 6/9; no change needed.

- [ ] **Step 5: Commit**

```bash
git add src/lib/ooo.py tests/test_ooo.py
git commit -m "feat(step7): OOO return-date parser (ooo.py)"
```

---

## Task 4: `classify.py` — inbound classifier

**Files:**
- Create: `src/lib/classify.py`
- Test: `tests/test_classify.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_classify.py
from src.lib.classify import classify_inbound, is_bounce, is_auto_reply
from src.lib.models import InboundMessage


def _msg(sender="Jane <jane@acme.example>", subject="Re: the role", headers=None, body="hi"):
    base = {"from": sender, "subject": subject}
    base.update(headers or {})
    return InboundMessage(sender=sender, subject=subject, headers=base, body=body, internal_date_ms=1000)


def test_genuine_reply():
    assert classify_inbound(_msg(body="Sounds good, let's talk Tuesday.")) == "genuine"

def test_bounce_by_sender():
    m = _msg(sender="Mail Delivery Subsystem <mailer-daemon@acme.example>", subject="Delivery Status Notification (Failure)")
    assert is_bounce(m) is True
    assert classify_inbound(m) == "bounce"

def test_bounce_by_content_type():
    m = _msg(headers={"content-type": 'multipart/report; report-type=delivery-status; boundary="x"'})
    assert classify_inbound(m) == "bounce"

def test_auto_reply_by_header():
    m = _msg(headers={"auto-submitted": "auto-replied"})
    assert is_auto_reply(m) is True
    assert classify_inbound(m) == "auto_reply"

def test_auto_reply_by_subject():
    m = _msg(subject="Automatic reply: Out of office")
    assert classify_inbound(m) == "auto_reply"

def test_bounce_wins_over_auto_headers():
    # a bounce that also carries auto-ish headers is still a bounce (precedence)
    m = _msg(sender="postmaster@acme.example", subject="Undelivered Mail Returned to Sender",
             headers={"auto-submitted": "auto-replied"})
    assert classify_inbound(m) == "bounce"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_classify.py -v`
Expected: FAIL — `No module named 'src.lib.classify'`.

- [ ] **Step 3: Implement `classify.py`**

```python
# src/lib/classify.py
"""Classify the latest inbound message in a thread: genuine / auto_reply / bounce.

Precedence: bounce -> auto_reply -> genuine. A bounce that also carries auto-reply
headers is still a bounce.
"""
from __future__ import annotations

import re
from email.utils import parseaddr

from src.lib.models import InboundMessage

_BOUNCE_LOCALPARTS = {"mailer-daemon", "postmaster"}
_BOUNCE_SUBJECTS = re.compile(
    r"delivery status notification|undelivered mail returned to sender|mail delivery failed",
    re.IGNORECASE,
)
_AUTO_SUBJECTS = re.compile(
    r"out of office|automatic reply|auto-?reply|on leave|on vacation|away from(?: the)? office",
    re.IGNORECASE,
)
_AUTO_PRECEDENCE = {"bulk", "auto_reply"}


def is_bounce(msg: InboundMessage) -> bool:
    localpart = parseaddr(msg.sender)[1].split("@", 1)[0].lower()
    if localpart in _BOUNCE_LOCALPARTS:
        return True
    if "report-type=delivery-status" in msg.headers.get("content-type", "").lower():
        return True
    return bool(_BOUNCE_SUBJECTS.search(msg.subject))


def is_auto_reply(msg: InboundMessage) -> bool:
    auto_submitted = msg.headers.get("auto-submitted", "").lower()
    if auto_submitted and auto_submitted != "no":
        return True
    if msg.headers.get("precedence", "").lower() in _AUTO_PRECEDENCE:
        return True
    if any(h in msg.headers for h in ("x-autoreply", "x-autorespond", "x-auto-response-suppress")):
        return True
    return bool(_AUTO_SUBJECTS.search(msg.subject))


def classify_inbound(msg: InboundMessage) -> str:
    if is_bounce(msg):
        return "bounce"
    if is_auto_reply(msg):
        return "auto_reply"
    return "genuine"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_classify.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: Commit**

```bash
git add src/lib/classify.py tests/test_classify.py
git commit -m "feat(step7): inbound message classifier (genuine/auto_reply/bounce)"
```

---

## Task 5: `load_thread_pack` + example thread-voice files

**Files:**
- Modify: `src/lib/profile.py`
- Create: `Profile.example/thread_voice.md`, `Profile.example/thread_drafts.md`
- Test: `tests/test_profile.py` (create if absent)

- [ ] **Step 1: Create the example thread-voice files**

`Profile.example/thread_voice.md`:

```markdown
# Thread voice (replies & follow-ups)

How I sound when someone has *already replied* — warmer and shorter than a cold email.

- 20-100 words. Usually 2-4 sentences.
- Match their energy. If they were brief, be brief.
- No em dashes. No "just circling back", "leverage", "passionate about".
- One clear next step (a time, a link, an answer). Never two asks.
- Plain sign-off with my first name only. No "Best regards".
```

`Profile.example/thread_drafts.md`:

```markdown
# Example reply drafts (thread voice)

Reference replies in my voice. Facts here are illustrative for the fictional persona.

---
They asked for times:
"Tuesday or Thursday afternoon both work my end. Want me to send a calendar invite, or
easier to grab 15 minutes off a Loom I can record first?"

---
They said "send more info":
"Sure thing. The one that maps closest to your post is the onboarding rebuild I ran at
Northwind that cut activation time 40%. Happy to walk through how it'd apply to your team."
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_profile.py  (add; uses the committed Profile.example via PROFILE_DIR override)
from pathlib import Path
import pytest
from src.lib.profile import load_thread_pack, ThreadPack


@pytest.fixture
def example_profile(monkeypatch):
    monkeypatch.setenv("PROFILE_DIR", "Profile.example")
    load_thread_pack.cache_clear()
    yield
    load_thread_pack.cache_clear()


def test_load_thread_pack_includes_thread_files_excludes_voice(example_profile):
    pack = load_thread_pack()
    assert isinstance(pack, ThreadPack)
    assert "Thread voice" in pack.thread_voice
    assert pack.thread_drafts.strip() != ""
    block = pack.as_prompt_block()
    # factual files included
    assert pack.resume in block
    # cold-email voice is intentionally excluded from the thread pack
    assert not hasattr(pack, "voice")
    assert not hasattr(pack, "past_drafts")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_profile.py -k thread_pack -v`
Expected: FAIL — cannot import `load_thread_pack` / `ThreadPack`.

- [ ] **Step 4: Implement in `profile.py`**

`profile.py` already has `_read(profile_dir, name)`, `load_config()`, and `@lru_cache`. Add:

```python
@dataclass
class ThreadPack:
    """Profile context for reply/follow-up drafting: thread voice + factual files.
    Deliberately EXCLUDES voice.md and past_drafts.md (cold-email voice) to avoid
    polluting the warmer thread tone."""
    thread_voice: str
    thread_drafts: str
    resume: str
    proof_points: str
    narrative: str

    def as_prompt_block(self) -> str:
        return (
            "THREAD VOICE\n============\n" + self.thread_voice
            + "\n\nEXAMPLE REPLIES\n===============\n" + self.thread_drafts
            + "\n\nRESUME (facts only)\n===================\n" + self.resume
            + "\n\nPROOF POINTS (facts only, use at most one light detail)\n"
            + "======================================================\n" + self.proof_points
            + "\n\nNARRATIVE (facts only)\n======================\n" + self.narrative
        )


@lru_cache(maxsize=1)
def load_thread_pack() -> ThreadPack:
    cfg = load_config()
    pdir = cfg.profile_dir
    return ThreadPack(
        thread_voice=_read(pdir, "thread_voice"),
        thread_drafts=_read(pdir, "thread_drafts"),
        resume=_read(pdir, "resume"),
        proof_points=_read(pdir, "proof_points"),
        narrative=_read(pdir, "narrative"),
    )
```

`_read(profile_dir, name)` reads `<name>.md` and only checks that the file exists — there is **no
allowlist guard** (the `PROFILE_FILES` tuple is documentation, not enforcement). So the two new
`_read(pdir, "thread_voice")` / `_read(pdir, "thread_drafts")` calls work as written once the files
exist in the profile dir. No change to `_read` is needed.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_profile.py -k thread_pack -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lib/profile.py Profile.example/thread_voice.md Profile.example/thread_drafts.md tests/test_profile.py
git commit -m "feat(step7): load_thread_pack + example thread-voice files"
```

---

## Task 6: `check_reply` voice gate

**Files:**
- Modify: `src/lib/voice_rules.py`
- Test: `tests/test_voice_rules.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_voice_rules.py  (add)
from pathlib import Path

from src.lib.voice_rules import check_reply, load_voice_config


def _cfg():
    return load_voice_config(Path("Profile.example"))


def test_check_reply_accepts_clean_short_reply():
    text = ("Tuesday or Thursday afternoon both work my end. Want me to send a calendar "
            "invite, or easier to grab fifteen minutes off a Loom I can record first?")
    assert check_reply(text, config=_cfg()).ok


def test_check_reply_rejects_too_short():
    res = check_reply("Sounds good.", config=_cfg())
    assert not res.ok
    assert any("word" in f.lower() for f in res.failures)


def test_check_reply_rejects_em_dash_and_banned():
    res = check_reply("I would love to leverage this opportunity " * 6, config=_cfg())
    assert not res.ok
```

Ensure `from pathlib import Path` is present in the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_voice_rules.py -k check_reply -v`
Expected: FAIL — `cannot import name 'check_reply'`.

- [ ] **Step 3: Implement `check_reply` in `voice_rules.py`**

Mirror `check_email` but with only word-count (20–100) + em-dash + banned-phrase checks — no
subject, no signature requirement. `voice_rules.py` already defines `VoiceCheckResult` (constructed
`VoiceCheckResult(ok=..., failures=...)`) and `_flag_banned(text, config, failures) -> None`
(**mutates the `failures` list in place** — append-style, returns nothing). Reuse both exactly as
shown below.

```python
REPLY_WORD_MIN = 20
REPLY_WORD_MAX = 100


def check_reply(text: str, *, config: VoiceConfig) -> VoiceCheckResult:
    """Gate for reply drafts: 20-100 words, no em dash, no banned phrases. No signature rule."""
    failures: list[str] = []
    stripped = text.strip()
    if "—" in stripped:
        failures.append("contains em dash (—); use periods or commas")
    _flag_banned(stripped, config, failures)
    words = len(stripped.split())
    if words < REPLY_WORD_MIN:
        failures.append(f"body is {words} words; must be at least {REPLY_WORD_MIN}")
    if words > REPLY_WORD_MAX:
        failures.append(f"body is {words} words; must be at most {REPLY_WORD_MAX}")
    return VoiceCheckResult(ok=not failures, failures=failures)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_voice_rules.py -k check_reply -v`
Expected: PASS (all 3).

- [ ] **Step 5: Commit**

```bash
git add src/lib/voice_rules.py tests/test_voice_rules.py
git commit -m "feat(step7): check_reply voice gate (20-100 words)"
```

---

## Task 7: `reply_drafts.py` — LLM reply with gate + template fallback

**Files:**
- Create: `src/lib/reply_drafts.py`
- Test: `tests/test_reply_drafts.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reply_drafts.py
import pytest
from src.lib import reply_drafts
from src.lib.reply_drafts import generate_reply, ReplyGenerationError


@pytest.fixture(autouse=True)
def _example_profile(monkeypatch):
    monkeypatch.setenv("PROFILE_DIR", "Profile.example")


def _good_reply_json():
    return ('{"reply": "Tuesday or Thursday afternoon both work my end. Want me to send a '
            'calendar invite, or easier to grab fifteen minutes off a Loom I can record first?"}')


def test_generate_reply_returns_body_on_clean_gate(monkeypatch):
    monkeypatch.setenv("REPLY_USE_LLM", "true")
    monkeypatch.setattr(reply_drafts, "_call_claude_reply", lambda blocks, model: _good_reply_json())
    out = generate_reply(inbound_body="What times work for a call?", first_name="Jane")
    assert "Tuesday" in out


def test_generate_reply_template_when_llm_disabled(monkeypatch):
    monkeypatch.setenv("REPLY_USE_LLM", "false")
    out = generate_reply(inbound_body="thanks!", first_name="Jane")
    assert out.strip() != ""
    assert "Jane" in out


def test_generate_reply_template_after_gate_fails(monkeypatch):
    monkeypatch.setenv("REPLY_USE_LLM", "true")
    # always returns a too-short reply -> fails gate every attempt -> template fallback
    monkeypatch.setattr(reply_drafts, "_call_claude_reply", lambda blocks, model: '{"reply": "ok"}')
    out = generate_reply(inbound_body="hi", first_name="Jane", max_attempts=2)
    assert "Jane" in out  # template, not the rejected "ok"


def test_generate_reply_transient_error_raises(monkeypatch):
    monkeypatch.setenv("REPLY_USE_LLM", "true")
    def _boom(blocks, model):
        raise ReplyGenerationError("429 rate limited")
    monkeypatch.setattr(reply_drafts, "_call_claude_reply", _boom)
    with pytest.raises(ReplyGenerationError):
        generate_reply(inbound_body="hi", first_name="Jane")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reply_drafts.py -v`
Expected: FAIL — `No module named 'src.lib.reply_drafts'`.

- [ ] **Step 3: Implement `reply_drafts.py`**

```python
# src/lib/reply_drafts.py
"""Draft a reply in the user's THREAD voice. One Claude call + up to 3 voice-gate regens,
then a deterministic template fallback. Transient API failures raise ReplyGenerationError so
the loop leaves the row untouched and retries next tick. NEVER sends."""
from __future__ import annotations

import json
import logging

from src.lib.config import load_config
from src.lib.parse_job import _strip_code_fences
from src.lib.profile import load_thread_pack
from src.lib.voice_rules import VoiceConfig, check_reply, load_voice_config

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 60.0


class ReplyGenerationError(RuntimeError):
    """Transient failure (429/5xx/timeout). The loop should retry next tick, not fall back."""


def _call_claude_reply(prompt_blocks: list[dict], model: str) -> str:
    """One Anthropic call with a 60s timeout. Raises ReplyGenerationError on 429/5xx/timeout/conn
    (transient); any other (hard) error propagates to the caller, which falls back to a template."""
    from anthropic import (
        Anthropic, APIConnectionError, APITimeoutError, InternalServerError, RateLimitError,
    )

    cfg = load_config()
    client = Anthropic(api_key=cfg.anthropic_api_key, timeout=_TIMEOUT_SECONDS)
    kwargs: dict = {"model": model, "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt_blocks}]}
    if "opus" not in model.lower():
        kwargs["temperature"] = 0.7
    try:
        msg = client.messages.create(**kwargs)
    except (APITimeoutError, RateLimitError, InternalServerError, APIConnectionError) as exc:
        raise ReplyGenerationError(f"transient API failure drafting reply: {exc}") from exc
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


def _build_blocks(*, voice_config: VoiceConfig, inbound_body: str, first_name: str,
                  prior_failures: list[str] | None) -> list[dict]:
    pack = load_thread_pack().as_prompt_block()
    static = f"""You are the writer described below, replying to someone who ALREADY replied to your cold email. Warmer and shorter than a cold email. Match their energy.

OUTPUT FORMAT
=============
A single valid JSON object, no prose outside it, no code fences:
{{"reply": "<20-100 words. Opens 'Hi {first_name},' or 'Hey {first_name},'. One clear next step. Sign off with your first name only.>"}}

VOICE RULES (enforced after output; regen on failure)
=====================================================
- 20-100 words. No em dashes. No "leverage", "passionate about", "circling back", "just following up".
- One ask, not two. Plain first-name sign-off.

{pack}
"""
    dynamic = f"THEIR MESSAGE (reply to this):\n{inbound_body[:2000]}\n\nNOW WRITE THE REPLY. Output JSON only.\n"
    if prior_failures:
        dynamic += ("\nPRIOR ATTEMPT FAILED THESE CHECKS. Fix every one:\n"
                    + "\n".join(f"  - {f}" for f in prior_failures) + "\n")
    return [
        {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic},
    ]


def _parse_reply(text: str) -> str:
    payload = json.loads(_strip_code_fences(text))
    return payload["reply"]


def _template_reply(first_name: str) -> str:
    cfg = load_config()
    name = (cfg.sender_name or "").split()[0] if cfg.sender_name else "me"
    return (
        f"Hi {first_name},\n\n"
        "Thanks so much for getting back to me, I really appreciate it. "
        "Happy to share more whenever is good for you, or hop on a quick call if that is easier. "
        "Just let me know what works.\n\n"
        f"{name}"
    )


def generate_reply(*, inbound_body: str, first_name: str, max_attempts: int = 3) -> str:
    """Return a reply-draft body. Template if REPLY_USE_LLM=false, gate-fails, or a hard API
    error. Raises ReplyGenerationError on a transient API failure (retry next tick)."""
    cfg = load_config()
    if not cfg.reply_use_llm:
        return _template_reply(first_name)

    voice_cfg = load_voice_config(cfg.profile_dir)
    failures: list[str] | None = None
    last_failures: list[str] = []
    for attempt in range(1, max_attempts + 1):
        blocks = _build_blocks(voice_config=voice_cfg, inbound_body=inbound_body,
                               first_name=first_name, prior_failures=failures)
        try:
            raw = _call_claude_reply(blocks, cfg.draft_model)
        except ReplyGenerationError:
            raise  # transient -> propagate so the loop leaves the row and retries next tick
        except Exception as exc:  # hard, non-transient API error -> template, don't crash
            log.warning("reply draft hard API error attempt %d: %s; using template", attempt, exc)
            return _template_reply(first_name)
        try:
            body = _parse_reply(raw)
        except Exception as exc:
            last_failures = [f"output was not valid JSON: {exc}"]
            failures = last_failures
            continue
        check = check_reply(body, config=voice_cfg)
        if check.ok:
            log.info("reply draft accepted on attempt %d (%d words)", attempt, len(body.split()))
            return body
        last_failures = check.failures
        failures = check.failures
        log.warning("reply draft attempt %d failed gate: %s", attempt, check.failures)
    log.warning("reply draft failed gate in %d attempts; using template. last=%s",
                max_attempts, last_failures)
    return _template_reply(first_name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_reply_drafts.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
git add src/lib/reply_drafts.py tests/test_reply_drafts.py
git commit -m "feat(step7): thread-voice reply drafter with gate + template fallback"
```

---

## Task 8: Wire reply handling into `run_tick`

**Files:**
- Modify: `src/loop.py`
- Test: `tests/test_loop.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_loop.py  (add)
from datetime import datetime, timedelta
from src.lib.models import InboundMessage
import src.loop as loop


def _row(**over):
    from src.lib.models import StagedRow
    # StagedRow is pydantic; date_added/company/role/job_url/contact_name/title are required.
    base = dict(date_added=datetime(2026, 5, 28, 9, 0), company="Acme", role="growth eng",
                job_url="https://example.com/job", contact_name="Jane", title="Head of Growth",
                email="jane@acme.example", status="Email 1 Sent", gmail_thread_id="T1",
                gmail_subject="the role", email_1_sent=datetime(2026, 6, 1, 8, 0))
    base.update(over)
    return StagedRow(**base)


def _cfg(**over):
    c = loop.load_config()
    import dataclasses
    return dataclasses.replace(c, **over)


def test_genuine_reply_sets_replied_and_stages_draft(monkeypatch):
    row = _row()
    inbound = InboundMessage(sender="Jane <jane@acme.example>", subject="Re: the role",
                             headers={"from": "jane@acme.example"}, body="What times work?",
                             internal_date_ms=1_700_000_000_000)
    monkeypatch.setattr(loop, "get_latest_inbound", lambda *a, **k: inbound)
    monkeypatch.setattr(loop, "classify_inbound", lambda m: "genuine")
    monkeypatch.setattr(loop, "generate_reply", lambda **k: "Tuesday works great, sending an invite now. Talk soon.")
    monkeypatch.setattr(loop, "create_reply_draft", lambda **k: "DRAFT123")
    cfg = _cfg(enable_reply_tracking=True, enable_reply_drafts=True)
    fields = loop._handle_inbound(row, cfg, service=object(), now=datetime(2026, 6, 2, 9, 0))
    assert fields["Replied?"] is True
    assert "Reply Date" in fields
    assert fields["Reply Draft ID"] == "DRAFT123"


def test_bounce_flags_without_replied(monkeypatch):
    row = _row()
    inbound = InboundMessage(sender="mailer-daemon@acme.example", subject="Delivery Status Notification (Failure)",
                             headers={"from": "mailer-daemon@acme.example"}, body="failed", internal_date_ms=1)
    monkeypatch.setattr(loop, "get_latest_inbound", lambda *a, **k: inbound)
    monkeypatch.setattr(loop, "classify_inbound", lambda m: "bounce")
    cfg = _cfg(enable_reply_tracking=True)
    fields = loop._handle_inbound(row, cfg, service=object(), now=datetime(2026, 6, 2))
    assert fields.get("Step7 Error", "").startswith("bounce")
    assert "Replied?" not in fields


def test_ooo_defers_next_action_date(monkeypatch):
    row = _row()
    inbound = InboundMessage(sender="Jane <jane@acme.example>", subject="Automatic reply",
                             headers={"from": "jane@acme.example", "auto-submitted": "auto-replied"},
                             body="I am out, back on June 9.", internal_date_ms=1)
    monkeypatch.setattr(loop, "get_latest_inbound", lambda *a, **k: inbound)
    monkeypatch.setattr(loop, "classify_inbound", lambda m: "auto_reply")
    cfg = _cfg(enable_reply_tracking=True, ooo_defer_days=5)
    fields = loop._handle_inbound(row, cfg, service=object(), now=datetime(2026, 6, 2, 9, 0))
    assert fields["Next Action Date"].date().isoformat() == "2026-06-10"  # June 9 + 1 day
    assert "Replied?" not in fields
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_loop.py -k "genuine_reply or bounce_flags or ooo_defers" -v`
Expected: FAIL — `loop` has no attribute `_handle_inbound`.

- [ ] **Step 3: Implement `_handle_inbound` and wire it into `run_tick`**

Update the imports at the top of `src/loop.py` (the existing datetime import already provides
`datetime, timedelta`, both of which `_handle_inbound` uses — leave it as-is):

1. Change the existing gmail import line (currently
   `from src.lib.gmail import create_reply_draft, get_draft_subject, _get_gmail_service`) to add
   `get_latest_inbound`:

```python
from src.lib.gmail import create_reply_draft, get_draft_subject, get_latest_inbound, _get_gmail_service
```

2. Add these new imports alongside the other `from src.lib...` imports:

```python
from email.utils import parseaddr

from src.lib.classify import classify_inbound
from src.lib.ooo import parse_return_date
from src.lib.reply_drafts import ReplyGenerationError, generate_reply
```

Add the handler:

```python
_OOO_MAX_DEFER_DAYS = 90


def _handle_inbound(row: StagedRow, cfg, service, *, now: datetime) -> dict:
    """Classify the latest inbound message and return field changes. Empty dict = no action
    (let follow-up logic run). May raise ReplyGenerationError (caller retries next tick)."""
    inbound = get_latest_inbound(row.gmail_thread_id, cfg.sender_email, service=service)
    if inbound is None:
        return {}
    kind = classify_inbound(inbound)
    reply_dt = datetime.fromtimestamp(inbound.internal_date_ms / 1000)

    if kind == "bounce":
        msg = "bounce: address may be invalid"
        return {} if row.step7_error == msg else {"Step7 Error": msg}

    if kind == "auto_reply":
        ret = parse_return_date(inbound.body, today=now.date())
        if ret is None:
            return {"Next Action Date": now + timedelta(days=cfg.ooo_defer_days)}
        if (ret - now.date()).days > _OOO_MAX_DEFER_DAYS:
            return {"Step7 Error": f"OOO >90d (returns {ret.isoformat()}): manual review"}
        return {"Next Action Date": datetime.combine(ret + timedelta(days=1), datetime.min.time())}

    # genuine
    fields: dict = {}
    if cfg.enable_reply_drafts and not row.reply_draft_id:
        body = generate_reply(inbound_body=inbound.body,
                              first_name=(row.contact_name or "there").split()[0])
        to_addr = parseaddr(inbound.sender)[1] or row.email
        draft_id = create_reply_draft(
            thread_id=row.gmail_thread_id, to=to_addr,
            subject=row.gmail_subject or row.role, body=body, service=service,
        )
        fields["Reply Draft ID"] = draft_id
    fields["Replied?"] = True
    fields["Reply Date"] = reply_dt
    return fields
```

Now modify the per-row block in `run_tick`. Replace the existing `if event is not None: ... elif
cfg.enable_followups and followup_due(...)` branch with:

```python
            event: SendEvent | None = detector.detect(row, service)
            if event is not None:
                changed.update(record_send_fields(row, event, cfg))
            else:
                inbound_fields: dict = {}
                if (cfg.enable_reply_tracking and row.email_1_sent
                        and not row.replied and row.gmail_thread_id):
                    inbound_fields = _handle_inbound(row, cfg, service, now=now)
                    changed.update(inbound_fields)
                # Follow-up still runs unless a genuine reply landed (Replied?) or we deferred
                # for an OOO (Next Action Date). A bounce flag does NOT stop the sequence.
                blocked = "Replied?" in inbound_fields or "Next Action Date" in inbound_fields
                if not blocked and cfg.enable_followups and followup_due(row, now=now):
                    changed.update(_stage_followup_fields(row, pools, service))
```

And add a `ReplyGenerationError` catch in the per-row `try/except` so a transient failure leaves
the row untouched (retry next tick) instead of writing `Step7 Error`. Insert before the broad
`except Exception`:

```python
        except ReplyGenerationError:
            log.info("row %d: transient reply-draft failure; will retry next tick", row_number)
            continue
        except Exception as exc:  # isolate the row, keep the tick going
            changed = {"Step7 Error": f"{type(exc).__name__}: {exc}"[:300]}
            log.warning("row %d failed: %s", row_number, exc)
```

(`ReplyGenerationError` and `generate_reply` are imported in the import block above.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_loop.py -v`
Expected: PASS (new + existing loop tests).

- [ ] **Step 5: Run the full suite + lint**

Run: `uv run pytest && uv run ruff check src tests`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/loop.py tests/test_loop.py
git commit -m "feat(step7): wire reply detection into run_tick (stop follow-ups, draft, OOO defer, bounce)"
```

---

## Task 9: Release docs (0.3.0)

**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `pyproject.toml`, `uv.lock`

- [ ] **Step 1: Add the 0.3.0 CHANGELOG entry**

Insert above the `## [0.2.0]` line in `CHANGELOG.md`:

```markdown
## [0.3.0] - 2026-05-30

Step 7 Phase 2 — reply intelligence.

### Added
- **Reply detection** — each `run-loop` tick reads the latest inbound message in a thread and
  classifies it: a **genuine reply** marks the row `Replied?` and stops follow-ups; a **bounce**
  is flagged (`Step7 Error`) without stopping the sequence; an **out-of-office** defers the next
  follow-up to the sender's stated return date (or `OOO_DEFER_DAYS`, default 5).
- **LLM reply drafts** — when a genuine reply lands, the loop stages a reply draft in a separate
  **thread voice** (`Profile/thread_voice.md`, `Profile/thread_drafts.md`) for you to review,
  with a 20-100 word voice gate and a deterministic template fallback. Kill-switch
  `REPLY_USE_LLM=false`. Still **never sends**.
- **OOO return-date parser** (`src/lib/ooo.py`) and **inbound classifier** (`src/lib/classify.py`).
- **Gmail full-body fetch** (`get_message_body`) and a latest-inbound thread walker.
- **Config** — `ENABLE_REPLY_TRACKING`, `ENABLE_REPLY_DRAFTS`, `REPLY_USE_LLM`, `OOO_DEFER_DAYS`.

### Upgrading from 0.2.x
`cp Profile.example/thread_voice.md Profile/thread_voice.md` and
`cp Profile.example/thread_drafts.md Profile/thread_drafts.md`, then edit them in your reply voice.
No new Sheet columns are needed (Phase 1 already added them).
```

Update the reference links at the bottom: add
`[0.3.0]: https://github.com/rsg9999/gtm-outreach-agent-template/releases/tag/v0.3.0`.

- [ ] **Step 2: Update README "Step 7" blurb + "What v1 does NOT do"**

In `README.md`, in the "Step 7 (optional): the send/reply loop" section, replace this line:

> Point `STEP7_SHEET_TAB` at a test-copy of your tab while you trial it. Reply tracking, out-of-office/bounce handling, and LLM reply drafts arrive in a later release.

with:

> Point `STEP7_SHEET_TAB` at a test-copy of your tab while you trial it. It also detects genuine replies (stopping follow-ups), handles out-of-office and bounces, and stages a reply draft in your thread voice for you to review.

And in the "What v1 does NOT do" list, replace this bullet:

> - Step 7 Phase 1 (manual-send detection + pooled follow-ups) is built; reply tracking,
>   out-of-office/bounce handling, LLM reply drafts, and Step 8 (launchd) are still deferred.

with:

> - Step 7 reply intelligence (reply tracking, out-of-office/bounce handling, LLM reply drafts) is
>   built; Step 8 (launchd auto-scheduling) is still deferred.

- [ ] **Step 3: Bump version**

In `pyproject.toml`, `version = "0.2.0"` → `version = "0.3.0"`. Then `uv sync` to update `uv.lock`.

- [ ] **Step 4: Verify + commit**

```bash
uv run pytest && uv run ruff check src tests && uv run python scripts/pre_publish_scan.py
git add CHANGELOG.md README.md pyproject.toml uv.lock
git commit -m "docs(step7): 0.3.0 release notes — reply intelligence; version bump"
```

---

## Manual verification (cannot be unit-tested)

After the suite is green, before pointing it at the production tab, manually verify against a
**test-copy** Sheet tab + a real Gmail thread:
1. Reply to a staged thread by hand → next `run-loop` marks `Replied?`, stops follow-ups, stages a reply draft.
2. Trigger an OOO auto-reply → follow-up defers to the return date; not counted as a reply.
3. Send to a bad address → bounce flagged; sequence not marked replied.
4. `REPLY_USE_LLM=false` → reply draft uses the template.

Run each with `uv run run-loop --dry-run` first to inspect planned writes.
