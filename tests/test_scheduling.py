"""Tests for src/lib/scheduling.py — next valid send slot computation.

The agent sends drafts only Tue/Wed/Thu, 7am-9am recipient local time, with 5-15 min jitter.
This module figures out when the NEXT valid slot is given a "now" timestamp.
"""
from __future__ import annotations

from datetime import datetime

from src.lib.scheduling import next_send_slot


def _at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """Build a naive datetime at the given wall-clock time (treated as local-tz wall clock)."""
    return datetime(year, month, day, hour, minute)


def test_next_send_slot_monday_morning_returns_tuesday_window():
    """Monday at 9am should schedule for Tuesday 07:00."""
    now = _at(2026, 5, 4, 9, 0)  # 2026-05-04 is a Monday
    slot = next_send_slot(now, send_days=("Tue", "Wed", "Thu"), window_start="07:00", window_end="09:00")
    assert slot.weekday() == 1  # Tuesday
    assert (slot.hour, slot.minute) == (7, 0)


def test_next_send_slot_during_send_window_returns_next_jitter_slot_same_day():
    """Tuesday at 7:30am: still inside send window, so returns same-day slot at 7:30 + jitter."""
    now = _at(2026, 5, 5, 7, 30)  # Tuesday
    slot = next_send_slot(now, send_days=("Tue", "Wed", "Thu"), window_start="07:00", window_end="09:00")
    assert slot.weekday() == 1
    assert slot.year == 2026 and slot.month == 5 and slot.day == 5
    assert 7 <= slot.hour <= 9


def test_next_send_slot_after_window_on_send_day_returns_next_send_day():
    """Tuesday at 10am (past window) should schedule for Wednesday 07:00."""
    now = _at(2026, 5, 5, 10, 0)
    slot = next_send_slot(now, send_days=("Tue", "Wed", "Thu"), window_start="07:00", window_end="09:00")
    assert slot.weekday() == 2  # Wednesday
    assert (slot.hour, slot.minute) == (7, 0)


def test_next_send_slot_friday_returns_next_tuesday():
    """Friday should skip weekend, return next Tuesday."""
    now = _at(2026, 5, 8, 12, 0)  # Friday
    slot = next_send_slot(now, send_days=("Tue", "Wed", "Thu"), window_start="07:00", window_end="09:00")
    assert slot.weekday() == 1
    assert (slot.hour, slot.minute) == (7, 0)


def test_next_send_slot_thursday_after_window_returns_next_tuesday():
    """Thursday at noon should schedule for Tuesday (skipping Fri/Sat/Sun/Mon)."""
    now = _at(2026, 5, 7, 12, 0)  # Thursday
    slot = next_send_slot(now, send_days=("Tue", "Wed", "Thu"), window_start="07:00", window_end="09:00")
    assert slot.weekday() == 1


def test_next_send_slot_thursday_before_window_returns_thursday():
    """Thursday at 6am should schedule for Thursday 07:00 (still before window)."""
    now = _at(2026, 5, 7, 6, 0)
    slot = next_send_slot(now, send_days=("Tue", "Wed", "Thu"), window_start="07:00", window_end="09:00")
    assert slot.weekday() == 3
    assert (slot.hour, slot.minute) == (7, 0)


def test_next_send_slot_respects_custom_send_days():
    """If send_days = (Mon,) only, Friday should return next Monday."""
    now = _at(2026, 5, 8, 12, 0)  # Friday
    slot = next_send_slot(now, send_days=("Mon",), window_start="08:00", window_end="10:00")
    assert slot.weekday() == 0  # Monday
