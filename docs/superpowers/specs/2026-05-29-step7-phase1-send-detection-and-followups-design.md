# Step 7 — Phase 1: Manual-send detection + pooled follow-ups

**Status:** Design approved 2026-05-29. Awaiting spec review → implementation plan.
**Branch:** `feature/send-reply-loop`
**Author:** rsg9999 + Claude

---

## 1. Context

The repo automates job-search outreach in three phases (parse → find contacts in
Claude.ai chat → draft + stage Gmail drafts + log to a Google Sheet). Step 7 — the
send/reply/follow-up loop — is the next milestone. It is currently a stub
([src/loop.py](../../../src/loop.py)) with `NotImplementedError` placeholders in
[gmail.py](../../../src/lib/gmail.py) (`send_draft`, `has_reply`) and
[sheets.py](../../../src/lib/sheets.py) (`read_queue`, `update_row`).

The full Step 7 vision spans nine subsystems (send detection, reply tracking, LLM
reply drafts, follow-up bumps, out-of-office handling, bounce handling, a Gmail
fetch-layer fix, voice/data separation, robustness). That is too large for one
implementation plan, and the rollout philosophy is **prove the loop by hand before
automating it with launchd**. So the work is decomposed into sequential
spec → plan → build cycles. **This spec covers Phase 1 only.**

## 2. Core guardrail (non-negotiable)

**The loop never sends email and never auto-replies.** The only Gmail write it ever
performs is `users.drafts.create`. The human sends every email by hand from Gmail.
The loop's job is to *detect* that a send happened, record it, and stage the next
draft. There is no `send()` call anywhere in Step 7 code. This overrides the old
`loop.py` docstring, which described auto-sending in a window — that behavior is
abandoned.

## 3. Phase 1 scope

In scope:

1. **Gmail fetch layer** — the foundational read primitives (full-format message
   body fetch, thread walk with an explicit header allowlist, Sent search, draft
   listing, reply-draft creation).
2. **Sheet read/write** — `read_queue`, `update_row`, and an idempotent header
   migration that adds the new columns.
3. **Manual-send detection** — observe through the Gmail API that a staged draft was
   sent by hand; record message/thread IDs; stamp the sent date; schedule the next
   follow-up. Works for the first email and for follow-ups (replies in-thread).
4. **Pooled follow-up bumps** — when a follow-up is due, stage a short one-line bump
   as a Gmail reply draft, drawn from an editable per-user pool, varied per contact
   by a stable hash (no LLM, deterministic, idempotent).
5. **The `run-loop` tick** — orchestration with per-row error isolation, `--dry-run`,
   and `--init-headers`.
6. **Config + example files** — new env vars and a shipped fictional bump-pool
   template.

Explicitly **deferred to Phase 2+** (named here so the seams exist):

- Reply detection → stop follow-ups.
- Auto-reply / out-of-office classification + return-date parser + defer.
- Bounce / delivery-failure handling.
- LLM-generated reply drafts (`Profile/thread_voice.md`, `thread_drafts.md`) and the
  dedicated reply voice gate.
- `backfill-step7` command, Slack daily digest, launchd auto-scheduling.
- **Gmail push (Pub/Sub `users.watch`) send detection** — see §5.

## 4. The `run-loop` tick (control flow)

Each invocation of `run-loop` is one idempotent tick:

```
load config + the follow-up pool
service = authorized Gmail API client
rows = read_queue()                      # from the Step 7 tab
for (row, row_number) in rows:
    if row.status in {Replied, Closed, Done}: continue
    try:
        # (a) cache the subject while the draft still exists
        cache_subject_if_needed(row)     # drafts.get -> Gmail Subject column
        # (b) detect a manual send via the detector seam
        event = detector.detect(row, service)   # SendEvent | None
        if event: record_send(row, event)       # stamp dates/ids, set Next Action Date
        # (c) stage a due follow-up
        if followups_enabled and followup_due(row):
            stage_followup(row, pool, service)   # drafts.create (reply in thread)
        if changed: update_row(row_number, changed_fields)
    except Exception as exc:
        update_row(row_number, {"Step7 Error": short_reason(exc)})   # isolate, continue
```

Two invariants make repeated runs safe:

- **Never sends.** Steps (a)–(c) only ever read, or call `drafts.create`.
- **Idempotent.** Every decision is derived from values already persisted on the row
  (sent dates, draft IDs, message IDs). Running the tick once or ten times a day
  produces the same end state and never double-stages a follow-up.

`--dry-run` performs all reads, prints every planned write, and performs **no** Sheet
update and **no** draft creation. `--init-headers` runs only the header migration.

## 5. Send-detection seam (so push can drop in later)

The loop must not know *how* a send was detected. It calls a detector that returns a
small immutable event or nothing:

```python
@dataclass(frozen=True)
class SendEvent:
    message_id: str       # the sent message
    thread_id: str
    sent_at: datetime     # from the message internalDate (naive local)
    step: str             # "email_1" | "followup_1" | "followup_2"

class SendDetector(Protocol):
    def detect(self, row: StagedRow, service) -> SendEvent | None: ...
```

**Phase 1 ships `PollingSendDetector`** (option A):

- Trigger: the row has a draft ID set for the current step (`Gmail Draft ID` for the
  first email, `Followup Draft ID` for a follow-up) **and** that draft ID is no longer
  returned by `users.drafts.list`.
- Confirm: `users.messages.list` with a query scoped to Sent —
  `in:sent to:<row.email> subject:<cached subject> after:<draft_seen_date>`. For a
  follow-up, additionally constrain to the known `thread_id`. Choose the **earliest**
  match at/after the draft was first seen.
- Result: build a `SendEvent` from the matched message (`id`, `threadId`,
  `internalDate`). Returns `None` when the draft is still present (not sent yet) or
  gone-with-no-match (handled in §7).

**Later, `PushSendDetector`** (option B, Pub/Sub `users.watch`) implements the same
`detect` contract (or feeds the same `SendEvent` path from a stored change log). The
loop code does not change. Phase 1 builds the `SendDetector` Protocol + `SendEvent`
even though only the polling implementation exists, to guarantee the seam.

## 6. Data model

### 6.1 Status lifecycle (the `Status` column)

```
Drafted ──send email 1──► Email 1 Sent ──D+FOLLOWUP_1_DAYS──► (stage follow-up 1)
   ──send follow-up 1──► Follow-up 1 Sent ──D+FOLLOWUP_2_DAYS──► (stage follow-up 2)
   ──send follow-up 2──► Follow-up 2 Sent ──(no more follow-ups)──► Done
```

`Replied`, `Closed`, and `Bounced` are terminal states set in Phase 2. Phase 1 sets:
`Email 1 Sent`, `Follow-up 1 Sent`, `Follow-up 2 Sent`, `Done`.

### 6.2 Cadence (existing config, defined precisely)

- After **Email 1** is detected sent at `t1`: `Next Action Date = t1 + FOLLOWUP_1_DAYS`.
- After **Follow-up 1** is detected sent at `t2`: `Next Action Date = t2 + FOLLOWUP_2_DAYS`.
- `FOLLOWUP_1_DAYS` / `FOLLOWUP_2_DAYS` are relative gaps in days (existing config keys;
  defaults remain as configured). Document them as relative gaps to remove ambiguity.

### 6.3 New Sheet columns (9), appended after the existing 23

| Column | Written when | Purpose |
|---|---|---|
| `Gmail Message ID` | email 1 send detected | the sent first message |
| `Gmail Subject` | first sighting of the draft | cached subject for Sent matching (survives draft deletion) |
| `Gmail Thread ID` | email 1 send detected | the conversation |
| `Last Gmail Message ID` | each send detected | newest message seen in-thread (Phase 2 reply detection anchor) |
| `Followup Draft ID` | a follow-up is staged | the staged reply draft awaiting manual send |
| `Reply Draft ID` | (Phase 2) | staged reply draft |
| `Step7 Error` | any per-row exception | human-readable error; isolates the row, tick continues |
| `Follow-up Sent?` | a follow-up send detected | `Yes`/`No` at-a-glance indicator |
| `Follow-up Date` | a follow-up send detected | date of the most recent follow-up sent |

`Email 2 Sent` / `Email 3 Sent` (already in the schema) hold the precise per-step
follow-up dates; `Follow-up Sent?` / `Follow-up Date` are the explicit at-a-glance
pair requested on top of them. `StagedRow` ([models.py](../../../src/lib/models.py))
and `SHEET_HEADERS` ([sheets.py](../../../src/lib/sheets.py)) both gain these fields,
in the same order, preserving the existing column contract.

## 7. Edge cases & error handling

- **Draft gone, no Sent match.** The user likely deleted the draft instead of sending.
  Write `Step7 Error = "draft removed; no matching Sent message"`, do not mark sent,
  leave the row for manual review. Do not retry-spam: once noted, skip re-detection
  for that step until the row is edited.
- **Subject caching race.** If the draft is already gone on the very first sighting
  (no cached subject yet), fall back to matching Sent by `to:<email>` +
  `after:<row date_added>` and take the earliest; if still ambiguous, record a
  `Step7 Error` and skip. (Subject caching on first sighting makes this rare.)
- **Per-row isolation.** Any exception in a row's processing is caught, written to
  that row's `Step7 Error`, and the tick proceeds to the next row.
- **Gmail/Sheets transient errors** at the tick level (auth failure, network) abort the
  tick with a non-zero exit and a log line; the next tick retries from Sheet state.
- **Dry-run** never writes; it prints the would-be field changes per row.

## 8. Follow-up bump pool

- Source: `Profile/thread_followups.md` (under the gitignored `Profile/` dir). Ship a
  fictional `Profile.example/thread_followups.md` as the template (kept fictional per
  the repo's privacy guarantees — see `docs/superpowers/specs` sibling work / the
  privacy denylist).
- Format: two labeled pools — Email 2 (bumps) and Email 3 (graceful final notes) — one
  line per entry.
- Selection: `pool[hash(contact_email + step) % len(pool)]` — deterministic per contact
  and step, so re-running a tick picks the same line (idempotent) and two different
  contacts are unlikely to receive a byte-identical bump. No LLM, no voice gate.
- Loaded via a new `load_followup_pools()` in [profile.py](../../../src/lib/profile.py)
  that reads only `thread_followups.md` (and factual files if needed) — never
  `voice.md` / `past_drafts.md` (preserves the cold-email voice/data separation).
- Staged via `create_reply_draft(thread_id, body)` so the bump threads under the
  original email (sets `In-Reply-To` / `References`, reuses the cached subject with a
  `Re:` prefix).

## 9. Modules (new + changed)

| Module | Change |
|---|---|
| `src/lib/gmail.py` | add `get_message_body`, `walk_thread` (header allowlist), `search_sent`, `list_draft_ids`, `get_draft_subject`, `create_reply_draft`. `send_draft` stays an un-called `NotImplementedError` guardrail. |
| `src/lib/sheets.py` | implement `read_queue`, `update_row`; add `ensure_step7_headers`; extend `SHEET_HEADERS` (+9). |
| `src/lib/models.py` | extend `StagedRow` (+9 fields). |
| `src/lib/send_detect.py` | **new** — `SendEvent`, `SendDetector` Protocol, `PollingSendDetector`. |
| `src/lib/followups.py` | **new** — pool loading + stable selection. |
| `src/lib/profile.py` | add `load_followup_pools()`. |
| `src/lib/config.py` | add `STEP7_SHEET_TAB`, `ENABLE_FOLLOWUPS`; (reply-related vars may be added but inert in Phase 1). |
| `src/loop.py` | replace stub with the tick orchestration; add `--init-headers`. |
| `Profile.example/thread_followups.md` | **new** — fictional bump-pool template. |
| `.env.example` | document the new vars. |

## 10. Config / env vars (Phase 1)

| Var | Default | Meaning |
|---|---|---|
| `STEP7_SHEET_TAB` | falls back to `SHEET_TAB_NAME` | which tab the loop reads/writes; point at a **test copy** during rollout, flip to the real tab when proven |
| `ENABLE_FOLLOWUPS` | `true` | stage follow-up bumps |
| `FOLLOWUP_1_DAYS` / `FOLLOWUP_2_DAYS` | existing | relative day gaps (see §6.2) |

## 11. CLI

```bash
uv run run-loop                 # one idempotent tick (detect sends, stage due follow-ups; never sends)
uv run run-loop --dry-run       # print every planned write, change nothing
uv run run-loop --init-headers  # add the 9 Step 7 columns to the tab, then exit
```

## 12. Testing (TDD, mocked Gmail + Sheets)

Write tests first. All Gmail/Sheets calls are mocked (no network). Cases:

- **Send detection:** draft present → no event; draft gone + matching Sent message →
  correct `SendEvent` (ids + date); draft gone + no match → `Step7 Error`, not sent.
- **Subject caching:** first sighting writes `Gmail Subject` from the draft.
- **Follow-up due:** not due → no stage; due + send-day → stages a reply draft, records
  `Followup Draft ID`; already staged → no double-stage (idempotent).
- **Cadence:** `Next Action Date` computed correctly from sent date + gap.
- **Pool selection:** stable per contact+step; different contacts differ.
- **Per-row isolation:** one row raising writes its `Step7 Error` and the next row
  still processes.
- **Header migration:** `ensure_step7_headers` adds 9 columns once, idempotent.
- **Dry-run:** performs no Sheet update and no draft creation.
- **Guardrail:** no code path calls a Gmail `send`.

Plus: `uv run ruff check src tests` clean, and the existing suite stays green.

## 13. Success criteria

A human can: run Phase 1–3 to stage an email → send it by hand → run `run-loop` and see
the row flip to `Email 1 Sent` with message/thread IDs recorded and a `Next Action Date`
set → on/after the due date, run `run-loop` and find a follow-up **reply draft** waiting
in Gmail → send it by hand → the next `run-loop` records `Follow-up 1 Sent`. All without
the loop ever sending anything itself, and re-running the loop at any point is a no-op.

## 14. Review decisions (resolved 2026-05-29)

- **`STEP7_SHEET_TAB` default:** falls back to `SHEET_TAB_NAME` when unset (decided).
  Docs/`.env.example` must carry a strong recommendation to point it at a test-copy tab
  during rollout, then flip to the real tab once proven.
- **Email 2/Email 3 ↔ Follow-up 1/Follow-up 2 mapping:** accepted as-is — the existing
  `Email 2/3 Sent` date columns hold the precise per-step dates, and the new
  `Follow-up Sent?` / `Follow-up Date` pair is the at-a-glance summary on top.
