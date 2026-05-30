# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Watching for updates:** click **Watch → Custom → Releases** on the GitHub repo to
> get notified when a new version ships.

## [0.2.0] - 2026-05-29

Step 7 Phase 1 — manual-send detection + pooled follow-ups.

### Added
- **`run-loop` CLI** — one idempotent tick that detects the emails you sent **by hand**
  (via the Gmail API), records them in your Sheet, and stages follow-up reply drafts.
  It **never sends and never auto-replies** — the only Gmail write is `drafts().create`.
  Flags: `--dry-run` (print planned writes, change nothing) and `--init-headers`
  (add the new columns to your tab).
- **Gmail fetch primitives** — draft listing, draft-subject caching, Sent search,
  message metadata, and threaded reply-draft creation.
- **Sheet read/write** — `read_queue` / `update_row` plus 9 new tracking columns:
  Gmail Message ID, Gmail Subject, Gmail Thread ID, Last Gmail Message ID,
  Followup Draft ID, Reply Draft ID, Step7 Error, Follow-up Sent?, Follow-up Date.
- **`SendEvent` detector seam** with a polling implementation (`PollingSendDetector`);
  a future Pub/Sub push detector can drop in without changing the loop.
- **Deterministic follow-up bump pool** (`Profile/thread_followups.md`), selected per
  contact via a stable hash so re-runs are idempotent. A fictional template ships in
  `Profile.example/thread_followups.md`.
- **Config** — `STEP7_SHEET_TAB` (defaults to `SHEET_TAB_NAME`; point it at a test-copy
  tab during rollout) and `ENABLE_FOLLOWUPS` (default `true`).

### Changed
- `scripts/pre_publish_scan.py` allowlists `anthropic.com` for the AI co-author commit
  trailer (false-positive fix; not personal data).

### Upgrading from 0.1.x
Your gitignored `Profile/`, `.env`, and `state/` are untouched by a pull. To adopt Step 7:
1. `git pull && uv sync`
2. `uv run run-loop --init-headers` — adds the 9 new columns to your Sheet tab (idempotent)
3. `cp Profile.example/thread_followups.md Profile/thread_followups.md`, then edit the bumps
4. *(optional)* set `STEP7_SHEET_TAB` to a **test-copy** of your tab while you trial the loop

Then send an email by hand and run `uv run run-loop`. The loop never sends for you.

### Deferred to later phases
Reply tracking, out-of-office + bounce handling, LLM-generated reply drafts,
`backfill-step7`, a Slack digest, and Step 8 (launchd auto-scheduling).

## [0.1.0] - 2026-05-27

### Added
- Initial public template: the three-phase outreach pipeline (parse a job/LinkedIn-post
  URL → find contacts in Claude.ai chat via the Clay MCP → draft + stage Gmail drafts +
  log to a Google Sheet), the per-user voice profile pack, voice-rule enforcement, and a
  pre-publish privacy scanner.

[0.2.0]: https://github.com/rsg9999/gtm-outreach-agent-template/releases/tag/v0.2.0
[0.1.0]: https://github.com/rsg9999/gtm-outreach-agent-template/commit/37464df
