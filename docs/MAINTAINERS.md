# Maintainer notes

Notes for whoever maintains this public template.

## What this repo is

A scrubbed, generic copy of a private working repo. Friends "Use this template" on GitHub to get their own copy.

## Hard rules

1. **No personal data in commits.** Run `python scripts/pre_publish_scan.py` before every push (also runs as a GitHub Action). The built-in scan checks for common secret shapes, real email addresses, phone numbers, absolute filesystem paths, and forbidden runtime files.
   - For your own names, handles, schools, employers, domains, and other private markers, add one marker per line to `.privacy-denylist.txt`.
   - `.privacy-denylist.txt` is gitignored so the public repo never publishes the exact private terms it is trying to block.
2. **`Profile/`, `state/runs/`, `credentials/`, `.env`, `.claude-memory/` are gitignored.** Never `git add` files from these directories.
3. **First commit per branch should run the scan locally.** Easiest: install the pre-push hook.

## Pre-push hook

```bash
cat > .git/hooks/pre-push <<'EOF'
#!/bin/sh
uv run python scripts/pre_publish_scan.py || exit 1
EOF
chmod +x .git/hooks/pre-push
```

## Syncing code-only changes from your private repo

When you fix a bug or add a feature in your private working repo, push the *code* change here without dragging Profile/ or state/runs/ along.

The simplest workflow:

```bash
# In your private working repo, generate a patch of the relevant files only
cd /path/to/private/gtm-outreach-agent
git diff <last-sync-sha>..HEAD -- src/ tests/ scripts/ pyproject.toml > /tmp/sync.patch

# Apply to the public template
cd /path/to/gtm-outreach-agent-public
git apply /tmp/sync.patch
# Resolve any conflicts, then:
uv run pytest                          # confirm tests pass
uv run python scripts/pre_publish_scan.py   # confirm no personal data leaked
git commit -am "sync: <description>"
git push
```

## Step 7 → v2 plan

When the send/reply/follow-up loop ships in the private repo:

1. Confirm `src/lib/followups.py`, `src/lib/reply_drafts.py`, and the modified `src/loop.py` are all stable and tested in the private repo.
2. Scrub the Step 7 spec doc (`docs/superpowers/specs/*step7*`) for personal data.
3. Sync to the public template using the workflow above.
4. Update [README.md](../README.md) to drop the "Step 7 deferred to v2" note.
5. Update `apply init` to walk through the new `launchd` setup step.

## Versioning

No versioning yet. If a friend's setup breaks because of a template change, they should just `git pull` their template clone. We're not maintaining backwards compat in v1.

## Issue management

Up to the maintainer. v1 doesn't ship issue templates. If you start getting issues, add one.
