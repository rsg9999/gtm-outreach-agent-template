"""Voice rules + per-user config loading.

The rules check drafts (email body/subject + LinkedIn surfaces) for AI tells
and personal-voice violations before they ship. The drafting loop regenerates
up to N times if any rule fails.

Universal rules (em-dashes, common AI buzzwords) ship hardcoded as
UNIVERSAL_BANNED_PHRASES. Per-user additions live in voice_config.yaml
inside the Profile/ directory and are passed via VoiceConfig.

This file is config-driven so every friend can plug in their own signature
and personal-voice list without forking the module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import yaml


UNIVERSAL_BANNED_PHRASES: tuple[str, ...] = (
    "I am writing to",
    "I wanted to reach out",
    "I'm reaching out because",
    "I came across",
    "passionate about",
    "excited about",
    "thrilled",
    "leverage",
    "synergy",
    "rockstar",
    "ninja",
    "Hope you're doing well",
    "just wanted to",
    "natural fit for",
    "is a natural fit",
    "feels like a natural fit",
    "Looking forward",
    "Best regards",
    "Sincerely,",
)


UNIVERSAL_BANNED_SUBJECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^application for ", re.IGNORECASE),
    re.compile(r"^exploring opportunities", re.IGNORECASE),
)


@dataclass(frozen=True)
class VoiceConfig:
    """Per-user voice rules. Built from voice_config.yaml or constructed in tests.
    signature is required; everything else has sensible defaults."""

    signature: str
    banned_phrases: tuple[str, ...] = ()
    banned_subject_patterns: tuple[re.Pattern[str], ...] = ()
    body_word_min: int = 50
    body_word_max: int = 110
    li_connect_max_chars: int = 300
    li_dm_max_chars: int = 500
    li_inmail_subject_max: int = 200
    li_inmail_body_max: int = 1500

    @property
    def all_banned_phrases(self) -> tuple[str, ...]:
        return UNIVERSAL_BANNED_PHRASES + tuple(self.banned_phrases)

    @property
    def all_banned_subject_patterns(self) -> tuple[re.Pattern[str], ...]:
        return UNIVERSAL_BANNED_SUBJECT_PATTERNS + tuple(self.banned_subject_patterns)


@dataclass
class VoiceCheckResult:
    ok: bool
    failures: list[str]


def _coerce_patterns(raw: Sequence[str] | None) -> tuple[re.Pattern[str], ...]:
    if not raw:
        return ()
    return tuple(re.compile(p, re.IGNORECASE) for p in raw)


@lru_cache(maxsize=8)
def load_voice_config(profile_dir: Path) -> VoiceConfig:
    """Load voice_config.yaml from the given profile directory. Cached by path.

    Raises FileNotFoundError if voice_config.yaml is missing — friends who
    haven't run `apply init` yet will see a clear error pointing them to setup.
    """
    config_path = profile_dir / "voice_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"voice_config.yaml not found at {config_path}. "
            "Run `uv run apply init` to scaffold your Profile/ pack, "
            "or copy Profile.example/voice_config.yaml into Profile/."
        )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if "signature" not in data or not str(data["signature"]).strip():
        raise ValueError(
            f"voice_config.yaml at {config_path} is missing a non-empty `signature` field."
        )

    return VoiceConfig(
        signature=str(data["signature"]).strip(),
        banned_phrases=tuple(data.get("banned_phrases") or ()),
        banned_subject_patterns=_coerce_patterns(data.get("banned_subject_patterns")),
        body_word_min=int(data.get("body_word_min", 50)),
        body_word_max=int(data.get("body_word_max", 110)),
        li_connect_max_chars=int(data.get("li_connect_max_chars", 300)),
        li_dm_max_chars=int(data.get("li_dm_max_chars", 500)),
        li_inmail_subject_max=int(data.get("li_inmail_subject_max", 200)),
        li_inmail_body_max=int(data.get("li_inmail_body_max", 1500)),
    )


def check_email(subject: str, body: str, *, config: VoiceConfig) -> VoiceCheckResult:
    failures: list[str] = []

    if "—" in subject or "—" in body:
        failures.append("contains em dash (—); use periods or commas")

    for phrase in config.all_banned_phrases:
        if phrase.lower() in body.lower() or phrase.lower() in subject.lower():
            failures.append(f"contains banned phrase: {phrase!r}")

    for pattern in config.all_banned_subject_patterns:
        if pattern.search(subject):
            failures.append(f"subject matches banned pattern: {pattern.pattern!r}")

    if subject.upper() == subject and any(c.isalpha() for c in subject):
        failures.append("subject is all caps")

    if any(ord(c) > 0x2700 for c in subject):
        failures.append("subject contains emoji-range character")

    body_stripped = body.strip()
    if not body_stripped.endswith(config.signature):
        failures.append(f"must sign off with just {config.signature!r} on its own line")

    word_count = len(body_stripped.split())
    if word_count > config.body_word_max:
        failures.append(f"word count {word_count} above {config.body_word_max} cap")
    elif word_count < config.body_word_min:
        failures.append(
            f"word count {word_count} below {config.body_word_min} floor "
            "(too short to carry the proof)"
        )

    return VoiceCheckResult(ok=not failures, failures=failures)


def _flag_banned(text: str, config: VoiceConfig, failures: list[str]) -> None:
    for phrase in config.all_banned_phrases:
        if phrase.lower() in text.lower():
            failures.append(f"contains banned phrase: {phrase!r}")


def check_li_connect(text: str, *, config: VoiceConfig) -> VoiceCheckResult:
    failures: list[str] = []
    if len(text) >= config.li_connect_max_chars:
        failures.append(
            f"connection note is {len(text)} chars; must be <{config.li_connect_max_chars}"
        )
    if "—" in text:
        failures.append("contains em dash")
    _flag_banned(text, config, failures)
    return VoiceCheckResult(ok=not failures, failures=failures)


def check_li_dm(text: str, *, config: VoiceConfig) -> VoiceCheckResult:
    failures: list[str] = []
    if len(text) >= config.li_dm_max_chars:
        failures.append(f"DM is {len(text)} chars; must be <{config.li_dm_max_chars}")
    if "—" in text:
        failures.append("contains em dash")
    _flag_banned(text, config, failures)
    return VoiceCheckResult(ok=not failures, failures=failures)


def check_li_inmail_subject(text: str, *, config: VoiceConfig) -> VoiceCheckResult:
    failures: list[str] = []
    if not text:
        failures.append("inmail subject is empty")
    if len(text) >= config.li_inmail_subject_max:
        failures.append(
            f"inmail subject is {len(text)} chars; "
            f"keep under {config.li_inmail_subject_max}"
        )
    if text.upper() == text and any(c.isalpha() for c in text):
        failures.append("inmail subject is all caps")
    if "—" in text:
        failures.append("contains em dash")
    _flag_banned(text, config, failures)
    return VoiceCheckResult(ok=not failures, failures=failures)


def check_li_inmail_body(text: str, *, config: VoiceConfig) -> VoiceCheckResult:
    """LinkedIn InMail cap is 1900 chars; we default to 1500 for safety margin."""
    failures: list[str] = []
    if not text:
        failures.append("inmail body is empty")
    if len(text) > config.li_inmail_body_max:
        failures.append(
            f"inmail body is {len(text)} chars; "
            f"must be <={config.li_inmail_body_max} (LinkedIn cap is 1900)"
        )
    if "—" in text:
        failures.append("contains em dash")
    _flag_banned(text, config, failures)
    return VoiceCheckResult(ok=not failures, failures=failures)
