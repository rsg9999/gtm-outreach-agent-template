"""Send-window scheduling. Given a recipient timezone and the agent's send rules
(Tue/Wed/Thu 07:00-09:00 by default, with 5-15 min jitter), compute the next valid send slot.
"""
from __future__ import annotations

import random
from datetime import datetime, time, timedelta

_WEEKDAY_INDEX = {
    "Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6,
}


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":", 1)
    return time(int(h), int(m))


def next_send_slot(
    now: datetime,
    *,
    send_days: tuple[str, ...] = ("Tue", "Wed", "Thu"),
    window_start: str = "07:00",
    window_end: str = "09:00",
    jitter_min: int = 5,
    jitter_max: int = 15,
    rng: random.Random | None = None,
) -> datetime:
    """Return the next datetime at which it is OK to send.

    Rules:
      - Must be on a weekday in `send_days` (e.g. Tue/Wed/Thu).
      - Must be within [window_start, window_end] in the same timezone as `now`.
      - If now is BEFORE today's window on a send day -> today @ window_start.
      - If now is INSIDE today's window on a send day -> now + jittered minutes (capped at window_end).
      - If now is AFTER today's window OR today is not a send day -> next send day @ window_start.
    """
    rng = rng or random.Random()
    allowed = {_WEEKDAY_INDEX[d] for d in send_days}
    start_t = _parse_hhmm(window_start)
    end_t = _parse_hhmm(window_end)

    today = now.replace(second=0, microsecond=0)
    today_window_start = today.replace(hour=start_t.hour, minute=start_t.minute)
    today_window_end = today.replace(hour=end_t.hour, minute=end_t.minute)

    if now.weekday() in allowed:
        if now < today_window_start:
            return today_window_start
        if today_window_start <= now <= today_window_end:
            jitter = timedelta(minutes=rng.randint(jitter_min, jitter_max))
            candidate = now + jitter
            if candidate > today_window_end:
                candidate = today_window_end
            return candidate

    # Otherwise step day-by-day forward to the next send day at window_start.
    cursor = today + timedelta(days=1)
    for _ in range(8):  # 7 days max plus a buffer
        if cursor.weekday() in allowed:
            return cursor.replace(hour=start_t.hour, minute=start_t.minute)
        cursor = cursor + timedelta(days=1)
    raise RuntimeError(f"No send day found in {send_days} within a week of {now}")
