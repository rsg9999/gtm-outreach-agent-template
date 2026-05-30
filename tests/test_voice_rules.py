"""Tests for src/lib/voice_rules.py — voice enforcement that flags AI tells before drafts ship.

VoiceConfig is constructed explicitly in tests rather than read from voice_config.yaml,
so tests stay deterministic and don't depend on a Profile/ directory existing.
"""
from __future__ import annotations

import pytest

from src.lib.voice_rules import (
    VoiceConfig,
    check_email,
    check_li_connect,
    check_li_dm,
)


# Default fixture mirrors a strict personal voice config. Friends customize this
# via voice_config.yaml.
_PERSONAL_BANNED = (
    "if you have a minute",
    "no worries if not",
    "let me know your availability",
    "is exactly the kind of work",
    "the kind of work I've been",
    "I think that",
    "maps well",
    "want to reach out",
    "Sounds like what",
    "where the company is heading",
    "builder instinct",
    "Quick context",
    "Quick note",
    "one of three I'm serious about",
)


def _config(signature: str = "Alex") -> VoiceConfig:
    return VoiceConfig(signature=signature, banned_phrases=_PERSONAL_BANNED)


# Body that satisfies non-phrase checks: ~90 words, no em dashes, signs off with the signature.
def _good_body(extra_phrase: str = "", signature: str = "Alex") -> str:
    sentence = (
        f"{extra_phrase} I built the self-serve funnel from scratch and ran the lifecycle program end to end."
    ).strip()
    body = " ".join([sentence] * 6) + f"\n\n{signature}"
    words = body.split()
    while len(words) < 90:
        words.insert(-1, "ship")
    while len(words) > 95:
        words.pop(-2)
    return " ".join(words)


@pytest.mark.parametrize(
    "phrase",
    [
        "is exactly the kind of work",
        "feels like a natural fit",
        "natural fit for",
        "I think that",
        "maps well",
        "want to reach out",
        "Looking forward",
        "Best regards",
        "Quick context",
        "Quick note",
        "one of three I'm serious about",
    ],
)
def test_check_email_flags_new_ai_tells(phrase):
    body = _good_body(phrase)
    res = check_email("the role at acme", body, config=_config())
    assert not res.ok, f"expected {phrase!r} to be banned"
    assert any(phrase.lower() in f.lower() for f in res.failures)


def test_check_email_still_passes_clean_body():
    res = check_email("the role at acme", _good_body(), config=_config())
    assert res.ok, f"clean body should pass; failures: {res.failures}"


def test_check_email_phrase_ban_is_case_insensitive():
    body = _good_body("IS EXACTLY THE KIND OF WORK")
    res = check_email("subject", body, config=_config())
    assert not res.ok


def test_check_li_connect_flags_em_dash_and_length():
    too_long = "x" * 320
    res = check_li_connect(too_long, config=_config())
    assert not res.ok


def test_check_li_dm_flags_em_dash():
    dm = "Saw the role at Acme—loved it."
    res = check_li_dm(dm, config=_config())
    assert not res.ok


def test_existing_past_drafts_phrasing_still_passes():
    """Sanity: phrases used in the user's past_drafts.md must NOT be flagged."""
    body_with_loom = _good_body("Happy to send a Loom of one of the systems first.")
    assert check_email("subject", body_with_loom, config=_config()).ok

    body_with_15min = _good_body("Would love 15 min if you're open.")
    assert check_email("subject", body_with_15min, config=_config()).ok

    body_with_extra = _good_body("I previously shipped three products from zero.")
    assert check_email("subject", body_with_extra, config=_config()).ok


def test_signature_is_configurable():
    """Different friends sign off with different names; the rule follows the config."""
    body = _good_body(signature="Pat")
    res = check_email("subject", body, config=_config(signature="Pat"))
    assert res.ok, f"body ending with 'Pat' should pass when signature='Pat'; failures: {res.failures}"

    res_mismatch = check_email("subject", body, config=_config(signature="Alex"))
    assert not res_mismatch.ok
    assert any("sign off" in f.lower() for f in res_mismatch.failures)


def test_universal_banned_phrases_flagged_even_without_personal_config():
    """A VoiceConfig with no personal banned_phrases still flags universal AI tells."""
    minimal = VoiceConfig(signature="Alex")  # no personal banned_phrases
    body = _good_body("Looking forward to your response")  # universal phrase
    res = check_email("subject", body, config=minimal)
    assert not res.ok
    assert any("looking forward" in f.lower() for f in res.failures)
