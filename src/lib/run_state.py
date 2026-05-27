"""Run-state JSON files. Shared between Phase 1 (Python CLI), Phase 2 (chat — me using session Clay MCP),
and Phase 3 (Python CLI, --resume).

Schema:
    {
      "run_id": "20260504T120300-exampleco-gtm-engineer",
      "created_at": "2026-05-04T12:03:00",
      "parsed_job": { ParsedJob fields } | null,
      "parsed_post": { ParsedPost fields } | null,
      "inferred_titles": ["Head of GTM", ...],
      "contacts": [ Contact fields, ... ],
      "drafts": [ StagedRow fields, ... ],
      "status": "awaiting_contacts" | "ready_to_draft" | "staged" | "failed",
      "error": str | null
    }

Phase 1 writes the file with status="awaiting_contacts".
Phase 2 (chat) populates contacts + emails via Clay MCP, sets status="ready_to_draft".
Phase 3 reads the file via `apply --resume <run_id>`, drafts, stages, sets status="staged".
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from src.lib.config import REPO_ROOT
from src.lib.models import Contact, ParsedJob, ParsedPost, StagedRow

RunStatus = Literal["awaiting_contacts", "ready_to_draft", "staged", "failed"]


class RunState(BaseModel):
    run_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    parsed_job: ParsedJob | None = None
    parsed_post: ParsedPost | None = None
    inferred_titles: list[str] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    drafts: list[StagedRow] = Field(default_factory=list)
    status: RunStatus = "awaiting_contacts"
    error: str | None = None


def runs_dir() -> Path:
    """Where state/runs/ lives. Tests can override with RUNS_DIR_OVERRIDE env var."""
    override = os.getenv("RUNS_DIR_OVERRIDE")
    base = Path(override) if override else REPO_ROOT / "state" / "runs"
    base.mkdir(parents=True, exist_ok=True)
    return base


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-") or "x"


def new_run_id(job: ParsedJob | None = None, *, label: str | None = None) -> str:
    """Build a unique-per-call run id from timestamp + a slug.

    Pass `job` for the standard "company-role" slug (JD flow). Pass `label` for the
    post-only flow (e.g. the post author's name or the LI post id), since there's no
    parsed job to derive a slug from.
    """
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    millis = int(time.monotonic_ns()) % 1_000_000
    if job is not None:
        slug = f"{_slug(job.company_name)}-{_slug(job.role_title)}"
    elif label:
        slug = _slug(label)
    else:
        slug = "run"
    return f"{ts}{millis:06d}-{slug}"


def _path_for(run_id: str) -> Path:
    return runs_dir() / f"{run_id}.json"


def save_run_state(state: RunState) -> Path:
    """Write the state JSON. Returns the file path."""
    path = _path_for(state.run_id)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_run_state(run_id: str) -> RunState:
    """Read a previously saved state. Raises FileNotFoundError if the run doesn't exist."""
    path = _path_for(run_id)
    if not path.exists():
        raise FileNotFoundError(f"No run state at {path}")
    return RunState.model_validate_json(path.read_text(encoding="utf-8"))
