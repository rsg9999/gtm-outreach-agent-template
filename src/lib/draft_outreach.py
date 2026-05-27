"""Artifact-driven email drafter. One Sonnet call per contact (with up to 3 voice-rule regens).

Two modes:
  - Post mode: contact is a LinkedIn post author. Personalization input = post snippet.
  - JD mode:   contact came from a Clay company search. Personalization input = JD body.

Static block (cached across contacts in a run): profile pack + voice rules + both-mode template.
Dynamic block (per contact): mode + artifact text + recipient info.

Output JSON: {"email": {"subject": "...", "body": "..."}}.

Voice rules from voice_rules.check_email are enforced post-output; up to 3 regen attempts
on failure, then DraftError.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from src.lib.config import load_config
from src.lib.models import Contact, EmailDraft, LinkedInDraft, ParsedJob, ParsedPost
from src.lib.parse_job import _strip_code_fences
from src.lib.profile import load_profile
from src.lib.voice_rules import (
    VoiceConfig,
    check_email,
    check_li_connect,
    check_li_dm,
    load_voice_config,
)

log = logging.getLogger(__name__)


class DraftError(RuntimeError):
    """Raised when the drafter can't produce an email passing voice rules within max_attempts."""


def call_claude_cached(prompt_blocks: list[dict], model: str, *, temperature: float = 0.7) -> str:
    """Call Anthropic with content blocks. Blocks marked with cache_control are cached.

    Defined as a module-level symbol so tests can monkeypatch the network call.
    Opus 4.x deprecated the `temperature` parameter; we omit it for those models.
    """
    from anthropic import Anthropic

    cfg = load_config()
    client = Anthropic(api_key=cfg.anthropic_api_key)
    kwargs: dict = {
        "model": model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt_blocks}],
    }
    if "opus" not in model.lower():
        kwargs["temperature"] = temperature
    msg = client.messages.create(**kwargs)
    parts = []
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def build_static_block(voice_config: VoiceConfig) -> str:
    """Profile pack + voice rules + both-mode template instructions. Cached across contacts in a run.

    voice_config supplies the signature and word-count bounds so the prompt
    matches the friend's actual voice rules rather than hardcoded defaults.
    """
    profile = load_profile().as_prompt_block()
    sig = voice_config.signature
    target_min = voice_config.body_word_min
    target_max = voice_config.body_word_max
    return f"""You are the writer described in the PROFILE PACK below. You are writing one cold email to a real person who has 30 seconds in their inbox at 7am. The bar: it reads like you wrote it on your phone in 90 seconds. Not templated. Not "outreach." Not AI.

OUTPUT FORMAT
=============
Your entire response is a single valid JSON object. No prose outside it. No code fences.

{{
  "email": {{
    "subject": "<short, sentence case>",
    "body": "<opens with 'Hi <First Name>,' or 'Hey <First Name>,' on its own line. {target_min}-{target_max} words total. Signs off '{sig}' on its own line.>"
  }},
  "linkedin": {{
    "connection_note": "<UNDER 300 chars. Sent with a LinkedIn connection request. One short, specific reason to connect. References the post or role briefly. NO 'Hi <name>' (LinkedIn shows the name automatically). NO sign-off.>",
    "dm": "<UNDER 500 chars. Sent AFTER the recipient accepts the connection request, NOT before. Three short sentences max: hook (post/role reference) + ONE concrete proof point with a number + low-friction ask. NO greeting, NO sign-off (LinkedIn DMs are inline). Should NOT repeat the email body verbatim — assume you have already sent the email.>"
  }}
}}

TWO MODES (the dynamic block below tells you which one applies)
================================================================

POST MODE (recipient is a LinkedIn post author):
  - The opener MUST establish HOW you found them. The recipient is getting a cold email and needs context — without it they wonder where this came from. Start with "Saw your post on LinkedIn..." or "Read your LinkedIn post..." or "Your post on LinkedIn about X..." or similar. This is non-negotiable.
  - After the context-setter, quote or paraphrase a real specific phrase from the snippet. Never fabricate beyond the snippet.
  - Pick ONE proof point from proof_points.md that ties to what they said.
  - One ask, low-friction: "Would love 15 min if you are open" or "Happy to send a Loom of one of the systems first if easier than a call."
  - Subject: when a specific role is identifiable from the post or the JD, anchor the subject on the ROLE (e.g. "the growth engineer role at Attention", "the founding PM role", "the GTM engineer role"). Only fall back to the post topic when no role is identifiable. Must read like a human-typed sentence fragment, NOT a recruiter/marketing blast. Use lowercase or sentence case. Use definite article ("the X role" not "X role"). NO title case, NO all-caps, NO emojis, NO "Application for X at Y", NO "Exploring opportunities", NO "Re:" prefix unless actually replying. If the subject could appear in a marketing newsletter or a spam folder, rewrite it.
  - If the dynamic block says the snippet is unavailable (LinkedIn locked the page), DO NOT fabricate. Open with an honest "Saw your post on LinkedIn about [role/topic if recoverable from URL slug, else just 'the role']" and write a credentialed pitch like JD mode.

JD MODE (recipient came from a Clay company search):
  - Opener: "Just applied for the [role] at [company]" or honest direct variant referencing the role.
  - Pick ONE proof point that ties to the JD's core requirement.
  - For founders/co-founders (recipient title contains "Founder", "Co-Founder", or "CEO"): the opener may add ONE warmer line per voice.md (founder-to-founder is fair). Soft option, not required.
  - One ask. Low-friction. Same as post mode.
  - Subject: "the [role] role at [company]" or similar. Never "Application for X at Y."

VOICE RULES (NON-NEGOTIABLE — voice.md is enforced after output, regen on failure)
==================================================================================
- {target_min}-{target_max} words.
- No em dashes anywhere. Use periods or commas.
- No "I am writing", "I came across", "passionate about", "leverage", "thrilled", "Looking forward", "Best regards", or other obvious AI tells / corporate-speak. See voice.md for the full list.
- No three-paragraph rhythm where every paragraph is the same length. Vary sentence length.
- One specific opener (post snippet OR role/JD reference). Not generic flattery.
- ONE proof point with a number. Not three.
- ONE ask. Not two.
- Sign off with just "{sig}" on its own line.
- Open with a salutation: "Hi <First Name>," or "Hey <First Name>,".

PROFILE PACK
============
{profile}
"""


def build_dynamic_block(*, contact: Contact, job: ParsedJob | None, post: ParsedPost | None) -> str:
    """Per-contact prompt: mode + artifact + recipient info. NOT cached."""
    if post is not None and contact.source == "post_author":
        return _post_mode_block(contact=contact, post=post, job=job)
    if job is not None:
        return _jd_mode_block(contact=contact, job=job)
    raise ValueError("build_dynamic_block requires a job (JD mode) or a post (post mode)")


def _post_mode_block(*, contact: Contact, post: ParsedPost, job: ParsedJob | None = None) -> str:
    if post.post_snippet:
        snippet_block = (
            "POST SNIPPET (real text from the recipient's post — anchor your opener to a specific phrase here):\n"
            f"  {post.post_snippet[:2000]}"
        )
        degrade_note = ""
    else:
        snippet_block = "POST SNIPPET: (unavailable — LinkedIn returned no usable text)"
        degrade_note = (
            "\nSnippet is unavailable. DO NOT fabricate post content. Open with an honest "
            "reference to the role or the act of seeing their post, then write a credentialed pitch "
            "in JD-mode style (proof point + ask). Honest beats fake.\n"
        )

    jd_block = ""
    if job is not None and job.jd_body:
        jd_block = (
            "\nJOB DESCRIPTION (additional context — the role they posted about). "
            "Still anchor the OPENER to the post snippet; use the JD to pick a TIGHTER proof point "
            "that ties to the specific requirements. Do NOT switch to JD-mode 'Just applied for...' opener:\n"
            f"  role: {job.role_title}\n"
            f"  company: {job.company_name}\n"
            f"  jd:\n{job.jd_body[:2000]}\n"
        )

    first_name = (contact.name or "there").split()[0]
    return f"""MODE: POST

RECIPIENT
  name: {contact.name}
  first_name_for_salutation: {first_name}
  title: {contact.title}
  company: {contact.company}
  linkedin: {contact.linkedin_url or "(unknown)"}
  email: {contact.email or "(none)"}

{snippet_block}
{degrade_note}{jd_block}
NOW WRITE THE EMAIL. Output JSON only.
"""


def _jd_mode_block(*, contact: Contact, job: ParsedJob) -> str:
    first_name = (contact.name or "there").split()[0]
    return f"""MODE: JD

RECIPIENT
  name: {contact.name}
  first_name_for_salutation: {first_name}
  title: {contact.title}
  company: {contact.company}
  linkedin: {contact.linkedin_url or "(unknown)"}
  email: {contact.email or "(none)"}

ROLE
  company: {job.company_name}
  domain: {job.company_domain or "(unknown)"}
  role: {job.role_title}
  location: {job.location or "(unknown)"}
  jd:
{job.jd_body[:2000]}

NOW WRITE THE EMAIL. Output JSON only.
"""


def _build_prompt_blocks(
    *,
    voice_config: VoiceConfig,
    contact: Contact,
    job: ParsedJob | None,
    post: ParsedPost | None,
    prior_failures: list[str] | None = None,
) -> list[dict]:
    static = build_static_block(voice_config)
    dynamic = build_dynamic_block(contact=contact, job=job, post=post)
    if prior_failures:
        dynamic += (
            "\nPRIOR ATTEMPT FAILED THESE CHECKS. Fix every one before output:\n"
            + "\n".join(f"  - {f}" for f in prior_failures)
            + "\n"
        )
    return [
        {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic},
    ]


def parse_outreach_response(text: str) -> tuple[EmailDraft, LinkedInDraft]:
    """Parse the JSON response into (EmailDraft, LinkedInDraft). Raises on malformed input."""
    payload: dict[str, Any] = json.loads(_strip_code_fences(text))
    email_obj = payload["email"]
    body = email_obj["body"]
    email = EmailDraft(
        subject=email_obj["subject"],
        body=body,
        word_count=len(body.split()),
    )
    li_obj = payload["linkedin"]
    li = LinkedInDraft(
        connection_note=li_obj["connection_note"],
        dm=li_obj["dm"],
    )
    return email, li


def draft_outreach(
    contact: Contact,
    *,
    job: ParsedJob | None = None,
    post: ParsedPost | None = None,
    max_attempts: int = 3,
) -> tuple[EmailDraft, LinkedInDraft]:
    """One Sonnet call per contact, with up to `max_attempts` voice-rule regens.

    Returns a tuple: (email draft, LinkedIn draft). Caller decides whether to use
    the LinkedIn surfaces (recipient with no linkedin_url, etc).
    """
    if job is None and post is None:
        raise ValueError("draft_outreach requires either job (JD mode) or post (post mode)")

    cfg = load_config()
    voice_cfg = load_voice_config(cfg.profile_dir)
    failures: list[str] | None = None
    last_failures: list[str] = []
    for attempt in range(1, max_attempts + 1):
        blocks = _build_prompt_blocks(
            voice_config=voice_cfg,
            contact=contact,
            job=job,
            post=post,
            prior_failures=failures,
        )
        raw = call_claude_cached(blocks, cfg.draft_model, temperature=0.7)
        try:
            email, li = parse_outreach_response(raw)
        except Exception as exc:
            last_failures = [f"output was not valid JSON: {exc}"]
            failures = last_failures
            log.warning("draft_outreach attempt %d: parse failure: %s", attempt, exc)
            continue
        all_failures: list[str] = []
        e_check = check_email(email.subject, email.body, config=voice_cfg)
        if not e_check.ok:
            all_failures.extend(f"email: {f}" for f in e_check.failures)
        c_check = check_li_connect(li.connection_note, config=voice_cfg)
        if not c_check.ok:
            all_failures.extend(f"connection_note: {f}" for f in c_check.failures)
        d_check = check_li_dm(li.dm, config=voice_cfg)
        if not d_check.ok:
            all_failures.extend(f"dm: {f}" for f in d_check.failures)
        if not all_failures:
            log.info(
                "draft_outreach accepted on attempt %d (subject=%r, words=%d, connect=%dch, dm=%dch)",
                attempt, email.subject, email.word_count,
                len(li.connection_note), len(li.dm),
            )
            return email, li
        last_failures = all_failures
        failures = all_failures
        log.warning("draft_outreach attempt %d failed: %s", attempt, all_failures)
    raise DraftError(
        f"could not produce outreach passing voice rules in {max_attempts} attempts; last failures: {last_failures}"
    )
