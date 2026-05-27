"""Tests for src/lib/parse_post.py — LinkedIn post URL fetcher."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


from src.lib.parse_post import (
    extract_author_name,
    extract_post_snippet,
    extract_profile_slug,
    humanize_slug,
    parse_post,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_extract_profile_slug_from_post_url():
    url = "https://www.linkedin.com/posts/javery_hiring-activity-1234"
    assert extract_profile_slug(url) == "javery"


def test_extract_profile_slug_from_feed_update_url():
    url = "https://www.linkedin.com/feed/update/urn:li:activity:1234"
    # Feed-update URLs don't contain a profile slug; we return None and rely on og:title.
    assert extract_profile_slug(url) is None


def test_extract_profile_slug_with_trailing_slash():
    assert extract_profile_slug("https://www.linkedin.com/posts/javery/") == "javery"


def test_extract_profile_slug_with_query_string():
    assert extract_profile_slug("https://www.linkedin.com/posts/javery?utm_source=share") == "javery"


def test_extract_profile_slug_with_fragment():
    assert extract_profile_slug("https://www.linkedin.com/posts/javery#comments") == "javery"


def test_extract_author_name_from_og_title():
    html = _read("li_post_rich.html")
    assert extract_author_name(html) == "Jordan Avery"


def test_extract_author_name_falls_back_to_slug_when_og_lacks_name():
    html = _read("li_post_locked.html")
    # og:title is just "LinkedIn", no author info. Falls back to None;
    # caller can use the slug from the URL.
    assert extract_author_name(html) is None


def test_extract_post_snippet_prefers_jsonld():
    html = _read("li_post_rich.html")
    snippet = extract_post_snippet(html)
    # JSON-LD articleBody is longer than og:description; prefer it.
    assert "full job descriptions" in snippet


def test_extract_post_snippet_falls_back_to_og():
    html = _read("li_post_og_only.html")
    snippet = extract_post_snippet(html)
    assert "Activation jumped" in snippet


def test_extract_post_snippet_returns_none_for_locked_page():
    html = _read("li_post_locked.html")
    snippet = extract_post_snippet(html)
    # The locked-page og:description is generic LinkedIn boilerplate; we filter it.
    assert snippet is None


def test_extract_post_snippet_keeps_real_content_containing_locked_phrase():
    """Filter must not false-positive on real posts that quote the locked-page phrasing."""
    html = '''<html><head>
<meta property="og:description" content="We removed the 'sign in to view' friction from our docs and saw a 47% trial increase." />
</head><body></body></html>'''
    snippet = extract_post_snippet(html)
    assert snippet is not None
    assert "sign in to view" in snippet
    assert "47%" in snippet


def test_parse_post_returns_full_model(monkeypatch):
    html = _read("li_post_rich.html")
    monkeypatch.setattr("src.lib.parse_post.fetch_html", lambda url: html)
    result = parse_post("https://www.linkedin.com/posts/javery_hiring-activity-1234")
    assert result.author_name == "Jordan Avery"
    assert result.profile_slug == "javery"
    assert result.post_url == "https://www.linkedin.com/posts/javery_hiring-activity-1234"
    assert "growth" in (result.post_snippet or "")
    assert isinstance(result.fetched_at, datetime)


def test_parse_post_handles_locked_page(monkeypatch):
    """Locked page returns a ParsedPost with snippet=None and a logged warning."""
    html = _read("li_post_locked.html")
    monkeypatch.setattr("src.lib.parse_post.fetch_html", lambda url: html)
    result = parse_post("https://www.linkedin.com/posts/sam_locked-7299")
    assert result.post_snippet is None
    # extract_profile_slug returns "sam"; extract_author_name returns None
    # (og:title is just "LinkedIn"); so author_name falls back to humanize_slug("sam").
    assert result.author_name == "Sam"


def test_extract_author_name_from_ugc_format():
    """LinkedIn UGC posts format og:title as '<post body> | Author Name | N comments'."""
    html = _read("li_post_ugc.html")
    assert extract_author_name(html) == "Casey Mendez"


def test_extract_author_name_decodes_html_entities():
    """LinkedIn often HTML-encodes apostrophes in og:title (Alex &#39;AJ&#39; Rivera)."""
    html = '<html><head><meta property="og:title" content="Alex &#39;AJ&#39; Rivera on LinkedIn: Acme is hiring" /></head></html>'
    assert extract_author_name(html) == "Alex 'AJ' Rivera"


def test_extract_post_snippet_decodes_html_entities():
    """Entities in og:description should be decoded (e.g. &amp;, &#39;, &quot;)."""
    html = '<html><head><meta property="og:description" content="It&#39;s our biggest launch &amp; we&#39;re hiring" /></head></html>'
    snippet = extract_post_snippet(html)
    assert snippet == "It's our biggest launch & we're hiring"


def test_extract_author_name_handles_pipe_format_without_comments():
    """A '<post body> | Author Name' (no trailing comments segment) should still resolve."""
    html = '<html><head><meta property="og:title" content="Some post body | Jane Doe" /></head></html>'
    assert extract_author_name(html) == "Jane Doe"


def test_extract_author_name_returns_none_for_unknown_format():
    """og:title that doesn't match either pattern returns None so caller falls back to slug."""
    html = '<html><head><meta property="og:title" content="just some random title" /></head></html>'
    assert extract_author_name(html) is None


def test_humanize_slug():
    assert humanize_slug("casey-mendez") == "Casey Mendez"
    assert humanize_slug("jordan-avery") == "Jordan Avery"
    assert humanize_slug("sam") == "Sam"
    assert humanize_slug("jane-doe-cto") == "Jane Doe Cto"


def test_parse_post_humanizes_slug_when_og_title_is_ugc_unparseable(monkeypatch):
    """Real-world: a UGC post's og:title parses correctly via the pipe format."""
    html = _read("li_post_ugc.html")
    monkeypatch.setattr("src.lib.parse_post.fetch_html", lambda url: html)
    result = parse_post("https://www.linkedin.com/posts/casey-mendez_day-1-2-prs-ugcPost-7457")
    assert result.author_name == "Casey Mendez"
    assert result.profile_slug == "casey-mendez"
    assert result.post_snippet and "GitHub repo" in result.post_snippet
