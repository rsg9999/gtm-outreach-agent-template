"""Classify a URL as either a job page or a LinkedIn post.

Used by the apply CLI to route the same positional argument to the right pipeline.
"""
from __future__ import annotations

from typing import Literal

UrlKind = Literal["job", "post"]


def classify_url(url: str) -> UrlKind:
    """Return 'post' if the URL points at a LinkedIn social post; 'job' otherwise."""
    lower = url.lower()
    if "linkedin.com/posts/" in lower or "linkedin.com/feed/update/" in lower:
        return "post"
    return "job"
