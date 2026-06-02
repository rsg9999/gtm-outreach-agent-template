import pytest

from src.lib import reply_drafts
from src.lib.reply_drafts import ReplyGenerationError, generate_reply


@pytest.fixture(autouse=True)
def _example_profile(monkeypatch):
    monkeypatch.setenv("PROFILE_DIR", "Profile.example")


def _good_reply_json():
    return ('{"reply": "Tuesday or Thursday afternoon both work my end. Want me to send a '
            'calendar invite, or easier to grab fifteen minutes off a Loom I can record first?"}')


def test_generate_reply_returns_body_on_clean_gate(monkeypatch):
    monkeypatch.setenv("REPLY_USE_LLM", "true")
    monkeypatch.setattr(reply_drafts, "_call_claude_reply", lambda blocks, model: _good_reply_json())
    out = generate_reply(inbound_body="What times work for a call?", first_name="Jane")
    assert "Tuesday" in out


def test_generate_reply_template_when_llm_disabled(monkeypatch):
    monkeypatch.setenv("REPLY_USE_LLM", "false")
    out = generate_reply(inbound_body="thanks!", first_name="Jane")
    assert out.strip() != ""
    assert "Jane" in out


def test_generate_reply_template_after_gate_fails(monkeypatch):
    monkeypatch.setenv("REPLY_USE_LLM", "true")
    # always returns a too-short reply -> fails gate every attempt -> template fallback
    monkeypatch.setattr(reply_drafts, "_call_claude_reply", lambda blocks, model: '{"reply": "ok"}')
    out = generate_reply(inbound_body="hi", first_name="Jane", max_attempts=2)
    assert "Jane" in out  # template, not the rejected "ok"


def test_generate_reply_transient_error_raises(monkeypatch):
    monkeypatch.setenv("REPLY_USE_LLM", "true")

    def _boom(blocks, model):
        raise ReplyGenerationError("429 rate limited")

    monkeypatch.setattr(reply_drafts, "_call_claude_reply", _boom)
    with pytest.raises(ReplyGenerationError):
        generate_reply(inbound_body="hi", first_name="Jane")
