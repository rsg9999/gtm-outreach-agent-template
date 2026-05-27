"""Fetch a LinkedIn post URL and recover what we can from public meta + JSON-LD.

LinkedIn requires login for most content, but the public HTML embeds OG meta tags and
sometimes a JSON-LD block for SEO. We extract:
  - author name (from og:title — typically "Name on LinkedIn: ...")
  - profile slug (from URL path)
  - post snippet (from JSON-LD articleBody, falling back to og:description)

When the page is locked enough that we get only generic LinkedIn boilerplate, we return
ParsedPost with post_snippet=None. The drafter detects None and degrades to an honest
credentialed pitch (no fabricated post quotes).

No Anthropic API calls. No Clay credits.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from html import unescape

import httpx
from bs4 import BeautifulSoup

from src.lib.models import ParsedPost

log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Exact-match (after lowercasing + trailing-period strip) detection of the boilerplate
# strings LinkedIn returns for locked / unauth pages. Substring matching is unsafe
# because a real post body could contain the phrase as part of its content.
_LOCKED_DESCRIPTIONS = frozenset({
    "linkedin is the world's largest professional network.",
    "sign in to view",
})


def fetch_html(url: str) -> str:
    """Fetch a URL and return raw HTML. Mockable in tests."""
    resp = httpx.get(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        follow_redirects=True,
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.text


def extract_profile_slug(url: str) -> str | None:
    """Pull the profile slug from a /posts/<slug>... URL.

    Handles slug terminators: `_` (precedes the activity id), `/` (trailing slash),
    `?` (query string), `#` (fragment), or end-of-string. Returns None for
    /feed/update/ URLs (no profile slug).
    """
    m = re.search(r"linkedin\.com/posts/([A-Za-z0-9-]+?)(?:_|[/?#]|$)", url)
    if m:
        return m.group(1)
    return None


def extract_author_name(html: str) -> str | None:
    """Pull the author name from og:title.

    LinkedIn uses two main formats depending on post type:
      1. Personal/share posts:  "Jordan Avery on LinkedIn: ..."
      2. UGC posts:             "<post body> | Casey Mendez | N comments"

    Returns None when og:title is missing, is just the generic 'LinkedIn',
    or doesn't match either format (caller should fall back to URL-slug humanization).
    """
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("meta", property="og:title")
    if not tag or not tag.get("content"):
        return None
    # HTML-decode in case og:title contains entities (e.g. "Alex &#39;AJ&#39; Rivera").
    title = unescape(tag["content"]).strip()
    if title.lower() == "linkedin":
        return None
    # Format 1: "Jordan Avery on LinkedIn: ..." → "Jordan Avery"
    if " on LinkedIn" in title:
        return title.split(" on LinkedIn", 1)[0].strip() or None
    # Format 2: "<post body> | Author Name | N comments" or "<post body> | Author Name"
    # Take the second-to-last pipe-segment when the last segment is "<digits> comments".
    if "|" in title:
        parts = [p.strip() for p in title.split("|")]
        if len(parts) >= 2:
            last = parts[-1].lower()
            # If the last segment is a comments count, the author is the previous segment.
            if " comment" in last or " repost" in last or " like" in last:
                if len(parts) >= 3:
                    return parts[-2] or None
            else:
                # No trailing metric — assume "<post body> | Author Name"
                return parts[-1] or None
    # Doesn't match either format. Return None so the caller can humanize the URL slug.
    return None


def humanize_slug(slug: str) -> str:
    """Convert 'casey-mendez' to 'Casey Mendez'. Last-resort fallback for the author name."""
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def _extract_jsonld_body(html: str) -> str | None:
    """Walk all <script type="application/ld+json"> tags and return the first articleBody we find."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            body = payload.get("articleBody")
            if isinstance(body, str) and body.strip():
                return unescape(body).strip()
    return None


def _extract_og_description(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("meta", property="og:description")
    if tag and tag.get("content"):
        text = unescape(tag["content"]).strip()
        normalized = text.lower().rstrip(".")
        # Compare without trailing period so both "...network." and "...network" match.
        if any(normalized == loc.rstrip(".") for loc in _LOCKED_DESCRIPTIONS):
            return None
        return text or None
    return None


def extract_post_snippet(html: str) -> str | None:
    """Best-effort extraction of the post text. Prefer JSON-LD articleBody, fall back to og:description.

    Returns None for locked / generic LinkedIn pages.
    """
    body = _extract_jsonld_body(html)
    if body:
        return body
    return _extract_og_description(html)


def parse_post(url: str) -> ParsedPost:
    """Fetch + parse a LinkedIn post URL. Always returns a ParsedPost, possibly with post_snippet=None."""
    log.info("parse_post: url=%s", url)
    html = fetch_html(url)
    slug = extract_profile_slug(url) or "unknown"
    author = extract_author_name(html) or (humanize_slug(slug) if slug != "unknown" else slug)
    snippet = extract_post_snippet(html)
    if snippet is None:
        log.warning("parse_post: no usable snippet recovered from %s; drafter will degrade", url)
    return ParsedPost(
        author_name=author,
        profile_slug=slug,
        post_url=url,
        post_snippet=snippet,
        fetched_at=datetime.now(),
    )
