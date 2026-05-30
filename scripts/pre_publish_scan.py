"""Privacy gate. Scans the repo for personal data + forbidden files.

Runs as a pre-push git hook and as a GitHub Action. Exits non-zero if ANY
forbidden pattern matches or any excluded path is staged.

This script is intentionally aggressive: a false positive is cheaper than
leaking personal data into a public repo. Default-deny is the policy.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DENYLIST_PATH = REPO_ROOT / ".privacy-denylist.txt"


# Generic patterns that must NEVER appear in committed files.
# Keep this list generic. Put private names, handles, schools, employers,
# domains, and project-specific markers in .privacy-denylist.txt instead.
FORBIDDEN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/Users/[A-Za-z0-9._-]+/"), "absolute macOS home path"),
    (re.compile(r"/home/[A-Za-z0-9._-]+/"), "absolute Linux home path"),
    (re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9._-]+\\"), "absolute Windows home path"),
    # Phone-like patterns: require at least one separator (paren or dash/dot/space)
    # to avoid false positives on LinkedIn activity IDs and other 10-digit numbers.
    (
        re.compile(r"(\(\d{3}\)\s?\d{3}[-.\s]\d{4})|(\b\d{3}[-.]\d{3}[-.]\d{4}\b)"),
        "phone-number-like pattern",
    ),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"), "Anthropic API key-like string"),
    (re.compile(r"sk-proj-[A-Za-z0-9_-]{10,}"), "OpenAI project key-like string"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b"), "API key-like string"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained token-like string"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"), "GitHub token-like string"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"), "Slack token-like string"),
    (re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9/_-]{20,}"), "Slack webhook URL"),
    (re.compile(r"AIza[0-9A-Za-z_-]{25,}"), "Google API key-like string"),
    (re.compile(r"\bA[SK]IA[0-9A-Z]{16}\b"), "AWS access key-like string"),
    (re.compile(r"ya29\.[A-Za-z0-9_-]{20,}"), "Google OAuth access token-like string"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key block"),
]

EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,}|[A-Z0-9.-]+\.example)\b", re.IGNORECASE)
# anthropic.com is allowed solely for the "Co-Authored-By: ... <noreply@anthropic.com>"
# trailer that appears verbatim in committed plan/spec docs — it is the AI co-author
# address, not personal data.
ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "example.edu", "example.ai", "acme.example", "anthropic.com"}


# File patterns that should NEVER be committed. These are checked at the path level.
FORBIDDEN_PATHS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^Profile/(?!\.gitkeep).*"), "personal Profile/ files (only Profile.example/ allowed)"),
    (re.compile(r"^state/runs/.+\.json$"), "real run state JSON (contains contacts)"),
    (re.compile(r"^credentials/.+"), "OAuth secrets"),
    (re.compile(r"^\.env$"), ".env file (use .env.example as the template)"),
    (re.compile(r"^\.privacy-denylist\.txt$"), "local privacy denylist"),
    (re.compile(r"^\.claude-memory/.+"), "per-machine memory snapshots"),
    (re.compile(r"^logs/.+"), "runtime logs"),
]


# File extensions to scan. Skip binaries, lockfiles, and git internals.
SCAN_EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".toml", ".html", ".txt", ".template", ".sh", ".env.example", ".gitignore"}
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".mypy_cache"}


def _tracked_files() -> list[Path]:
    """Return committed/staged paths, falling back to all files outside git."""
    try:
        output = subprocess.check_output(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return [path for path in REPO_ROOT.rglob("*") if path.is_file()]

    files: list[Path] = []
    for raw in output.split(b"\0"):
        if not raw:
            continue
        files.append(REPO_ROOT / raw.decode("utf-8"))
    return files


def _load_local_denylist() -> list[tuple[re.Pattern[str], str]]:
    """Load private markers without committing those markers to the repo."""
    if not LOCAL_DENYLIST_PATH.exists():
        return []

    patterns: list[tuple[re.Pattern[str], str]] = []
    for lineno, raw_line in enumerate(LOCAL_DENYLIST_PATH.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if line.startswith("regex:"):
                pattern = re.compile(line.removeprefix("regex:").strip(), re.IGNORECASE)
            else:
                pattern = re.compile(re.escape(line), re.IGNORECASE)
        except re.error as exc:
            raise SystemExit(f"{LOCAL_DENYLIST_PATH.name}:{lineno}: invalid regex: {exc}") from exc
        patterns.append((pattern, f"local privacy denylist entry on line {lineno}"))
    return patterns


def _line_views(line: str) -> list[str]:
    """Scan raw lines plus string-literal views where escaped separators are spaces."""
    normalized = line.replace(r"\n", " ").replace(r"\r", " ").replace(r"\t", " ")
    if normalized == line:
        return [line]
    return [line, normalized]


def _line_has(pattern: re.Pattern[str], line: str) -> bool:
    return any(pattern.search(view) for view in _line_views(line))


def _safe_violation(rel: str, lineno: int, label: str) -> str:
    # Do not echo secrets or private denylist matches into CI logs.
    return f"  {rel}:{lineno} — {label}"


def _email_domain(email: str) -> str:
    return email.rsplit("@", 1)[1].lower()


def _should_scan(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if path.is_dir():
        return False
    # special-case extensionless interesting files
    if path.name in {"LICENSE", "install.sh", ".env.example", ".gitignore"}:
        return True
    if path.suffix.lower() in SCAN_EXTENSIONS:
        return True
    return False


def scan_content() -> list[str]:
    """Walk the repo, return a list of human-readable violations for forbidden patterns."""
    violations: list[str] = []
    patterns = FORBIDDEN_PATTERNS + _load_local_denylist()
    for path in _tracked_files():
        if not _should_scan(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()

        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern, label in patterns:
                if _line_has(pattern, line):
                    violations.append(_safe_violation(rel, lineno, label))

            for view in _line_views(line):
                for match in EMAIL_PATTERN.finditer(view):
                    if _email_domain(match.group(0)) not in ALLOWED_EMAIL_DOMAINS:
                        violations.append(_safe_violation(rel, lineno, "real email address"))

    return violations


def scan_paths() -> list[str]:
    """Return violations for forbidden file paths."""
    violations: list[str] = []
    for path in _tracked_files():
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for pattern, label in FORBIDDEN_PATHS:
            if pattern.match(rel):
                violations.append(f"  {rel} — {label}")
                break
    return violations


def main() -> int:
    print("pre_publish_scan: checking for personal data + forbidden files\n")
    content_violations = scan_content()
    path_violations = scan_paths()
    total = len(content_violations) + len(path_violations)

    if path_violations:
        print(f"[FAIL] {len(path_violations)} forbidden file path(s):")
        for v in path_violations:
            print(v)
        print()

    if content_violations:
        print(f"[FAIL] {len(content_violations)} content match(es):")
        for v in content_violations:
            print(v)
        print()

    if total == 0:
        print("[OK] scan clean — no personal data or forbidden files detected.")
        return 0

    print(f"\n[FAIL] {total} total violation(s). Fix above before pushing.")
    print("If a match is a false positive, allowlist it in scripts/pre_publish_scan.py.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
