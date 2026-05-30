# CLAUDE.md

Guidance for Claude Code when working with this repo (as a friend's working copy of the template).

## What this is

Local CLI agent that automates job-search outreach: parse a job/LinkedIn-post URL, find 3-4 contacts via Clay, draft emails + LinkedIn surfaces in the user's voice, stage as Gmail drafts, log to a Google Sheet. Step 7 (send/reply loop) and Step 8 (launchd) are not yet built — see status checklist in [README.md](README.md).

## Commands

```bash
uv sync                                       # install deps into .venv
uv run pytest                                 # run full suite (~125 tests)
uv run pytest tests/test_draft_outreach.py    # single test file
uv run pytest tests/test_voice_rules.py -k em_dash   # single test by name
uv run ruff check src tests                   # lint
uv run apply init                             # first-time setup
uv run apply doctor                           # diagnostic checks
uv run apply run <url> [<url>]                # Phase 1 (parse + state)
uv run apply run --resume <run_id>            # Phase 3 (draft + stage)
uv run apply run --resume <run_id> --dry-run  # Phase 3 without Gmail/Sheets writes
uv run run-loop                 # Step 7: one tick (detect manual sends, stage follow-ups; never sends)
uv run run-loop --dry-run       # print planned writes, change nothing
uv run run-loop --init-headers  # add the 9 Step 7 columns to the tab
```

CLI entrypoint is wired in [pyproject.toml](pyproject.toml): `apply` → `src.apply:main`.

## Three-phase architecture

This is the central design constraint. It exists because **Clay has no public REST API and their MCP server is OAuth-only via Claude.ai** (confirmed; do not try to reach Clay from Python). Work splits across two processes connected by a JSON state file:

| Phase | Where it runs | What it does | State transition |
|---|---|---|---|
| **1** | Python CLI (`apply run <url>`) | parse JD ([parse_job.py](src/lib/parse_job.py)) and/or fetch LI post ([parse_post.py](src/lib/parse_post.py)); infer hiring-manager titles ([find_contacts.py](src/lib/find_contacts.py)) | writes `state/runs/<run_id>.json` with `status="awaiting_contacts"` |
| **2** | **Claude Code chat** (this assistant), session Clay MCP | reads the state JSON, calls `mcp__claude_ai_Clay__find-and-enrich-*` tools, writes contacts back, flips status | `awaiting_contacts` → `ready_to_draft` |
| **3** | Python CLI (`apply run --resume <run_id>`) | drafts per contact ([draft_outreach.py](src/lib/draft_outreach.py)), stages Gmail drafts + Sheet rows | `ready_to_draft` → `staged` (or `failed`) |

Run-state schema lives in [src/lib/run_state.py](src/lib/run_state.py); contact/draft/row schemas in [src/lib/models.py](src/lib/models.py). Phase-2 operator manual is [docs/PHASE2.md](docs/PHASE2.md) — read it before doing any Phase-2 work in chat.

### Phase-2 rules (when invoked in chat)

- Use **only the email enrichment** from Clay (1 credit/contact). Do NOT request thought-leadership / recent activity / company news enrichments — they burn ~10 credits each and the artifact-driven drafter doesn't consume them anyway.
- Sub-50-person companies: founders are usually the hiring manager. Widen titles to include `Founder`, `CEO`, `Co-Founder`.
- Do NOT add `manual_notes`, `personalization_hook`, or `recent_li_activity` to contacts — those fields were intentionally killed when the drafter became artifact-driven.
- Preserve `run_id`, `parsed_job`, `parsed_post`, `inferred_titles`, `created_at`. Only mutate `contacts` and `status`.

## Drafter contract

[draft_outreach.py](src/lib/draft_outreach.py) is **one Sonnet call per contact** with up to 3 voice-rule regen attempts, then `DraftError`. Two modes:

- **POST mode** (contact `source="post_author"` and a `ParsedPost` exists): opener must establish "Saw your post on LinkedIn..." and quote/paraphrase the snippet. If `parsed_post.post_snippet is None` (LinkedIn locked the page), do NOT fabricate — degrade to a credentialed pitch.
- **JD mode** (everything else): opener references "Just applied for the [role] at [company]".

Output is a single JSON object with `email` + `linkedin` blocks. The static prompt block (profile pack + voice rules + template) is marked `cache_control: ephemeral` and amortizes across all contacts in a run — keep it stable to preserve cache hits.

Voice rules ([voice_rules.py](src/lib/voice_rules.py)) are **per-user**, loaded from [Profile/voice_config.yaml](Profile.example/voice_config.yaml). Universal AI tells (em-dashes, "leverage", "passionate about", etc.) are baked into `UNIVERSAL_BANNED_PHRASES`; personal additions come from voice_config.yaml. Friends customize their signature, banned phrases, and word-count bounds in that file.

## Profile pack

The agent reads every file in `Profile/` on every drafting call ([profile.py](src/lib/profile.py), `lru_cache`'d). Files: `resume.md`, `voice.md`, `proof_points.md`, `past_drafts.md`, `narrative.md`. **`Profile/` is gitignored** — every friend keeps their own. The committed [Profile.example/](Profile.example/) ships a fictional "Alex Chen" persona as a reference.

## Google Sheet is the source of truth

`StagedRow` in [models.py](src/lib/models.py) and `SHEET_HEADERS` in [sheets.py](src/lib/sheets.py) define the column contract. Sheet integration is optional: if `SHEET_ID` is empty, the agent skips Sheets and still stages Gmail drafts (warns on stderr).

## Run-state files (`state/runs/*.json`)

**Gitignored** because they contain real contact names + emails from your job applications. Tests can override the runs dir via the `RUNS_DIR_OVERRIDE` env var (see [run_state.py](src/lib/run_state.py)).

## Models and tokens

- Drafting: `CLAUDE_DRAFT_MODEL` (default `claude-sonnet-4-6`). Temperature 0.7. Opus models drop the `temperature` kwarg automatically — see `call_claude_cached` in [draft_outreach.py](src/lib/draft_outreach.py).
- Parsing/title inference: `CLAUDE_PARSE_MODEL` (default `claude-haiku-4-5-20251001`).
- Apollo is wired in `Config` but unused. Leave `USE_APOLLO=false`.

## Secrets / per-machine state (gitignored)

`.env`, `credentials/credentials.json`, `credentials/token.json`, `Profile/`, `state/runs/`, `.claude-memory/`, `logs/`. First Phase-3 run opens a browser for Google OAuth and writes `credentials/token.json`.
