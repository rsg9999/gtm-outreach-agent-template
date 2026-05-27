"""Title inference for the JD flow.

`infer_titles(job)` calls Haiku to expand a role title into 4-6 likely hiring-manager
or adjacent-leader titles. The result is then passed to clay_lookup.find_company_contacts
to narrow the Clay people-search at the company's domain.

Lives separately from clay_lookup because title inference is pure Anthropic + JD text
(no Clay credits) and is mockable independently from the Clay path.
"""
from __future__ import annotations

import json
import logging

from src.lib.config import load_config
from src.lib.models import ParsedJob
from src.lib.parse_job import _strip_code_fences, call_claude

log = logging.getLogger(__name__)


def _build_titles_prompt(job: ParsedJob) -> str:
    return (
        "You are a B2B GTM expert. Given the role below, list 4-6 SHORT job titles that are "
        "likely candidates for the HIRING MANAGER or ADJACENT SENIOR LEADER for this role at this company. "
        "Output ONLY a JSON array of strings (no prose, no fences). Titles should be the kind that would "
        "appear on LinkedIn. Avoid duplicates. Avoid generic titles like 'Manager' alone.\n\n"
        f"COMPANY: {job.company_name}\n"
        f"ROLE: {job.role_title}\n"
        f"JD (truncated): {job.jd_body[:1200]}\n"
    )


def _dedupe_case_insensitive(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        if not isinstance(raw, str):
            continue
        s = raw.strip()
        key = s.lower()
        if not s or key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def infer_titles(job: ParsedJob) -> list[str]:
    """Return up to 6 deduped, likely hiring-manager / adjacent-leader titles for `job`."""
    cfg = load_config()
    raw = call_claude(_build_titles_prompt(job), cfg.parse_model)
    parsed = json.loads(_strip_code_fences(raw))
    if not isinstance(parsed, list):
        raise ValueError(f"infer_titles expected a JSON array, got {type(parsed).__name__}")
    titles = _dedupe_case_insensitive(parsed)
    return titles[:6]
