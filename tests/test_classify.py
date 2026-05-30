# tests/test_classify.py
from src.lib.classify import classify_inbound, is_bounce, is_auto_reply
from src.lib.models import InboundMessage


def _msg(sender="Jane <jane@acme.example>", subject="Re: the role", headers=None, body="hi"):
    base = {"from": sender, "subject": subject}
    base.update(headers or {})
    return InboundMessage(sender=sender, subject=subject, headers=base, body=body, internal_date_ms=1000)


def test_genuine_reply():
    assert classify_inbound(_msg(body="Sounds good, let's talk Tuesday.")) == "genuine"

def test_bounce_by_sender():
    m = _msg(sender="Mail Delivery Subsystem <mailer-daemon@acme.example>", subject="Delivery Status Notification (Failure)")
    assert is_bounce(m) is True
    assert classify_inbound(m) == "bounce"

def test_bounce_by_content_type():
    m = _msg(headers={"content-type": 'multipart/report; report-type=delivery-status; boundary="x"'})
    assert classify_inbound(m) == "bounce"

def test_auto_reply_by_header():
    m = _msg(headers={"auto-submitted": "auto-replied"})
    assert is_auto_reply(m) is True
    assert classify_inbound(m) == "auto_reply"

def test_auto_reply_by_subject():
    m = _msg(subject="Automatic reply: Out of office")
    assert classify_inbound(m) == "auto_reply"

def test_bounce_wins_over_auto_headers():
    # a bounce that also carries auto-ish headers is still a bounce (precedence)
    m = _msg(sender="postmaster@acme.example", subject="Undelivered Mail Returned to Sender",
             headers={"auto-submitted": "auto-replied"})
    assert classify_inbound(m) == "bounce"
