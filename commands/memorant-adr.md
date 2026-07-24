---
description: Write an Architecture Decision Record to 书童 · Memorant
argument-hint: <short description of the decision>
allowed-tools: mcp__plugin_memorant_memorant__vault_search, mcp__plugin_memorant_memorant__vault_create_entry, mcp__plugin_memorant_memorant__vault_update_frontmatter, mcp__plugin_memorant_memorant__vault_get_recent
---

Write an ADR in Memorant for: **$ARGUMENTS**

Follow Flow B of the `memorant` skill:
1. Deduplicate with `mcp__plugin_memorant_memorant__vault_search(query=<topic>, dirs=["arch"])`.
2. Gather Context, at least two Options, Decision, Consequences, and all required frontmatter without guessing.
3. If this supersedes an ADR, set `supersedes` and update the old entry's status with `mcp__plugin_memorant_memorant__vault_update_frontmatter`.
4. Call `mcp__plugin_memorant_memorant__vault_create_entry(type="arch", ...)`; do not pass a sequence.
5. Use `mcp__plugin_memorant_memorant__vault_get_recent` when recent ADR context is needed.
6. Confirm the resulting path and related links.

Apply the skill's ADR quality threshold; implementation details belong in snippets.
