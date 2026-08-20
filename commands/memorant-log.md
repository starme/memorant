---
description: Record a bug or snippet as a Memorant memory
argument-hint: <bug | snippet> <short description>
allowed-tools: mcp__plugin_memorant_memorant__memorant_append_event, mcp__plugin_memorant_memorant__memorant_list_pending_events, mcp__plugin_memorant_memorant__memorant_write_memory
---

Record development experience in Memorant. Type and description: **$ARGUMENTS**

Follow Flow E of the `memorant` skill:
1. Call `mcp__plugin_memorant_memorant__memorant_append_event` to record the resolved bug or reusable snippet as a journal event.
2. Call `mcp__plugin_memorant_memorant__memorant_list_pending_events` then run the silent ontology gate.
3. Call `mcp__plugin_memorant_memorant__memorant_write_memory` for each gate-passing form.

Apply the skill's bug/snippet quality threshold and skip noise.
