# infer_titles prompt (Step 3)

Used with Haiku to expand a role title into a list of likely hiring-manager and adjacent-leader titles.

Example: "GTM Engineer" -> ["Head of GTM", "VP Growth", "Director of RevOps", "GTM Lead"]

Inputs:
- `{role_title}`
- `{company_name}`
- `{jd_body}` (truncated)

Required output: JSON list of strings, max 6.
