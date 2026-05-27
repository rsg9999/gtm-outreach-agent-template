# Setup guide

Long-form walkthrough of each integration. The fastest path is to just run `uv run apply init` and follow the prompts — this doc is the reference if you get stuck on any step.

## Prerequisites

```bash
brew install uv python@3.11
```

(macOS-only today. If you're on Linux/Windows, the Python side will work but the OAuth browser flow may need tweaking — open an issue.)

## Step 1 — Anthropic API key

1. Go to [console.anthropic.com](https://console.anthropic.com/).
2. Settings → API Keys → Create Key. Copy it.
3. Add it to `.env` as `ANTHROPIC_API_KEY=sk-ant-...`. (`apply init` does this for you.)

You pay for the calls — they're cheap (~$0.05 per drafted contact for Sonnet, fractions of a cent for Haiku parsing).

## Step 2 — Google OAuth (Gmail + Sheets)

This is the most fiddly step. Do it once.

1. Open [console.cloud.google.com](https://console.cloud.google.com/), create a new project ("gtm-outreach-agent" or whatever).
2. **APIs & Services → Library**: enable both **Gmail API** and **Google Sheets API**.
3. **APIs & Services → OAuth consent screen**:
   - User type: **External**
   - App name: anything (this only shows in the consent screen to you)
   - Add yourself as a **Test user** (Add Users → your Gmail address)
   - Scopes: you don't need to add any here; the CLI requests them at runtime.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**
   - Download the JSON. Rename it to `credentials.json` and save it to `credentials/credentials.json` in this repo.
5. Run `apply init` (or any `apply run <url>`). A browser will pop up asking you to authorize the app. Approve. The token gets cached in `credentials/token.json`. You won't have to do this again unless the token is revoked.

Both `credentials/credentials.json` and `credentials/token.json` are gitignored.

## Step 3 — Google Sheet

The agent logs every drafted contact to a Sheet for tracking (and Step 7's send-loop will read it).

1. Open [sheets.google.com](https://sheets.google.com/), create a blank Sheet.
2. Copy the ID from the URL: `https://docs.google.com/spreadsheets/d/<ID>/edit`.
3. Add it to `.env` as `SHEET_ID=<paste here>`. (`apply init` does this and writes the header row for you.)

The Sheet must be accessible to the Google account you OAuth'd with in Step 2. (If you used the same account, that's automatic. If different, share the sheet.)

If you skip this step (leave `SHEET_ID` empty), the agent still works — it stages Gmail drafts but doesn't log anywhere.

## Step 4 — Clay + Claude.ai (the manual one)

Clay has no public REST API. Contact-finding (Phase 2) runs through Claude.ai chat with Clay as an MCP connector.

1. Sign up at [clay.com](https://clay.com). The free tier gives 100 credits/mo, which is ~25 contacts.
2. Make sure you have a Claude.ai account ([claude.ai](https://claude.ai), Pro plan ~$20/mo).
3. In Claude.ai: Settings → Connectors → add Clay. Authorize.
4. When the CLI prints `Ask Claude in chat: 'find contacts for run <run_id>'`, do exactly that in any Claude conversation. Claude calls Clay MCP tools to find emails and writes them back to your local state file. See [PHASE2.md](PHASE2.md) for the operator manual.

This is the one step the CLI can't automate — it's a real human-in-Claude.ai step.

## Step 5 — Slack webhook (optional)

For Step 7's notifications. Skip if you don't care.

1. [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From scratch.
2. Pick a name + workspace.
3. Features → Incoming Webhooks → toggle on → Add New Webhook to Workspace → pick a channel.
4. Copy the webhook URL into `.env` as `SLACK_WEBHOOK_URL=...`.

## Step 6 — Profile/

The drafter reads `Profile/` on every email. `apply init`'s final step scaffolds it for you:

1. Paste your resume (markdown or plain text — Claude figures it out).
2. Answer 5 short questions: one-line pitch, target role/industry, 2-3 recent wins with numbers, voice description, 1-2 stories worth telling.
3. Claude Sonnet generates draft `resume.md`, `voice.md`, `voice_config.yaml`, `proof_points.md`, `past_drafts.md`, and `narrative.md`.
4. **Review and edit** before drafting your first outreach. The AI is the starter, your voice is the final word.

If you'd rather write `Profile/` from scratch, copy `Profile.example/` to `Profile/` and replace the fictional persona's content with yours.

## Common errors

**`ANTHROPIC_API_KEY: API call failed: 401`** — Your key is wrong or your billing isn't set up. Go back to console.anthropic.com.

**`Google credentials.json missing`** — Step 2 wasn't completed. The `credentials.json` file from Google Cloud Console must be saved to `credentials/credentials.json`.

**`Sheet check failed: 403`** — The Google account you OAuth'd with doesn't have access to the Sheet. Either share the sheet with that account or re-OAuth with the right account (`rm credentials/token.json` then re-run `apply init`).

**`voice_config.yaml not found`** — `Profile/` wasn't scaffolded. Run `apply init` or copy `Profile.example/voice_config.yaml` to `Profile/voice_config.yaml` and fill in your name.

**Browser doesn't open during OAuth** — Sometimes happens on remote machines. Check the terminal for a URL it printed; open it manually.

When in doubt, run `uv run apply doctor` — it'll tell you exactly what's wired and what's missing.
