"""Centralized config loaded from .env. Imported by every module that needs settings."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key, str(default)).lower()
    return raw in {"1", "true", "yes", "on"}


def _env_path(key: str, default: str) -> Path:
    raw = _env(key, default)
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    draft_model: str
    parse_model: str

    use_apollo: bool
    apollo_api_key: str

    google_credentials_path: Path
    google_token_path: Path
    sheet_id: str
    sheet_tab_name: str
    step7_sheet_tab: str
    enable_followups: bool

    slack_webhook_url: str

    sender_name: str
    sender_email: str
    default_timezone: str

    send_days: tuple[str, ...]
    send_window_start: str
    send_window_end: str
    send_jitter_min: int
    send_jitter_max: int

    followup_1_days: int
    followup_2_days: int

    profile_dir: Path
    resume_path: Path
    log_level: str
    log_file: Path


def _parse_window(raw: str) -> tuple[str, str]:
    if "-" not in raw:
        raise ValueError(f"SEND_WINDOW must look like 'HH:MM-HH:MM', got {raw!r}")
    a, b = raw.split("-", 1)
    return a.strip(), b.strip()


def _parse_jitter(raw: str) -> tuple[int, int]:
    if "-" not in raw:
        n = int(raw)
        return n, n
    a, b = raw.split("-", 1)
    return int(a), int(b)


def load_config() -> Config:
    start, end = _parse_window(_env("SEND_WINDOW", "07:00-09:00"))
    jmin, jmax = _parse_jitter(_env("SEND_JITTER_MINUTES", "5-15"))
    return Config(
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        draft_model=_env("CLAUDE_DRAFT_MODEL", "claude-sonnet-4-6"),
        parse_model=_env("CLAUDE_PARSE_MODEL", "claude-haiku-4-5-20251001"),
        use_apollo=_env_bool("USE_APOLLO", False),
        apollo_api_key=_env("APOLLO_API_KEY"),
        google_credentials_path=_env_path("GOOGLE_CREDENTIALS_PATH", "credentials/credentials.json"),
        google_token_path=_env_path("GOOGLE_TOKEN_PATH", "credentials/token.json"),
        sheet_id=_env("SHEET_ID"),
        sheet_tab_name=_env("SHEET_TAB_NAME", "Outreach"),
        step7_sheet_tab=_env("STEP7_SHEET_TAB", "") or _env("SHEET_TAB_NAME", "Outreach"),
        enable_followups=_env_bool("ENABLE_FOLLOWUPS", True),
        slack_webhook_url=_env("SLACK_WEBHOOK_URL"),
        sender_name=_env("SENDER_NAME", ""),
        sender_email=_env("SENDER_EMAIL"),
        default_timezone=_env("DEFAULT_TIMEZONE", "America/New_York"),
        send_days=tuple(d.strip() for d in _env("SEND_DAYS", "Tue,Wed,Thu").split(",") if d.strip()),
        send_window_start=start,
        send_window_end=end,
        send_jitter_min=jmin,
        send_jitter_max=jmax,
        followup_1_days=int(_env("FOLLOWUP_1_DAYS", "4")),
        followup_2_days=int(_env("FOLLOWUP_2_DAYS", "9")),
        profile_dir=_env_path("PROFILE_DIR", "Profile"),
        resume_path=_env_path("RESUME_PATH", "Profile/resume.pdf"),
        log_level=_env("LOG_LEVEL", "INFO"),
        log_file=_env_path("LOG_FILE", "logs/agent.log"),
    )
