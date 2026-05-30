# Reply-Intelligence (Step 7 Phase 2) — Design Spec

**Date:** 2026-05-30
**Status:** Approved design, pre-implementation
**Branch:** `feature/reply-intelligence`

## 1. Overview

Phase 1 (`run-loop`) detects emails the user sends by hand and stages pooled follow-up
drafts. Phase 2 adds **reply intelligence**: each tick, for a row whose Email 1 was sent,
read the latest *inbound* message in the thread and classify it, then act:

- **Genuine reply** → mark the row `Replied?`, stop follow-ups, and (optionally) stage an
  LLM-drafted reply in the user's *thread voice* for review.
- **Bounce** → flag the row; do **not** stop the sequence; do **not** stage a reply.
- **Auto-reply / out-of-office** → do **not** count as a reply; defer the next follow-up
  to the sender's stated return date (or a default).
- **Nothing** → unchanged; existing follow-up logic runs.

The guardrail is unchanged: **the loop never sends and never auto-replies.** The only Gmail
write remains `drafts().create`. Reply drafts are staged for the user to send by hand.

## 2. Goals / Non-Goals

**Goals**
- Stop emailing people who already replied.
- Never mistake a bounce or an out-of-office for a genuine reply.
- Defer follow-ups intelligently around a stated OOO return date.
- Stage a review-ready reply draft in the user's thread voice when a real reply lands.
- Keep every external call mocked in tests; ship with TDD coverage.

**Non-Goals (YAGNI — explicitly out of scope)**
- Sending or auto-replying (still manual-only, by design).
- Sentiment analysis / intent classification of reply content beyond genuine-vs-noise.
- A Pub/Sub push path (the polling detector stays; a push detector is a later seam).
- Step 8 (launchd scheduling).
- LinkedIn auto-send.

## 3. Current state (what Phase 1 already gives us)

- `StagedRow` already has `replied`, `reply_date`, `reply_draft_id` ("populated in Phase 2"),
  `gmail_thread_id`, `last_gmail_message_id`. **No new Sheet columns are required.**
- `run_tick` already **skips** rows where `row.replied` is true, so setting `Replied?`
  stops follow-ups for free.
- `gmail.py` has a naive `has_reply(thread_id, our_address)` that cannot distinguish a
  genuine reply from an auto-reply or a bounce — Phase 2 replaces reliance on it.
- Per-row error isolation (errors → `Step7 Error`, tick continues) already exists and is
  the pattern Phase 2 extends.

## 4. Architecture — modules

| Module | Change |
|---|---|
| `src/lib/gmail.py` | **add** `get_message_body(message_id) -> (body_text, internal_date_ms)` (full-format fetch, decode base64url `text/plain`); **fix** the thread walk to request an explicit `metadataHeaders` allowlist so detection headers are reliably present. |
| `src/lib/classify.py` | **new** — `classify_inbound(...) -> "genuine" | "auto_reply" | "bounce" | "none"`, with `is_bounce(...)` and `is_auto_reply(...)` helpers. Pure functions over headers/subject/sender/body. |
| `src/lib/ooo.py` | **new** — `parse_return_date(text, *, today) -> date | None`. Pure, naive-local. |
| `src/lib/reply_drafts.py` | **new** — `generate_reply(row, inbound_body, cfg) -> str`. One Claude call, thread-voice gate, template fallback. |
| `src/lib/profile.py` | **add** `load_thread_pack()` — loads `thread_voice.md` + `thread_drafts.md` + factual files (resume, proof points framed "facts only"); **excludes** `voice.md` and `past_drafts.md`. |
| `src/lib/voice_rules.py` | **add** `check_reply(text) -> list[violation]` gate (20–100 words + banned-phrase rules). |
| `src/lib/config.py` | **add** `enable_reply_tracking` (true), `enable_reply_drafts` (true), `reply_use_llm` (true), `ooo_defer_days` (5). |
| `src/loop.py` | **add** the reply-detection branch in `run_tick`. |
| `src/lib/models.py` | **add** `ReplyGenerationError` (typed exception for transient API/timeout failures). |
| `Profile.example/` | **add** `thread_voice.md`, `thread_drafts.md` (fictional persona; `thread_followups.md` already exists). |

## 5. Core decision flow (`run_tick`)

After the existing send-detection step, for a row with `email_1_sent` and not `replied`,
and only when `cfg.enable_reply_tracking`:

```
inbound = the most recent thread message (by Gmail internalDate) whose From
          address is NOT the user's cfg.sender_email
if inbound is None: fall through to existing follow-up logic
else classify_inbound(inbound):

  "genuine"    -> Replied? = True; Reply Date = inbound date
                  (follow-ups auto-stop: loop already skips replied rows)
                  if cfg.enable_reply_drafts and not row.reply_draft_id:
                      stage a reply draft (see §7), addressed to the actual reply sender

  "bounce"     -> Step7 Error = "bounce: address may be invalid"
                  do NOT set Replied?; do NOT stop the sequence; do NOT stage a reply

  "auto_reply" -> do NOT set Replied?
                  d = parse_return_date(inbound_body, today=now.date())
                  if d and (d - today) <= 90 days:  Next Action Date = d + 1 day
                  elif d:                            Step7 Error = "OOO >90d: manual review"
                  else:                              Next Action Date = now + ooo_defer_days

  "none"       -> no change; existing follow-up staging runs
```

**Classification precedence:** `bounce` is checked before `auto_reply` before `genuine`
(a bounce that carries auto-ish headers is still a bounce).

**Idempotency:** OOO defer is anchored to fixed timestamps (the inbound message date / the
stated return date), so every tick recomputes the *same* `Next Action Date` — no drift, safe
to re-run. A genuine reply on a later tick (after an OOO) still wins because OOO never sets
`Replied?`.

**Error isolation:** any exception in the per-row block is caught and written to
`Step7 Error`; the tick continues (existing pattern, unchanged).

## 6. Classification heuristics (`classify.py`)

Order: `is_bounce` → `is_auto_reply` → else genuine (if inbound exists) → else none.

**`is_bounce`** — any of:
- sender localpart is `mailer-daemon` or `postmaster`;
- `Content-Type` contains `report-type=delivery-status` (a DSN);
- subject matches (case-insensitive) `delivery status notification`,
  `undelivered mail returned to sender`, or `mail delivery failed`.

**`is_auto_reply`** — any of:
- header `Auto-Submitted` is present and not `no` (i.e. `auto-generated` / `auto-replied`);
- header `Precedence` in `bulk` / `auto_reply`;
- any of `X-Autoreply`, `X-Autorespond`, `X-Auto-Response-Suppress` present;
- subject matches `out of office`, `automatic reply`, `auto-reply`, `on leave`, `on vacation`,
  `away from( the)? office`.

**Genuine** = inbound message exists, is from the contact (not the user), and is neither a
bounce nor an auto-reply.

## 7. Reply drafts (`reply_drafts.py`)

`generate_reply(row, inbound_body, cfg)`:

1. Load `load_thread_pack()` (thread voice + thread drafts + factual files).
2. Read the **full inbound body** (via `get_message_body`, not Gmail's truncated snippet).
3. If `cfg.reply_use_llm`: one Claude call (**60s timeout**) → run `check_reply` gate
   (20–100 words + banned phrases) → up to **3 regen attempts**. On success, return the text.
4. **Fallbacks:**
   - Gate still failing after 3 attempts, **or** a hard/non-transient API error →
     return a **deterministic template** (loop never crashes).
   - **Transient** failure (HTTP 429 / 5xx / timeout) → raise `ReplyGenerationError` so the
     row is left unchanged and **retries next tick** (does not burn its one shot on a template).
5. If `not cfg.reply_use_llm`: always return the deterministic template.

The staged reply draft is addressed to the **actual reply sender** (the inbound `From`), which
may differ from the original contact. It is created with `create_reply_draft` into the existing
thread; its id is written to `Reply Draft ID`.

## 8. OOO return-date parser (`ooo.py`)

`parse_return_date(text, *, today) -> date | None`, naive-local:

- Formats: `Month D` ("June 3"), `D Month` ("3 June"), `M/D`, `M/D/YYYY`, `YYYY-MM-DD`.
- Cue words that introduce a return date: `back on`, `returning`, `return`, `until`, `through`,
  `as of`.
- **Ignore** a date immediately following a *departure* cue ("out 6/2 through 6/9" → returns 6/9,
  not 6/2).
- When several dates appear, take the **latest**.
- `until further notice` / `indefinitely` → `None`.
- Year inference for bare `Month D` / `M/D`: assume the next occurrence on/after `today`.

The loop maps the result: `None` → default defer (`ooo_defer_days`); `>90 days out` →
`Step7 Error` (permanent/long responder, manual review); otherwise `date + 1 day`.

## 9. Config (`config.py`)

| Var | Default | Meaning |
|---|---|---|
| `ENABLE_REPLY_TRACKING` | `true` | Classify inbound + stop follow-ups on a genuine reply. |
| `ENABLE_REPLY_DRAFTS` | `true` | Stage an LLM reply draft when a genuine reply is found. |
| `REPLY_USE_LLM` | `true` | `false` forces the deterministic template (kill-switch). |
| `OOO_DEFER_DAYS` | `5` | Days to defer when an OOO names no parseable return date. |

## 10. Testing (TDD, all external calls mocked)

Foundation first, then behaviors:

1. `ooo.py` — every format, departure-cue ignore, latest-of-many, "until further notice",
   year inference, idempotent recompute.
2. `classify.py` — genuine / auto_reply / bounce / none matrix incl. precedence
   (bounce-with-auto-headers → bounce).
3. `gmail.get_message_body` — base64url decode, internal-date extraction; thread walk requests
   the header allowlist.
4. `reply_drafts.py` — gate pass, gate-fail→template, hard-error→template,
   transient→`ReplyGenerationError`, `reply_use_llm=false`→template, addressed-to-sender.
5. `loop.py` — reply stops follow-ups; bounce flags without stopping; OOO defers
   idempotently; OOO >90d escalates; all errors isolated; reply-draft staged once
   (not re-staged when `reply_draft_id` already set).

Mocks: Gmail service, Sheets read/update, the Claude client. No network in tests.

## 11. Implementation sequencing (for the plan)

1. Config flags + `ReplyGenerationError` model.
2. `gmail.get_message_body` + thread-walk header allowlist.
3. `ooo.py` (+ tests).
4. `classify.py` (+ tests).
5. `profile.load_thread_pack` + `Profile.example/thread_voice.md`, `thread_drafts.md`.
6. `voice_rules.check_reply` gate.
7. `reply_drafts.generate_reply` (+ tests, mocked Claude).
8. `loop.py` reply branch wiring (+ tests).
9. CHANGELOG + README + version bump (0.2.0 → 0.3.0) for the release.

Each step: write tests first, make them pass, commit.
