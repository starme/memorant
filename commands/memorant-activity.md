---
description: Show today's Memorant Memory Activity (writes, promotions, conflicts)
allowed-tools: mcp__plugin_memorant_memorant__memorant_activity, mcp__plugin_memorant_memorant__memorant_recall
---

Show non-blocking Memory Activity for today.

1. Call `memorant_activity` with `include_session_summary=true`.
2. Summarize: new A/B memories, B→A promotions, corrections/conflicts, and `needs_attention` items.
3. Do **not** treat this as an approval gate — Activity is an audit surface only.
4. If the user asks about a specific memory, call `memorant_recall` or read the memory path.

$ARGUMENTS
