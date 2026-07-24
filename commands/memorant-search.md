---
description: Search 书童 · Memorant for past bugs/snippets/ADRs/daily notes
argument-hint: <query>
allowed-tools: mcp__plugin_memorant_memorant__vault_search, mcp__plugin_memorant_memorant__vault_get_recent
---

Search Memorant for past development experience relevant to: **$ARGUMENTS**

Procedure:
1. Follow Flow A of the `memorant` skill and call `mcp__plugin_memorant_memorant__vault_search` with the query. If the query looks like an error message, use the literal error text + stack name. If it is a concept such as "cache strategy", filter `dirs=["arch"]`.
2. Also call `mcp__plugin_memorant_memorant__vault_get_recent(dir="bugs", limit=5)` to surface recent bugs that might relate.
3. Summarize what applies and quote the file + line so the user can open it.
4. If nothing matches, say so plainly; do not fabricate matches.
