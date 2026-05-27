# Phase 2 — finding contacts in Claude.ai chat

The CLI handles Phase 1 (parse + state) and Phase 3 (draft + stage). Phase 2 — finding 3-4 contacts to reach out to — runs in a Claude.ai chat session because Clay has no public REST API.

## Prereqs

- Clay account at clay.com (free tier: 100 credits/mo)
- Claude.ai account with Pro plan
- Clay added as an MCP connector in Claude.ai (Settings → Connectors)

## When to do it

After running `apply run <job_url>`, the CLI prints something like:

```
State saved: state/runs/20260527T123456-exampleco-growth.json
Run id:      20260527T123456-exampleco-growth

Next step (Phase 2 — runs in chat, not this CLI):
  Ask Claude in chat: 'find contacts for run 20260527T123456-exampleco-growth'
```

That's your cue to open Claude.ai (or Claude Code) and ask exactly that.

## What Claude does

Claude opens your local state file (the JSON in `state/runs/`), reads `parsed_job` (or `parsed_post`), and uses the Clay MCP connector to:

1. **Search for the company in Clay** by name + domain.
2. **Find 3-4 candidate contacts** at that company matching the inferred titles. For a hiring manager search, the inferred titles are typically:
   - The exact role (e.g., "Head of Growth")
   - One step up (e.g., "VP Marketing")
   - For sub-50-person companies: founders / CEO are usually the hiring manager — widen to include Founder, Co-Founder, CEO.
3. **Enrich each contact's email** using Clay's work-email-only enrichment (1 credit per contact).
4. **Write the contacts back into the state JSON**, flipping `status` from `awaiting_contacts` to `ready_to_draft`.

## Rules Claude follows

- **Only use the email enrichment.** Do not request thought-leadership / recent activity / company news enrichments — they cost ~10 credits each and the drafter doesn't consume them anyway.
- **Do not add** `manual_notes`, `personalization_hook`, or `recent_li_activity` fields to contacts. The drafter is artifact-driven; those fields were intentionally killed.
- **Preserve** `run_id`, `parsed_job`, `parsed_post`, `inferred_titles`, `created_at`. Only mutate `contacts` and `status`.
- **role_priority**: 1 for the inferred hiring manager (the exact role title), 2-3 for adjacent roles.
- **source**: `"clay_search"` for contacts found by company search; `"post_author"` for the author of the LinkedIn post (when in post-mode).

## After Phase 2

Run `apply run --resume <run_id>` from the CLI. The drafter generates an email + LinkedIn surfaces for each contact, stages Gmail drafts, and writes a row to your Google Sheet.

## Troubleshooting

**Claude says it can't find the state file** — Make sure you're in the same machine + repo where you ran the CLI. The state file is local to your filesystem; Claude.ai web won't see it. Use Claude Code (the CLI version) so it has filesystem access.

**Clay returns no contacts** — Some smaller companies don't have anyone in Clay's database. Ask Claude to try the founder/CEO route, or use Apollo as a fallback (manual — not in v1).

**Out of Clay credits** — Free tier is 100/mo. Each enriched contact = 1 credit. If you run dry mid-month, you can either upgrade Clay's plan or skip Phase 2 for that run and paste contacts into the state file manually.
