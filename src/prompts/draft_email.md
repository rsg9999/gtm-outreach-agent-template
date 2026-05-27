# draft_email prompt (Step 5)

Used with Sonnet @ temperature 0.7.

The full Profile pack (resume, voice, proof_points, past_drafts, narrative) is loaded into the system prompt
via `src/lib/profile.py::ProfilePack.as_prompt_block`.

User-message inputs:
- `{contact}` (name, title, company, personalization_hook, recent_li_activity)
- `{job}` (role_title, company_name, jd_body)
- `{anchor_choice}` (selected past_drafts example by id 1-8)
- `{proof_points_chosen}` (1-2 keys from proof_points buckets)
- `{narrative_chunk}` (optional, max 1)

Required output: JSON object matching `EmailDraft` (subject, body, word_count). Body MUST:
- Be within `voice_config.body_word_min`..`body_word_max` (default 50-110).
- Sign off with `voice_config.signature` on its own line.
- Pass `voice_rules.check_email(subject, body, config=voice_config)`. If it fails, regenerate (max 3 attempts) with the failure list appended to the prompt.
