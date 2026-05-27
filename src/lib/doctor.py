"""`apply doctor` — diagnostic checks for each integration.

Each check returns (label, ok, message). Ok messages summarize what's wired;
not-ok messages point at the fix command. Designed to run quickly with no
network calls beyond what's strictly needed to verify auth.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

import click


@dataclass
class CheckResult:
    label: str
    ok: bool
    detail: str


def _check_python() -> CheckResult:
    if sys.version_info < (3, 11):
        return CheckResult(
            "Python >= 3.11",
            False,
            f"running {sys.version.split()[0]}; install Python 3.11+ via `brew install python@3.11`",
        )
    return CheckResult("Python >= 3.11", True, f"{sys.version.split()[0]}")


def _check_env_file() -> CheckResult:
    from src.lib.config import REPO_ROOT

    env = REPO_ROOT / ".env"
    if not env.exists():
        return CheckResult(
            ".env file",
            False,
            f"{env} missing; copy .env.example to .env and fill it in (or run `uv run apply init`)",
        )
    return CheckResult(".env file", True, str(env))


def _check_anthropic_key() -> CheckResult:
    from src.lib.config import load_config

    cfg = load_config()
    if not cfg.anthropic_api_key:
        return CheckResult(
            "ANTHROPIC_API_KEY",
            False,
            "missing; get a key at https://console.anthropic.com/ and add to .env",
        )
    try:
        from anthropic import Anthropic
    except ImportError:
        return CheckResult(
            "ANTHROPIC_API_KEY",
            False,
            "anthropic SDK not installed; run `uv sync`",
        )
    try:
        client = Anthropic(api_key=cfg.anthropic_api_key)
        msg = client.messages.create(
            model=cfg.parse_model,
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        _ = msg.content  # touch to ensure the response is real
        return CheckResult("ANTHROPIC_API_KEY", True, f"works (model={cfg.parse_model})")
    except Exception as exc:
        return CheckResult(
            "ANTHROPIC_API_KEY",
            False,
            f"API call failed: {exc}. Check key + billing at https://console.anthropic.com/",
        )


def _check_google_oauth() -> CheckResult:
    from src.lib.config import load_config

    cfg = load_config()
    if not cfg.google_credentials_path.exists():
        return CheckResult(
            "Google credentials.json",
            False,
            f"missing at {cfg.google_credentials_path}. See docs/SETUP.md for the OAuth Desktop-app setup.",
        )
    if not cfg.google_token_path.exists():
        return CheckResult(
            "Google OAuth token",
            False,
            f"missing at {cfg.google_token_path}. Run `uv run apply init` or any `apply <url>` flow to trigger OAuth.",
        )
    return CheckResult("Google OAuth", True, str(cfg.google_token_path))


def _check_sheet() -> CheckResult:
    from src.lib.config import load_config

    cfg = load_config()
    if not cfg.sheet_id:
        return CheckResult(
            "SHEET_ID",
            True,  # optional
            "(unset — Sheet integration disabled; Gmail drafts only)",
        )
    try:
        from src.lib import sheets

        sheets.ensure_headers()
        return CheckResult(
            "SHEET_ID + headers",
            True,
            f"sheet_id={cfg.sheet_id[:12]}... tab={cfg.sheet_tab_name!r}",
        )
    except Exception as exc:
        return CheckResult(
            "SHEET_ID",
            False,
            f"sheet check failed: {exc}. Confirm SHEET_ID is correct + share the sheet with your Google account.",
        )


def _check_slack() -> CheckResult:
    from src.lib.config import load_config

    cfg = load_config()
    if not cfg.slack_webhook_url:
        return CheckResult("SLACK_WEBHOOK_URL", True, "(unset — Slack notifications disabled)")
    return CheckResult("SLACK_WEBHOOK_URL", True, "set")


def _check_profile() -> CheckResult:
    from src.lib.config import load_config

    cfg = load_config()
    pdir = cfg.profile_dir
    required = ("resume.md", "voice.md", "proof_points.md", "past_drafts.md", "narrative.md")
    missing = [f for f in required if not (pdir / f).exists()]
    if missing:
        return CheckResult(
            "Profile/ pack",
            False,
            f"missing files in {pdir}: {', '.join(missing)}. Run `uv run apply init` to scaffold.",
        )
    return CheckResult("Profile/ pack", True, f"{pdir} has all {len(required)} files")


def _check_voice_config() -> CheckResult:
    from src.lib.config import load_config
    from src.lib.voice_rules import load_voice_config

    cfg = load_config()
    try:
        vc = load_voice_config(cfg.profile_dir)
        return CheckResult(
            "voice_config.yaml",
            True,
            f"signature={vc.signature!r}, +{len(vc.banned_phrases)} personal phrases",
        )
    except (FileNotFoundError, ValueError) as exc:
        return CheckResult("voice_config.yaml", False, str(exc))


def _check_clay_reminder() -> CheckResult:
    return CheckResult(
        "Clay (manual step)",
        True,
        "Phase 2 runs in Claude.ai chat. Confirm Clay MCP is connected at https://claude.ai/settings/connectors",
    )


CHECKS = (
    _check_python,
    _check_env_file,
    _check_anthropic_key,
    _check_google_oauth,
    _check_sheet,
    _check_slack,
    _check_profile,
    _check_voice_config,
    _check_clay_reminder,
)


def run_doctor() -> int:
    """Run all checks, print a summary, return 0 if all required checks pass else 1."""
    click.echo("apply doctor — checking integrations\n")
    failures = 0
    for check_fn in CHECKS:
        try:
            result = check_fn()
        except Exception as exc:
            result = CheckResult(check_fn.__name__, False, f"unhandled error: {exc}")
        prefix = "✓" if result.ok else "✗"
        click.echo(f"  {prefix} {result.label}: {result.detail}")
        if not result.ok:
            failures += 1
    click.echo("")
    if failures:
        click.echo(f"{failures} check(s) failed. Fix the ones above and re-run `uv run apply doctor`.")
        return 1
    click.echo("All checks passed. You're ready to run `uv run apply <job_url>`.")
    return 0
