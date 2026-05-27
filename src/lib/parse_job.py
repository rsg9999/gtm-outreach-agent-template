"""Step 2: Job parsing. Fetch URL, clean HTML, extract structured fields with Haiku."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.lib.config import load_config
from src.lib.models import ParsedJob

log = logging.getLogger(__name__)

_KNOWN_BOARDS = (
    ("lever", ("jobs.lever.co", "lever.co")),
    ("greenhouse", ("greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io")),
    ("workday", ("myworkdayjobs.com", "workday.com")),
    ("linkedin", ("linkedin.com",)),
    ("indeed", ("indeed.com",)),
    # Ashby: either hosted on jobs.ashbyhq.com, or embedded on a custom domain
    # using the ashby_jid query param (e.g. clay.com/jobs?ashby_jid=...).
    ("ashby", ("jobs.ashbyhq.com", "ashbyhq.com", "ashby_jid=")),
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def resolve_ashby_canonical(url: str, html: str) -> str | None:
    """Resolve a custom-domain Ashby widget URL (e.g. clay.com/jobs?ashby_jid=...) to its
    canonical jobs.ashbyhq.com/{slug}/{posting_id} URL.

    Returns None when the URL is already canonical, the page is not Ashby, or the slug can't be found.
    """
    lower = url.lower()
    if "jobs.ashbyhq.com" in lower:
        return None
    m = re.search(r"[?&]ashby_jid=([0-9a-f-]+)", url, flags=re.IGNORECASE)
    if not m:
        return None
    jid = m.group(1)
    slug_match = re.search(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)/embed", html)
    if not slug_match:
        return None
    slug = slug_match.group(1)
    return f"https://jobs.ashbyhq.com/{slug}/{jid}"


def _extract_json_string_value(blob: str, start_index: int) -> str | None:
    """Given text and the index where a JSON-encoded string value begins (the opening "), return its decoded contents.

    Walks the string respecting JSON escape rules to find the closing unescaped quote.
    """
    if start_index >= len(blob) or blob[start_index] != '"':
        return None
    i = start_index + 1
    out: list[str] = []
    while i < len(blob):
        c = blob[i]
        if c == "\\":
            if i + 1 >= len(blob):
                return None
            esc = blob[i + 1]
            mapping = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
            if esc in mapping:
                out.append(mapping[esc])
                i += 2
                continue
            if esc == "u" and i + 5 < len(blob):
                try:
                    out.append(chr(int(blob[i + 2 : i + 6], 16)))
                except ValueError:
                    return None
                i += 6
                continue
            out.append(esc)
            i += 2
            continue
        if c == '"':
            return "".join(out)
        out.append(c)
        i += 1
    return None


def extract_jd_payload(html: str, *, source_site: str) -> str:
    """Pre-clean step. For SPA-rendered boards, pull the JD HTML out of the JSON island.

    Currently special-cases Ashby. For everything else, returns the input unchanged so
    downstream `clean_html` can run normally.
    """
    if source_site != "ashby":
        return html
    marker = '"descriptionHtml":'
    idx = html.find(marker)
    if idx < 0:
        return html
    value_start = idx + len(marker)
    decoded = _extract_json_string_value(html, value_start)
    if decoded is None:
        return html
    return decoded


def detect_source_site(url: str) -> str:
    """Classify a job URL by host. Returns 'other' for anything unrecognized."""
    host = url.lower()
    for label, hosts in _KNOWN_BOARDS:
        if any(h in host for h in hosts):
            return label
    return "other"


def clean_html(html: str) -> str:
    """Strip noise from a job-posting page and return readable text."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse runs of whitespace inside lines but preserve paragraph breaks.
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    # Cap consecutive blank lines at one
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _strip_code_fences(text: str) -> str:
    """Pull a JSON value out of a Claude response.

    Handles three common shapes:
      1. raw JSON
      2. ```json ... ``` fences (or just ```)
      3. JSON preceded/followed by prose (we slice from the first { to the last })
    """
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", s)
        if s.endswith("```"):
            s = s[: -len("```")]
        s = s.strip()
    # If there's still leading/trailing prose, slice from the first { to the matching last }.
    # (We assume the JSON is an object; for arrays callers can handle separately.)
    if not s.startswith("{") and "{" in s and "}" in s:
        first = s.find("{")
        last = s.rfind("}")
        if first != -1 and last != -1 and last > first:
            s = s[first : last + 1]
    return s.strip()


def parse_claude_response(text: str, *, job_url: str, source_site: str) -> ParsedJob:
    """Parse a JSON string returned by Claude into a ParsedJob.

    Pydantic raises on missing required fields. Optional fields default to None.
    """
    payload: dict[str, Any] = json.loads(_strip_code_fences(text))
    payload["job_url"] = job_url
    payload["source_site"] = source_site
    return ParsedJob.model_validate(payload)


def fetch_html(url: str) -> str:
    """Fetch a URL and return the raw HTML string. Mockable in tests."""
    resp = httpx.get(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        follow_redirects=True,
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.text


def _build_prompt(cleaned_text: str, job_url: str) -> str:
    return (
        "You are extracting structured fields from a job posting. "
        "Return ONLY a single JSON object, no prose, no code fences. "
        "Schema: {\n"
        '  "company_name": string,\n'
        '  "company_domain": string|null,\n'
        '  "role_title": string,\n'
        '  "location": string|null,\n'
        '  "jd_body": string (the JD itself, condensed; 200-2000 chars),\n'
        '  "posted_date": string|null (ISO yyyy-mm-dd if present),\n'
        '  "recruiter_name": string|null (only if explicitly named in the JD as the recruiter or contact)\n'
        "}\n\n"
        f"JOB_URL: {job_url}\n\n"
        "PAGE_TEXT:\n"
        f"{cleaned_text}\n"
    )


def call_claude(prompt: str, model: str) -> str:
    """Call the Anthropic Messages API with a single user turn. Returns the text content.

    Wrapped as a top-level function so tests can monkeypatch it without touching the SDK.
    """
    from anthropic import Anthropic  # local import to keep test discovery fast

    cfg = load_config()
    client = Anthropic(api_key=cfg.anthropic_api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = []
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def parse_job(url: str) -> ParsedJob:
    """Fetch, clean, and structure a job posting into a ParsedJob."""
    cfg = load_config()
    source = detect_source_site(url)
    log.info("parse_job: url=%s source=%s", url, source)

    html = fetch_html(url)
    if source == "ashby":
        canonical = resolve_ashby_canonical(url, html)
        if canonical:
            log.info("parse_job: resolved Ashby widget to canonical %s", canonical)
            html = fetch_html(canonical)
    payload = extract_jd_payload(html, source_site=source)
    cleaned = clean_html(payload)
    if len(cleaned) < 80:
        # Very short page is usually a JS-rendered SPA we couldn't read.
        log.warning("parse_job: cleaned text only %d chars; result may be poor", len(cleaned))

    prompt = _build_prompt(cleaned, url)
    raw = call_claude(prompt, cfg.parse_model)
    return parse_claude_response(raw, job_url=url, source_site=source)
