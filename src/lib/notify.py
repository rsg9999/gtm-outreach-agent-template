"""Slack incoming-webhook notifications. No-ops cleanly when SLACK_WEBHOOK_URL is unset."""
from __future__ import annotations

import json
import logging

import httpx

from src.lib.config import load_config

log = logging.getLogger(__name__)


def slack(text: str) -> None:
    """Post a plain-text message to the configured Slack webhook. No-op if unset."""
    cfg = load_config()
    if not cfg.slack_webhook_url:
        log.debug("SLACK_WEBHOOK_URL not set; skipping notification: %s", text)
        return
    try:
        resp = httpx.post(
            cfg.slack_webhook_url,
            data=json.dumps({"text": text}),
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("Slack notification failed: %s", exc)
