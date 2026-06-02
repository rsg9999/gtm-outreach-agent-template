# src/lib/classify.py
"""Classify the latest inbound message in a thread: genuine / auto_reply / bounce.

Precedence: bounce -> auto_reply -> genuine. A bounce that also carries auto-reply
headers is still a bounce.
"""
from __future__ import annotations

import re
from email.utils import parseaddr

from src.lib.models import InboundMessage

_BOUNCE_LOCALPARTS = {"mailer-daemon", "postmaster"}
_BOUNCE_SUBJECTS = re.compile(
    r"delivery status notification|undelivered mail returned to sender|mail delivery failed",
    re.IGNORECASE,
)
_AUTO_SUBJECTS = re.compile(
    r"out of office|automatic reply|auto-?reply|on leave|on vacation|away from(?: the)? office",
    re.IGNORECASE,
)
_AUTO_PRECEDENCE = {"bulk", "auto_reply"}


def is_bounce(msg: InboundMessage) -> bool:
    localpart = parseaddr(msg.sender)[1].split("@", 1)[0].lower()
    if localpart in _BOUNCE_LOCALPARTS:
        return True
    if "report-type=delivery-status" in msg.headers.get("content-type", "").lower():
        return True
    return bool(_BOUNCE_SUBJECTS.search(msg.subject))


def is_auto_reply(msg: InboundMessage) -> bool:
    auto_submitted = msg.headers.get("auto-submitted", "").lower()
    if auto_submitted and auto_submitted != "no":
        return True
    if msg.headers.get("precedence", "").lower() in _AUTO_PRECEDENCE:
        return True
    if any(h in msg.headers for h in ("x-autoreply", "x-autorespond", "x-auto-response-suppress")):
        return True
    return bool(_AUTO_SUBJECTS.search(msg.subject))


def classify_inbound(msg: InboundMessage) -> str:
    if is_bounce(msg):
        return "bounce"
    if is_auto_reply(msg):
        return "auto_reply"
    return "genuine"
