---
description: Record a bug or snippet to the vault (run after solving something non-trivial)
argument-hint: <bug | snippet> <short description>
allowed-tools: vault_search, vault_create_entry, vault_get_recent
---

Record a dev experience to the vault. Type and description: **$ARGUMENTS**

First argument is `bug` or `snippet`. The rest is a short description of what was solved.

Procedure (Flow B of the vault skill):
1. **Dedup**: call `vault_search` with keywords from the description. If a matching entry exists, tell the user and offer to update it instead of duplicating.
2. **Collect required fields** by asking the user (don't guess):
   - bug: `stack[]`, `version{}` (node/framework versions), `status` (resolved/workaround/open), the error text (现象), root cause one-liner (根因), the misjudgment chain (排查过程), the fix diff (修复), 避坑 points, `related` wikilinks.
   - snippet: `stack[]`, `where` (use scenario), `dont[]` (禁忌), `version{}`, the code, 使用场景, 禁忌, 踩坑记录.
3. Once all required fields are gathered, call `vault_create_entry` once with everything. The server validates — if it returns `VALIDATION_ERROR`, ask the user for the missing field and retry.
4. Confirm the created path to the user.

Threshold reminder: only record if it meets the skill's bug/snippet threshold (≥5 min + ≥2 wrong paths for bugs; reusable + scenario-specific + pitfall-earned for snippets). If it doesn't meet the bar, say so and skip.
