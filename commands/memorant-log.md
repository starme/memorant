---
description: Record a bug or snippet to 书童 · Memorant
argument-hint: <bug | snippet> <short description>
allowed-tools: vault_search, vault_create_entry, vault_get_recent
---

Record development experience in Memorant. Type and description: **$ARGUMENTS**

Follow Flow B of the `memorant` skill:
1. Deduplicate with the legacy-compatible `vault_search` tool.
2. Collect every required field without guessing.
3. After user confirmation, call `vault_create_entry` once. If it returns `VALIDATION_ERROR`, request the missing field and retry.
4. Confirm the created path.

Apply the skill's bug/snippet quality threshold and skip noise.
