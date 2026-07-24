---
description: Correct, supersede, or confirm successful reuse of a Memorant memory
allowed-tools: mcp__plugin_memorant_memorant__memorant_feedback, mcp__plugin_memorant_memorant__memorant_promote, mcp__plugin_memorant_memorant__memorant_activity
---

Apply async feedback to a memory.

Arguments: `<path-or-id> <adopted|ignored|corrected|contradicted|successful_reuse> [note]`

1. Parse path/id and action from `$ARGUMENTS`.
2. For `corrected` / `contradicted`, ask for a replacement claim if missing, then call `memorant_feedback` with `replacement_claim`.
3. For `successful_reuse`, call `memorant_feedback` (or `memorant_promote`) with the **current** `session_id` and a success outcome — same-session origin must be rejected by the server.
4. Confirm the Activity line was recorded.

$ARGUMENTS
