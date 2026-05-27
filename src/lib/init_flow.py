"""`apply init` — guided first-time setup.

Walks the user through every per-machine integration: Anthropic key, Google
OAuth (Gmail + Sheets), Sheet ID, optional Slack webhook, the Clay/Claude.ai
reminder, and the AI-scaffolded Profile/ pack. Each step writes to .env or
Profile/ as we go so the friend never has to manually edit config files.
"""
from __future__ import annotations

import os
import sys
import webbrowser

import click

from src.lib.config import REPO_ROOT


# --------------------------------------------------------------------------- #
# .env management                                                             #
# --------------------------------------------------------------------------- #

ENV_PATH = REPO_ROOT / ".env"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"


def _ensure_env_exists() -> None:
    if ENV_PATH.exists():
        return
    if ENV_EXAMPLE_PATH.exists():
        ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text())
    else:
        ENV_PATH.write_text("# GTM Outreach Agent — local config\n")


def _set_env_var(key: str, value: str) -> None:
    """Set or update KEY=VALUE in .env, preserving other lines + comments."""
    _ensure_env_exists()
    lines = ENV_PATH.read_text().splitlines()
    out: list[str] = []
    found = False
    quoted_value = value if " " not in value and "#" not in value else f'"{value}"'
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
            out.append(f"{key}={quoted_value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={quoted_value}")
    ENV_PATH.write_text("\n".join(out) + ("\n" if not out or out[-1] else ""))
    os.environ[key] = value  # so subsequent load_config() picks it up in-process


# --------------------------------------------------------------------------- #
# Steps                                                                       #
# --------------------------------------------------------------------------- #


def _step_intro() -> None:
    click.echo("=" * 72)
    click.echo("GTM Outreach Agent — first-time setup")
    click.echo("=" * 72)
    click.echo(
        "\nYou'll need (each is your own account, never the maintainer's):\n"
        "  - Anthropic API key  (you pay for Sonnet/Haiku calls)\n"
        "  - Google account     (Gmail drafts + Google Sheets)\n"
        "  - Claude.ai account  (Clay MCP runs through it — paid plan, ~$20/mo)\n"
        "  - Clay account       (free tier — sign up at clay.com)\n"
        "  - (optional) Slack incoming webhook for notifications\n"
    )
    if not click.confirm("Ready to set up?", default=True):
        click.echo("Setup cancelled. Run `uv run apply init` again when ready.")
        sys.exit(0)


def _step_anthropic_key() -> None:
    click.echo("\n--- Step 1/6: Anthropic API key ---")
    click.echo("Get a key at https://console.anthropic.com/ -> Settings -> API Keys.")
    existing = os.environ.get("ANTHROPIC_API_KEY", "")
    if existing:
        if click.confirm(f"Key already set (sk-...{existing[-4:]}). Replace it?", default=False):
            pass
        else:
            click.echo("  ✓ keeping existing key")
            return
    key = click.prompt("Paste your Anthropic API key", type=str, hide_input=True).strip()
    if not key.startswith("sk-"):
        raise click.ClickException("That doesn't look like an Anthropic key (expected to start with 'sk-').")
    _set_env_var("ANTHROPIC_API_KEY", key)
    click.echo("  ✓ saved to .env")
    click.echo("  Testing the key with a small Haiku call...")
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=key)
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        click.echo("  ✓ key works")
    except Exception as exc:
        raise click.ClickException(
            f"Anthropic API call failed: {exc}. "
            "Check the key + billing at https://console.anthropic.com/"
        )


def _step_google_oauth() -> None:
    click.echo("\n--- Step 2/6: Google OAuth (Gmail + Sheets) ---")
    click.echo(
        "Before continuing, follow docs/SETUP.md to:\n"
        "  1) create a Google Cloud project,\n"
        "  2) enable the Gmail API + Sheets API,\n"
        "  3) create a Desktop OAuth client + download credentials.json,\n"
        "  4) save it to credentials/credentials.json (this folder)."
    )
    from src.lib.config import load_config

    cfg = load_config()
    if not cfg.google_credentials_path.exists():
        click.echo(f"  credentials.json not found at {cfg.google_credentials_path}.")
        if click.confirm("Open the SETUP.md instructions in your browser now?", default=False):
            setup_path = REPO_ROOT / "docs" / "SETUP.md"
            if setup_path.exists():
                webbrowser.open(setup_path.as_uri())
        raise click.ClickException(
            "Set up credentials.json then re-run `uv run apply init`."
        )
    click.echo("  ✓ credentials.json present")
    click.echo("  Launching OAuth (browser will open)...")
    try:
        from src.lib import google_auth
        google_auth.load_credentials()  # triggers the full OAuth flow if no token yet
        click.echo("  ✓ Google OAuth token saved")
    except Exception as exc:
        raise click.ClickException(f"Google OAuth failed: {exc}")


def _step_sheet() -> None:
    click.echo("\n--- Step 3/6: Google Sheet (optional but recommended) ---")
    click.echo(
        "The agent logs every drafted contact to a Google Sheet for tracking. "
        "You can paste an existing sheet ID or skip and stage Gmail drafts only."
    )
    if not click.confirm("Set up the Google Sheet now?", default=True):
        click.echo("  ✓ skipped (Gmail drafts only)")
        return
    click.echo(
        "Create a blank Sheet at https://sheets.google.com/, then copy the ID from the URL:\n"
        "  https://docs.google.com/spreadsheets/d/<ID>/edit"
    )
    sheet_id = click.prompt("Paste the Sheet ID", type=str, default="", show_default=False).strip()
    if not sheet_id:
        click.echo("  ✓ skipped")
        return
    _set_env_var("SHEET_ID", sheet_id)
    click.echo("  Testing the Sheet by writing the header row...")
    try:
        from src.lib import sheets
        sheets.ensure_headers()
        click.echo("  ✓ headers written")
    except Exception as exc:
        raise click.ClickException(
            f"Sheet test failed: {exc}. "
            "Confirm the Sheet ID is correct + the sheet is shared with the Google account you OAuth'd with."
        )


def _step_slack() -> None:
    click.echo("\n--- Step 4/6: Slack webhook (optional) ---")
    click.echo(
        "Step 7's send-loop posts notifications to a Slack channel via incoming webhook. "
        "Skip if you don't use Slack (or if Step 7 isn't implemented yet — it's a v2 feature)."
    )
    if not click.confirm("Configure Slack webhook?", default=False):
        click.echo("  ✓ skipped")
        return
    url = click.prompt("Paste your Slack incoming webhook URL", type=str, default="", show_default=False).strip()
    if not url.startswith("https://hooks.slack.com/"):
        click.echo("  ! that doesn't look like a Slack webhook URL; skipping")
        return
    _set_env_var("SLACK_WEBHOOK_URL", url)
    click.echo("  ✓ saved")


def _step_clay_reminder() -> None:
    click.echo("\n--- Step 5/6: Clay (manual step — cannot be automated) ---")
    click.echo(
        "Clay has no public REST API. Phase 2 (finding contacts) runs through "
        "Claude.ai with Clay as an MCP connector.\n\n"
        "  1) Sign up for Clay at https://clay.com (free tier, 100 credits/mo)\n"
        "  2) Open Claude.ai -> Settings -> Connectors -> add Clay\n"
        "  3) When you run `apply <job_url>`, the CLI tells you to ask Claude\n"
        "     in chat: 'find contacts for run <run_id>'. Claude uses Clay MCP\n"
        "     to find emails and write them back to your local state file.\n"
    )
    click.confirm("Press enter when Clay is connected in Claude.ai", default=True)


def _step_profile_scaffold() -> None:
    click.echo("\n--- Step 6/6: Profile/ pack (the heart of the personalization) ---")
    click.echo(
        "The drafter reads Profile/ on every email it generates. We'll scaffold "
        "your pack from your resume + 5 short questions using Sonnet.\n"
    )
    from src.lib.config import load_config

    cfg = load_config()
    existing = cfg.profile_dir.exists() and any(cfg.profile_dir.iterdir())
    if existing:
        if not click.confirm(
            f"Profile/ already has files at {cfg.profile_dir}. Overwrite?", default=False
        ):
            click.echo("  ✓ keeping existing Profile/")
            return
    from src.lib.profile_scaffold import run_scaffold

    try:
        run_scaffold(cfg.profile_dir)
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(f"Profile scaffold failed: {exc}")
    click.echo(
        f"\n  ✓ Profile/ written to {cfg.profile_dir}. "
        "Open and edit any of the files before drafting your first email — "
        "the AI starter is a draft, your voice is the final word."
    )


def _step_final_doctor() -> int:
    click.echo("\n--- Final check: running `apply doctor` ---\n")
    from src.lib.doctor import run_doctor

    return run_doctor()


# --------------------------------------------------------------------------- #
# Entry point                                                                 #
# --------------------------------------------------------------------------- #


def run_init() -> int:
    _step_intro()
    _step_anthropic_key()
    _step_google_oauth()
    _step_sheet()
    _step_slack()
    _step_clay_reminder()
    _step_profile_scaffold()
    return _step_final_doctor()
