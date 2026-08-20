---
description: Search 书童 · Memorant memories for past experience
argument-hint: <query>
allowed-tools: mcp__plugin_memorant_memorant__memorant_recall
---

Search Memorant memories relevant to: **$ARGUMENTS**

Procedure:
1. Call `mcp__plugin_memorant_memorant__memorant_recall` with the query. If it looks like an error message, use the literal error text + stack name.
2. Summarize what applies and quote the memory so the user can open it.
3. If nothing matches, say so plainly; do not fabricate matches.
