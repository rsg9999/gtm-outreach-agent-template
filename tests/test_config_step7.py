"""Step 7 config additions."""
from __future__ import annotations

from src.lib.config import load_config


def test_step7_tab_falls_back_to_sheet_tab(monkeypatch):
    monkeypatch.delenv("STEP7_SHEET_TAB", raising=False)
    monkeypatch.setenv("SHEET_TAB_NAME", "Outreach")
    cfg = load_config()
    assert cfg.step7_sheet_tab == "Outreach"
    assert cfg.enable_followups is True


def test_step7_tab_override_and_followups_off(monkeypatch):
    monkeypatch.setenv("STEP7_SHEET_TAB", "Outreach_Step7_Test")
    monkeypatch.setenv("ENABLE_FOLLOWUPS", "false")
    cfg = load_config()
    assert cfg.step7_sheet_tab == "Outreach_Step7_Test"
    assert cfg.enable_followups is False


def test_reply_flags_default_true_and_defer_days(monkeypatch):
    for var in ("ENABLE_REPLY_TRACKING", "ENABLE_REPLY_DRAFTS", "REPLY_USE_LLM", "OOO_DEFER_DAYS"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.enable_reply_tracking is True
    assert cfg.enable_reply_drafts is True
    assert cfg.reply_use_llm is True
    assert cfg.ooo_defer_days == 5


def test_reply_flags_env_overrides(monkeypatch):
    monkeypatch.setenv("ENABLE_REPLY_TRACKING", "false")
    monkeypatch.setenv("ENABLE_REPLY_DRAFTS", "false")
    monkeypatch.setenv("REPLY_USE_LLM", "0")
    monkeypatch.setenv("OOO_DEFER_DAYS", "3")
    cfg = load_config()
    assert cfg.enable_reply_tracking is False
    assert cfg.enable_reply_drafts is False
    assert cfg.reply_use_llm is False
    assert cfg.ooo_defer_days == 3
