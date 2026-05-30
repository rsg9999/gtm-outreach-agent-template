"""Deterministic follow-up bump selection. No LLM, no voice gate."""
from __future__ import annotations

import hashlib


def select_bump(pool: list[str], contact_email: str, step: str) -> str:
    """Pick a pool line that is stable for a given (contact, step) across processes.

    Uses hashlib (not the per-process-salted builtin hash) so re-running the loop
    selects the same line — required for idempotency.
    """
    if not pool:
        raise ValueError("follow-up pool is empty")
    key = f"{contact_email}|{step}".encode("utf-8")
    idx = int(hashlib.sha256(key).hexdigest(), 16) % len(pool)
    return pool[idx]
