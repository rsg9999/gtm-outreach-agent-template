"""Tests for the deterministic follow-up bump pool."""
from __future__ import annotations

from src.lib.followups import select_bump
from src.lib.profile import parse_followup_pools

_SAMPLE = """## Email 2
- just bumping this up for you.
- floating this back up in case it got buried.

## Email 3
- last bump from me on this. no worries if the timing's off.
"""


def test_parse_followup_pools_splits_sections():
    pools = parse_followup_pools(_SAMPLE)
    assert pools["followup_1"] == ["just bumping this up for you.",
                                   "floating this back up in case it got buried."]
    assert pools["followup_2"] == ["last bump from me on this. no worries if the timing's off."]


def test_select_bump_is_stable_per_contact_and_step():
    pool = ["a", "b", "c", "d"]
    first = select_bump(pool, "jordan@acme.example", "followup_1")
    again = select_bump(pool, "jordan@acme.example", "followup_1")
    assert first == again  # deterministic across calls/processes
    assert first in pool


def test_select_bump_varies_across_contacts():
    pool = ["a", "b", "c", "d", "e", "f", "g", "h"]
    picks = {select_bump(pool, f"person{i}@x.example", "followup_1") for i in range(8)}
    assert len(picks) > 1  # not all identical
