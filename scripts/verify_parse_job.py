"""Manual Step 2 verification: run Haiku on JD fixture(s) and print the structured output.

Each fixture file starts with two header lines:
    SOURCE_URL: <url>
    SOURCE_SITE: <lever|greenhouse|workday|linkedin|indeed|other>
    ---
    <JD body>

Drop a JD into a text file with this header format and point this script at it
to sanity-check the parser without running the full pipeline.

Usage:
    uv run python scripts/verify_parse_job.py path/to/your_jd.txt
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from src.lib.config import load_config
from src.lib.parse_job import _build_prompt, call_claude, parse_claude_response


def _load_fixture(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    header, _, body = text.partition("---\n")
    meta = {}
    for line in header.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta.get("SOURCE_URL", "https://example.com/x"), meta.get("SOURCE_SITE", "other"), body.strip()


def main(argv: list[str]) -> int:
    cfg = load_config()
    if not cfg.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set in .env. Aborting.", file=sys.stderr)
        return 2

    paths = [Path(p) for p in argv[1:]]
    if not paths:
        print(__doc__)
        return 1

    for path in paths:
        if not path.exists():
            print(f"!! missing fixture: {path}")
            continue

        url, source, body = _load_fixture(path)
        print("=" * 80)
        print(f"FIXTURE: {path.name}")
        print(f"  url    = {url}")
        print(f"  source = {source}")
        print(f"  bytes  = {len(body)}")

        prompt = _build_prompt(body, url)
        raw = call_claude(prompt, cfg.parse_model)
        try:
            job = parse_claude_response(raw, job_url=url, source_site=source)
        except Exception as exc:
            print(f"  !! parse failed: {exc}")
            print("  raw response:")
            print(raw)
            continue

        print("  parsed:")
        print(json.dumps(job.model_dump(mode="json"), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
