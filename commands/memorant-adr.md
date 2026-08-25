---
description: Write an Architecture Decision Record as a Memorant memory
argument-hint: <short description of the decision>
allowed-tools: mcp__plugin_memorant_memorant__memorant_append_event, mcp__plugin_memorant_memorant__memorant_write_memory
---

Write an ADR in Memorant for: **$ARGUMENTS**

Follow Flow E of the `memorant` skill:
1. Call `mcp__plugin_memorant_memorant__memorant_append_event` with `event_type=decision.adopt`.
2. Call `mcp__plugin_memorant_memorant__memorant_write_memory` with `kind=decision`.

Apply the skill's ADR quality threshold; implementation details belong in snippets.
