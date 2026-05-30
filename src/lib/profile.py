"""Loads the Profile/ pack into one cached blob for drafting prompts."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.lib.config import load_config

PROFILE_FILES = (
    "resume.md",
    "voice.md",
    "proof_points.md",
    "past_drafts.md",
    "narrative.md",
)
# manual_pitch.md is intentionally NOT loaded; it is reference-only per the handoff doc.


@dataclass(frozen=True)
class ProfilePack:
    resume: str
    voice: str
    proof_points: str
    past_drafts: str
    narrative: str

    def as_prompt_block(self) -> str:
        return (
            "<resume>\n" + self.resume.strip() + "\n</resume>\n\n"
            "<voice>\n" + self.voice.strip() + "\n</voice>\n\n"
            "<proof_points>\n" + self.proof_points.strip() + "\n</proof_points>\n\n"
            "<past_drafts>\n" + self.past_drafts.strip() + "\n</past_drafts>\n\n"
            "<narrative>\n" + self.narrative.strip() + "\n</narrative>"
        )


def _read(profile_dir: Path, name: str) -> str:
    path = profile_dir / name
    if not path.exists():
        raise FileNotFoundError(
            f"Missing profile file: {path}. Expected one of: {', '.join(PROFILE_FILES)} in {profile_dir}."
        )
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_profile() -> ProfilePack:
    cfg = load_config()
    pdir = cfg.profile_dir
    return ProfilePack(
        resume=_read(pdir, "resume.md"),
        voice=_read(pdir, "voice.md"),
        proof_points=_read(pdir, "proof_points.md"),
        past_drafts=_read(pdir, "past_drafts.md"),
        narrative=_read(pdir, "narrative.md"),
    )


def parse_followup_pools(text: str) -> dict[str, list[str]]:
    """Parse a thread_followups.md into {'followup_1': [...], 'followup_2': [...]}.

    '## Email 2' -> followup_1 (first bump), '## Email 3' -> followup_2 (final note).
    Lines starting with '- ' are pool entries.
    """
    section_map = {"email 2": "followup_1", "email 3": "followup_2"}
    pools: dict[str, list[str]] = {"followup_1": [], "followup_2": []}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("##"):
            current = section_map.get(line.lstrip("#").strip().lower())
        elif line.startswith("- ") and current:
            pools[current].append(line[2:].strip())
    return pools


def load_followup_pools() -> dict[str, list[str]]:
    """Load the per-user bump pool from Profile/thread_followups.md."""
    cfg = load_config()
    text = _read(cfg.profile_dir, "thread_followups.md")
    return parse_followup_pools(text)
