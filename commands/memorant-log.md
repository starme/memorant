---
description: Record a bug or snippet to 书童 · Memorant
argument-hint: <bug | snippet> <short description>
allowed-tools: mcp__plugin_memorant_memorant__vault_search, mcp__plugin_memorant_memorant__vault_create_entry, mcp__plugin_memorant_memorant__vault_get_recent
---

Record development experience in Memorant. Type and description: **$ARGUMENTS**

Follow Flow B of the `memorant` skill:
1. Deduplicate with `mcp__plugin_memorant_memorant__vault_search`.
2. Collect every required field without guessing.
3. After user confirmation, call `mcp__plugin_memorant_memorant__vault_create_entry` once. If it returns `VALIDATION_ERROR`, request the missing field and retry.
4. Use `mcp__plugin_memorant_memorant__vault_get_recent` when recent context is needed.
5. Confirm the created path.

Apply the skill's bug/snippet quality threshold and skip noise.
