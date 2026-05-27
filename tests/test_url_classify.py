"""Tests for url classification used by the apply CLI."""
from __future__ import annotations

import pytest

from src.lib.url_classify import classify_url


@pytest.mark.parametrize(
    "url,expected",
    [
        # LinkedIn posts (the new path)
        ("https://www.linkedin.com/posts/example-author_hiring-1234", "post"),
        ("https://www.linkedin.com/posts/foo_bar-12345", "post"),
        ("https://www.linkedin.com/feed/update/urn:li:activity:1234/", "post"),
        # LinkedIn JOBS — not a post, treat as a job page
        ("https://www.linkedin.com/jobs/view/3829471028", "job"),
        # Regular job boards
        ("https://jobs.ashbyhq.com/exampleco/9f6f540f", "job"),
        ("https://boards.greenhouse.io/notion/jobs/4567", "job"),
        ("https://example.com/careers/some-role", "job"),
    ],
)
def test_classify_url_known_patterns(url, expected):
    assert classify_url(url) == expected


def test_classify_url_is_case_insensitive():
    assert classify_url("HTTPS://WWW.LINKEDIN.COM/POSTS/foo") == "post"
