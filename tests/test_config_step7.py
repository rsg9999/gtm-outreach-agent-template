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
