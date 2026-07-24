---
description: Search the dev experience vault for past bugs/snippets/ADRs/daily notes
argument-hint: <query>
allowed-tools: mcp__plugin_memorant_memorant__vault_search, mcp__plugin_memorant_memorant__vault_get_recent
---

Search the vault for past dev experience relevant to: **$ARGUMENTS**

Procedure:
1. Call `mcp__plugin_memorant_memorant__vault_search` with the query. If the query looks like an error message, use the literal error text + stack name. If it's a concept (e.g. "cache strategy"), filter `dirs=["arch"]`.
2. Also call `mcp__plugin_memorant_memorant__vault_get_recent(dir="bugs", limit=5)` to surface recent bugs that might relate.
3. Summarize what you found: which past entries apply, and what 避坑/Decision they carry. Quote the file + line so the user can open it in Obsidian.
4. If nothing matches, say so plainly — don't fabricate matches.

This is the manual entry point for Flow A of the vault skill.
