# GTM Outreach Agent

A local CLI that automates the boring part of a job search: parse a job posting (or LinkedIn post), find 3-4 people to reach out to, draft personalized emails and LinkedIn DMs **in your voice**, stage them as Gmail drafts, and log everything to a Google Sheet for tracking. You review, hit send.

**Local, private, per-user.** Everything — your API key, your Profile/, your contacts — lives on your machine. Nothing is shared with the maintainer or other users.

## What you need

Each is **your own account**, never the maintainer's. You pay the bills.

| What | Why | Cost |
|---|---|---|
| **Anthropic API key** | Drafts emails + parses jobs | Pay-as-you-go (~$0.05 per drafted contact) |
| **Google account** | Gmail drafts + Google Sheets | Free |
| **Claude.ai account** | Phase 2 (finding contacts via Clay MCP) runs here | Pro plan ~$20/mo |
| **Clay account** | Contact + email enrichment | Free tier: 100 credits/mo |
| **Slack incoming webhook** *(optional)* | Step 7 send-loop notifications | Free |
| macOS | The CLI is macOS-first today | — |

## 3-step install

```bash
# 1. Install uv (if you don't have it)
brew install uv

# 2. Clone your copy of the template
git clone https://github.com/<your-username>/gtm-outreach-agent.git
cd gtm-outreach-agent

# 3. Install + run the guided setup
uv sync
uv run apply init
```

`apply init` walks you through each integration (Anthropic key, Google OAuth, Sheet, Clay, Profile/ scaffold) and writes everything to `.env` + `Profile/` as you go. It ends by running `apply doctor` so you can see what's wired.

## How to use it

```bash
# Phase 1 — point at a job posting or a LinkedIn hiring post (or both)
uv run apply run <job_url>
uv run apply run <linkedin_post_url>
uv run apply run <job_url> <linkedin_post_url>

# Phase 2 — runs in Claude.ai chat (NOT here)
# The CLI prints a run ID and a message telling you to ask Claude:
#   "find contacts for run <run_id>"
# Claude uses your Clay MCP connector to find emails and write them back
# into your local state file. See docs/PHASE2.md.

# Phase 3 — draft + stage
uv run apply run --resume <run_id>
uv run apply run --resume <run_id> --dry-run   # print drafts without staging
```

You can also re-run `apply doctor` any time to check what's wired:

```bash
uv run apply doctor
```

## Step 7 (optional): the send/reply loop

Once drafts are staged, `run-loop` tracks the emails **you** send by hand and stages follow-ups for you to review. **It never sends and never auto-replies — you send every email yourself.**

```bash
uv run run-loop --init-headers   # one-time: add the Step 7 columns to your Sheet tab
uv run run-loop --dry-run        # show what it would do, write nothing
uv run run-loop                  # one tick: detect manual sends, stage due follow-ups
```

Point `STEP7_SHEET_TAB` at a test-copy of your tab while you trial it. Reply tracking, out-of-office/bounce handling, and LLM reply drafts arrive in a later release.

## How it stays in your voice

The drafter reads `Profile/` on every email it generates:

```
Profile/
  resume.md          # your resume in markdown
  voice.md           # your voice rules (length, sign-off, banned phrases)
  voice_config.yaml  # machine-readable copy of the rules above
  proof_points.md    # 5-8 quantified accomplishments, drafter picks 1 per email
  past_drafts.md     # 3-5 example emails in your style
  narrative.md       # 3-4 origin chunks, used at most 1 per email
```

`apply init` generates a draft of all six files from your resume + 5 short questions using Claude Sonnet. You review and edit. The drafter then enforces your rules from `voice_config.yaml` after every generation — if a draft uses an em dash or a banned phrase, the drafter regenerates (up to 3 times) before giving up.

[`Profile.example/`](Profile.example/) ships a polished fictional persona ("Alex Chen, PM-turned-founder") so you can see what good looks like before writing your own.

## Updating to a new version

Pulling new code never touches your gitignored `Profile/`, `.env`, or `state/`. To upgrade:

```bash
git pull && uv sync
```

Some releases need a one-time migration — [CHANGELOG.md](CHANGELOG.md) lists the steps per version (e.g. 0.2.0 adds the Step 7 loop, which needs `uv run run-loop --init-headers` plus a customized `Profile/thread_followups.md`). **Click Watch → Releases on the GitHub repo to get notified when a new version ships.**

## What v1 does NOT do

- Step 7 Phase 1 (manual-send detection + pooled follow-ups) is built; reply tracking,
  out-of-office/bounce handling, LLM reply drafts, and Step 8 (launchd) are still deferred.
- LinkedIn auto-send (you copy/paste DMs from the Sheet).
- Multi-account email rotation.
- Auto-apply to jobs.

## License

MIT. See [LICENSE](LICENSE).
