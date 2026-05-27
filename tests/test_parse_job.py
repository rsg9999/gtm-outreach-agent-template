"""Voice-rule additions are tested in tests/test_voice_rules.py.

Tests for src/lib/parse_job.py.

TDD discipline: every assertion below was written before its production code existed.
"""
from __future__ import annotations

import json

import pytest

from src.lib.models import ParsedJob
from src.lib.parse_job import (
    clean_html,
    detect_source_site,
    extract_jd_payload,
    parse_claude_response,
    parse_job,
    resolve_ashby_canonical,
)


# --------------------------------------------------------------------------- #
# detect_source_site                                                          #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://jobs.lever.co/runwayml/abc-123", "lever"),
        ("https://boards.greenhouse.io/notion/jobs/4567", "greenhouse"),
        ("https://job-boards.greenhouse.io/anthropic/jobs/9876", "greenhouse"),
        ("https://anthropic.wd1.myworkdayjobs.com/External/job/Some-Role", "workday"),
        ("https://www.linkedin.com/jobs/view/3829471028", "linkedin"),
        ("https://www.indeed.com/viewjob?jk=abc123", "indeed"),
        ("https://jobs.ashbyhq.com/exampleco/9f6f540f", "ashby"),
        ("https://jobs.ashbyhq.com/Sierra/21a4df49", "ashby"),
        ("https://www.clay.com/jobs?ashby_jid=ed810aff", "ashby"),
        ("https://example.com/careers/some-role", "other"),
    ],
)
def test_detect_source_site_classifies_known_boards(url, expected):
    assert detect_source_site(url) == expected


def test_detect_source_site_is_case_insensitive():
    assert detect_source_site("HTTPS://JOBS.LEVER.CO/Foo/abc") == "lever"


# --------------------------------------------------------------------------- #
# clean_html                                                                  #
# --------------------------------------------------------------------------- #

def test_clean_html_strips_scripts_styles_and_nav():
    raw = """
    <html><head>
      <style>.x{}</style>
      <script>alert('x')</script>
    </head><body>
      <nav><a>home</a></nav>
      <header>top</header>
      <main><h1>Senior PM</h1><p>Help us build great things.</p></main>
      <footer>bottom</footer>
    </body></html>
    """
    cleaned = clean_html(raw)
    assert "alert" not in cleaned
    assert ".x{}" not in cleaned
    assert "Senior PM" in cleaned
    assert "Help us build great things." in cleaned
    # nav/header/footer text should be removed
    assert "home" not in cleaned
    assert "bottom" not in cleaned


def test_clean_html_collapses_whitespace():
    raw = "<html><body><p>One     line.</p>\n\n\n<p>Next.</p></body></html>"
    cleaned = clean_html(raw)
    assert "One line." in cleaned
    # No runs of 3+ blank lines
    assert "\n\n\n" not in cleaned


def test_clean_html_preserves_paragraph_breaks():
    raw = "<html><body><p>First paragraph.</p><p>Second paragraph.</p></body></html>"
    cleaned = clean_html(raw)
    assert "First paragraph." in cleaned
    assert "Second paragraph." in cleaned


# --------------------------------------------------------------------------- #
# resolve_ashby_canonical (custom-domain Ashby widgets)                       #
# --------------------------------------------------------------------------- #

def test_resolve_ashby_canonical_from_custom_domain_widget():
    """clay.com/jobs?ashby_jid=... is a widget; the host page references jobs.ashbyhq.com/claylabs/embed."""
    url = "https://www.clay.com/jobs?ashby_jid=ed810aff-a513-4359-b6bb-08e50e80b131"
    html = '<html>... script src="https://jobs.ashbyhq.com/claylabs/embed/something.js" ...</html>'
    out = resolve_ashby_canonical(url, html)
    assert out == "https://jobs.ashbyhq.com/claylabs/ed810aff-a513-4359-b6bb-08e50e80b131"


def test_resolve_ashby_canonical_returns_none_when_already_canonical():
    url = "https://jobs.ashbyhq.com/exampleco/9f6f540f-e323-49ab-8f4a-d77f9fe34968"
    html = "<html>...</html>"
    assert resolve_ashby_canonical(url, html) is None


def test_resolve_ashby_canonical_returns_none_for_non_ashby_urls():
    assert resolve_ashby_canonical("https://example.com/x", "<html></html>") is None


def test_resolve_ashby_canonical_returns_none_when_widget_lacks_slug():
    url = "https://acme.com/careers?ashby_jid=abc-123"
    html = "<html>nothing useful here</html>"
    assert resolve_ashby_canonical(url, html) is None


# --------------------------------------------------------------------------- #
# extract_jd_payload (Ashby JSON island)                                      #
# --------------------------------------------------------------------------- #

def test_extract_jd_payload_pulls_ashby_descriptionhtml():
    """Ashby embeds the JD as JSON-encoded HTML in a <script>. We extract just that fragment."""
    ashby_html = (
        '<html><head></head><body>'
        '<div id="root"></div>'
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"a":1,"posting":{"title":"GTM Engineer","descriptionHtml":'
        '"<p><strong>About RelayCo</strong></p><p>RelayCo is infrastructure for sending A2P messages.</p>'
        '<ul><li>Own the funnel</li></ul>",'
        '"locationName":"NYC"}}'
        '</script></body></html>'
    )
    out = extract_jd_payload(ashby_html, source_site="ashby")
    assert "About RelayCo" in out
    assert "RelayCo is infrastructure for sending A2P messages." in out
    assert "Own the funnel" in out


def test_extract_jd_payload_passes_through_for_non_ashby():
    html = "<html><body><main>Hello world</main></body></html>"
    assert extract_jd_payload(html, source_site="lever") is html


def test_extract_jd_payload_returns_input_when_ashby_marker_missing():
    html = "<html><body>nothing useful</body></html>"
    out = extract_jd_payload(html, source_site="ashby")
    # Should not crash; returns input so downstream cleaning still has a chance.
    assert out == html


# --------------------------------------------------------------------------- #
# parse_claude_response                                                       #
# --------------------------------------------------------------------------- #

def test_parse_claude_response_builds_parsed_job_from_json():
    payload = json.dumps({
        "company_name": "Anthropic",
        "company_domain": "anthropic.com",
        "role_title": "GTM Engineer",
        "location": "New York, NY",
        "jd_body": "We're hiring a GTM engineer to build pipeline automation.",
        "posted_date": "2026-04-15",
        "recruiter_name": None,
    })
    result = parse_claude_response(payload, job_url="https://example.com/role", source_site="other")
    assert isinstance(result, ParsedJob)
    assert result.company_name == "Anthropic"
    assert result.company_domain == "anthropic.com"
    assert result.role_title == "GTM Engineer"
    assert result.location == "New York, NY"
    assert "GTM engineer" in result.jd_body
    assert result.recruiter_name is None
    assert result.job_url == "https://example.com/role"
    assert result.source_site == "other"


def test_parse_claude_response_strips_code_fences():
    """Claude sometimes wraps JSON in ```json ... ``` fences. Must still parse."""
    payload = (
        "```json\n"
        + json.dumps({
            "company_name": "Notion",
            "role_title": "Founding PM",
            "jd_body": "Build the future.",
        })
        + "\n```"
    )
    result = parse_claude_response(payload, job_url="https://x", source_site="lever")
    assert result.company_name == "Notion"
    assert result.role_title == "Founding PM"


def test_parse_claude_response_handles_missing_optional_fields():
    payload = json.dumps({
        "company_name": "Tiny Startup",
        "role_title": "GTM Engineer",
        "jd_body": "Help us go to market.",
    })
    result = parse_claude_response(payload, job_url="https://y", source_site="other")
    assert result.company_domain is None
    assert result.location is None
    assert result.posted_date is None
    assert result.recruiter_name is None


def test_parse_claude_response_raises_on_missing_required_field():
    payload = json.dumps({"role_title": "PM", "jd_body": "x"})  # no company_name
    with pytest.raises(Exception):
        parse_claude_response(payload, job_url="https://z", source_site="other")


# --------------------------------------------------------------------------- #
# parse_job (end-to-end with mocks)                                           #
# --------------------------------------------------------------------------- #

def test_parse_job_orchestrates_fetch_clean_and_claude(monkeypatch):
    captured = {}

    def fake_fetch_html(url: str) -> str:
        captured["url"] = url
        return (
            "<html><body><main><h1>GTM Engineer</h1>"
            "<p>ExampleCo is hiring.</p></main></body></html>"
        )

    def fake_call_claude(prompt: str, model: str) -> str:
        captured["prompt"] = prompt
        captured["model"] = model
        return json.dumps({
            "company_name": "ExampleCo",
            "company_domain": "exampleco.example",
            "role_title": "GTM Engineer",
            "location": "Remote",
            "jd_body": "ExampleCo is hiring.",
            "posted_date": "2026-04-20",
            "recruiter_name": None,
        })

    monkeypatch.setattr("src.lib.parse_job.fetch_html", fake_fetch_html)
    monkeypatch.setattr("src.lib.parse_job.call_claude", fake_call_claude)

    job = parse_job("https://jobs.lever.co/exampleco/some-id")
    assert job.company_name == "ExampleCo"
    assert job.role_title == "GTM Engineer"
    assert job.source_site == "lever"
    assert job.job_url == "https://jobs.lever.co/exampleco/some-id"
    # The cleaned JD body must have made it into the prompt
    assert "ExampleCo is hiring." in captured["prompt"]
    # Haiku must be the model used
    assert "haiku" in captured["model"].lower()
