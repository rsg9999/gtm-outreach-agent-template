"""Scaffold Profile/ from a resume + 5 voice questions via one Sonnet call.

Called from `apply init`. The friend pastes their resume + answers a few
short questions; we feed it all to Sonnet with a meta-prompt that produces
draft resume.md, voice.md, narrative.md, proof_points.md, and past_drafts.md.
We also write voice_config.yaml with their name as the signature.

Output files land in Profile/ (the friend's real, gitignored profile pack).
The friend is told to review and edit before drafting their first outreach.
"""
from __future__ import annotations

import json
from pathlib import Path

import click


SCAFFOLD_PROMPT = """You are helping {name} scaffold a personal-voice profile pack for a job-outreach automation tool.

The tool drafts cold emails + LinkedIn DMs that read like {name} wrote them on their phone in 90 seconds, anchored to specific proof points and a recognizable voice. To do that it needs five markdown files:

1. resume.md — a clean markdown rendering of their resume content
2. voice.md — non-negotiable voice rules ("Short. 50-110 words. No em dashes. Sign off with the first name only. Open with 'Hi <FirstName>,'. Avoid [list]. Use [list of authentic phrasings they use]")
3. proof_points.md — 5-8 quantified accomplishments from their career, each one line, each with a specific number/metric
4. past_drafts.md — 3-5 short example emails that capture the voice; written in their style
5. narrative.md — 3-4 short origin chunks (one paragraph each) that explain who they are and why they do what they do, used at most once per email

Their inputs are below. Generate ALL FIVE markdown files. Return a single JSON object:

{{
  "resume_md": "<full markdown>",
  "voice_md": "<full markdown>",
  "proof_points_md": "<full markdown>",
  "past_drafts_md": "<full markdown>",
  "narrative_md": "<full markdown>"
}}

No prose outside the JSON. No code fences.

---

NAME: {name}
ONE-LINE PITCH: {pitch}
TARGET ROLES/INDUSTRY: {target}
2-3 RECENT WINS (with numbers): {wins}
VOICE DESCRIPTION (e.g. "concise, direct, slightly self-deprecating"): {voice_style}
1-2 STORIES WORTH TELLING (short summaries, will become narrative chunks): {stories}

RESUME (paste, can be unstructured):
{resume}
"""


def _read_multiline(prompt_text: str, *, end_marker: str = "Ctrl-D (or 'END')") -> str:
    click.echo(f"\n{prompt_text}")
    click.echo(f"  (end input with {end_marker} on its own line)")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _prompt_short(prompt_text: str) -> str:
    while True:
        value = click.prompt(prompt_text, type=str, default="", show_default=False).strip()
        if value:
            return value
        click.echo("  (required — enter something)")


def gather_inputs() -> dict[str, str]:
    """Collect the 5 questions + resume. Returns a dict ready for the prompt template."""
    click.echo("Profile scaffold — answer 5 short questions, then paste your resume.\n")
    name = _prompt_short("Your name (used as the email sign-off)")
    pitch = _prompt_short("Your one-line pitch (e.g. 'PM-turned-founder applying to seed-stage AI companies')")
    target = _prompt_short("Target roles / industries (e.g. 'Head of Growth at AI/dev-tools companies, 20-200 employees')")
    wins = _prompt_short(
        "2-3 recent wins, with numbers (e.g. 'shipped onboarding v2, activation 41%->58%; built new vertical to $4M pipeline')"
    )
    voice_style = _prompt_short(
        "Voice description in 5-10 words (e.g. 'concise, direct, builder energy, light self-deprecation')"
    )
    stories = _prompt_short(
        "1-2 short story summaries that explain why you do what you do (e.g. 'scaled an agency to $800K then sold it because...')"
    )
    resume = _read_multiline("Paste your resume now (markdown or plain text)")
    if not resume:
        raise click.ClickException("Empty resume; profile scaffold needs your resume content.")
    return {
        "name": name,
        "pitch": pitch,
        "target": target,
        "wins": wins,
        "voice_style": voice_style,
        "stories": stories,
        "resume": resume,
    }


def _strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
    if s.endswith("```"):
        s = s.rsplit("\n", 1)[0]
    return s.strip()


def call_sonnet(inputs: dict[str, str]) -> dict[str, str]:
    """One Sonnet call to generate all five Profile/ markdown files."""
    from anthropic import Anthropic
    from src.lib.config import load_config

    cfg = load_config()
    client = Anthropic(api_key=cfg.anthropic_api_key)
    prompt = SCAFFOLD_PROMPT.format(**inputs)
    msg = client.messages.create(
        model=cfg.draft_model,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    payload = json.loads(_strip_code_fences(text))
    required_keys = {"resume_md", "voice_md", "proof_points_md", "past_drafts_md", "narrative_md"}
    missing = required_keys - set(payload.keys())
    if missing:
        raise click.ClickException(f"Sonnet response is missing keys: {missing}")
    return payload


def write_profile(profile_dir: Path, generated: dict[str, str], signature: str) -> None:
    """Write the generated markdown files + a starter voice_config.yaml."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    file_map = {
        "resume.md": generated["resume_md"],
        "voice.md": generated["voice_md"],
        "proof_points.md": generated["proof_points_md"],
        "past_drafts.md": generated["past_drafts_md"],
        "narrative.md": generated["narrative_md"],
    }
    for filename, content in file_map.items():
        (profile_dir / filename).write_text(content, encoding="utf-8")

    voice_config_yaml = f"""# Per-user voice rules. Edit any of these to tighten or loosen the drafter.
# Universal AI tells (em dashes, "leverage", "I am writing to", etc.) are baked
# into voice_rules.py and always enforced; the list below ADDS to them.

signature: "{signature}"

# Phrases that should never appear in your drafts. Add things you catch yourself
# saying that feel like an AI tell, or banal corporate-speak you want to avoid.
banned_phrases:
  - "if you have a minute"
  - "no worries if not"
  - "let me know your availability"
  - "I think that"
  - "maps well"
  - "want to reach out"
  - "Quick context"
  - "Quick note"

# Regex patterns for subject-line anti-patterns (case-insensitive).
banned_subject_patterns: []

# Email body word-count window.
body_word_min: 50
body_word_max: 110

# LinkedIn surface caps.
li_connect_max_chars: 300
li_dm_max_chars: 500
li_inmail_subject_max: 200
li_inmail_body_max: 1500
"""
    (profile_dir / "voice_config.yaml").write_text(voice_config_yaml, encoding="utf-8")


def run_scaffold(profile_dir: Path) -> str:
    """End-to-end: gather inputs -> call Sonnet -> write files. Returns the signature used."""
    inputs = gather_inputs()
    click.echo("\nGenerating your Profile/ pack with Sonnet — this takes 15-30 seconds...\n")
    generated = call_sonnet(inputs)
    signature = inputs["name"].split()[0]  # first name only
    write_profile(profile_dir, generated, signature)
    click.echo(f"  ✓ wrote Profile/ pack to {profile_dir}")
    click.echo(f"  ✓ wrote Profile/voice_config.yaml (signature={signature!r})")
    return signature
