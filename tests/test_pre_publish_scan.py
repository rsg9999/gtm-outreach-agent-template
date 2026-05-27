from __future__ import annotations

import scripts.pre_publish_scan as scan


def test_local_denylist_catches_escaped_string_literals(monkeypatch, tmp_path):
    target = tmp_path / "sample.py"
    target.write_text('body = "hello\\n\\nPrivateName"\n', encoding="utf-8")
    denylist = tmp_path / ".privacy-denylist.txt"
    denylist.write_text("PrivateName\n", encoding="utf-8")

    monkeypatch.setattr(scan, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(scan, "LOCAL_DENYLIST_PATH", denylist)
    monkeypatch.setattr(scan, "_tracked_files", lambda: [target])

    violations = scan.scan_content()

    assert violations == ["  sample.py:1 — local privacy denylist entry on line 1"]
    assert "PrivateName" not in violations[0]


def test_scan_paths_checks_tracked_files_only(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=value\n", encoding="utf-8")

    monkeypatch.setattr(scan, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(scan, "_tracked_files", lambda: [])
    assert scan.scan_paths() == []

    monkeypatch.setattr(scan, "_tracked_files", lambda: [env_file])
    assert scan.scan_paths() == ["  .env — .env file (use .env.example as the template)"]
