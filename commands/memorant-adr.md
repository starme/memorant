---
description: Write an Architecture Decision Record to 书童 · Memorant
argument-hint: <short description of the decision>
allowed-tools: vault_search, vault_create_entry, vault_update_frontmatter, vault_get_recent
---

Write an ADR in Memorant for: **$ARGUMENTS**

Follow Flow B of the `memorant` skill:
1. Deduplicate with `vault_search(query=<topic>, dirs=["arch"])`.
2. Gather Context, at least two Options, Decision, Consequences, and all required frontmatter without guessing.
3. If this supersedes an ADR, set `supersedes` and update the old entry's status with `vault_update_frontmatter`.
4. Call the legacy-compatible `vault_create_entry(type="arch", ...)`; do not pass a sequence.
5. Confirm the resulting path and related links.

Apply the skill's ADR quality threshold; implementation details belong in snippets.
