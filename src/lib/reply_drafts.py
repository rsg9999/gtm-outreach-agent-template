"""Draft a reply in the user's THREAD voice. One Claude call + up to 3 voice-gate regens,
then a deterministic template fallback. Transient API failures raise ReplyGenerationError so
the loop leaves the row untouched and retries next tick. NEVER sends."""
from __future__ import annotations

import json
import logging

from src.lib.config import load_config
from src.lib.parse_job import _strip_code_fences
from src.lib.profile import load_thread_pack
from src.lib.voice_rules import VoiceConfig, check_reply, load_voice_config

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 60.0


class ReplyGenerationError(RuntimeError):
    """Transient failure (429/5xx/timeout). The loop should retry next tick, not fall back."""


def _call_claude_reply(prompt_blocks: list[dict], model: str) -> str:
    """One Anthropic call with a 60s timeout. Raises ReplyGenerationError on 429/5xx/timeout/conn
    (transient); any other (hard) error propagates to the caller, which falls back to a template."""
    from anthropic import (
        Anthropic,
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    cfg = load_config()
    client = Anthropic(api_key=cfg.anthropic_api_key, timeout=_TIMEOUT_SECONDS)
    kwargs: dict = {"model": model, "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt_blocks}]}
    if "opus" not in model.lower():
        kwargs["temperature"] = 0.7
    try:
        msg = client.messages.create(**kwargs)
    except (APITimeoutError, RateLimitError, InternalServerError, APIConnectionError) as exc:
        raise ReplyGenerationError(f"transient API failure drafting reply: {exc}") from exc
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


def _build_blocks(*, voice_config: VoiceConfig, inbound_body: str, first_name: str,
                  prior_failures: list[str] | None) -> list[dict]:
    pack = load_thread_pack().as_prompt_block()
    static = f"""You are the writer described below, replying to someone who ALREADY replied to your cold email. Warmer and shorter than a cold email. Match their energy.

OUTPUT FORMAT
=============
A single valid JSON object, no prose outside it, no code fences:
{{"reply": "<20-100 words. Opens 'Hi {first_name},' or 'Hey {first_name},'. One clear next step. Sign off with your first name only.>"}}

VOICE RULES (enforced after output; regen on failure)
=====================================================
- 20-100 words. No em dashes. No "leverage", "passionate about", "circling back", "just following up".
- One ask, not two. Plain first-name sign-off.

{pack}
"""
    dynamic = f"THEIR MESSAGE (reply to this):\n{inbound_body[:2000]}\n\nNOW WRITE THE REPLY. Output JSON only.\n"
    if prior_failures:
        dynamic += ("\nPRIOR ATTEMPT FAILED THESE CHECKS. Fix every one:\n"
                    + "\n".join(f"  - {f}" for f in prior_failures) + "\n")
    return [
        {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic},
    ]


def _parse_reply(text: str) -> str:
    payload = json.loads(_strip_code_fences(text))
    return payload["reply"]


def _template_reply(first_name: str) -> str:
    cfg = load_config()
    name = (cfg.sender_name or "").split()[0] if cfg.sender_name else "me"
    return (
        f"Hi {first_name},\n\n"
        "Thanks so much for getting back to me, I really appreciate it. "
        "Happy to share more whenever is good for you, or hop on a quick call if that is easier. "
        "Just let me know what works.\n\n"
        f"{name}"
    )


def generate_reply(*, inbound_body: str, first_name: str, max_attempts: int = 3) -> str:
    """Return a reply-draft body. Template if REPLY_USE_LLM=false, gate-fails, or a hard API
    error. Raises ReplyGenerationError on a transient API failure (retry next tick)."""
    cfg = load_config()
    if not cfg.reply_use_llm:
        return _template_reply(first_name)

    voice_cfg = load_voice_config(cfg.profile_dir)
    failures: list[str] | None = None
    last_failures: list[str] = []
    for attempt in range(1, max_attempts + 1):
        blocks = _build_blocks(voice_config=voice_cfg, inbound_body=inbound_body,
                               first_name=first_name, prior_failures=failures)
        try:
            raw = _call_claude_reply(blocks, cfg.draft_model)
        except ReplyGenerationError:
            raise  # transient -> propagate so the loop leaves the row and retries next tick
        except Exception as exc:  # hard, non-transient API error -> template, don't crash
            log.warning("reply draft hard API error attempt %d: %s; using template", attempt, exc)
            return _template_reply(first_name)
        try:
            body = _parse_reply(raw)
        except Exception as exc:
            last_failures = [f"output was not valid JSON: {exc}"]
            failures = last_failures
            continue
        check = check_reply(body, config=voice_cfg)
        if check.ok:
            log.info("reply draft accepted on attempt %d (%d words)", attempt, len(body.split()))
            return body
        last_failures = check.failures
        failures = check.failures
        log.warning("reply draft attempt %d failed gate: %s", attempt, check.failures)
    log.warning("reply draft failed gate in %d attempts; using template. last=%s",
                max_attempts, last_failures)
    return _template_reply(first_name)
