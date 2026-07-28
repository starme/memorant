---
description: Capture a non-dev milestone (PRD/ADR/decision finalized) into 书童 · Memorant
argument-hint: <doc.commit | decision.adopt> <short description>
allowed-tools: mcp__plugin_memorant_memorant__memorant_append_event, mcp__plugin_memorant_memorant__memorant_list_pending_events, mcp__plugin_memorant_memorant__memorant_write_memory
---

Capture a non-dev milestone into Memorant, then proactively distill. Milestone and description: **$ARGUMENTS**

Use this when a PRD / spec / ADR / design decision was just **finalized or adopted** and no Bash hook fired (no `test.success` / `git.commit`). This is the non-dev entry to the same journal→Distill chain — proactively record, do not wait for the user to say「记一下」.

Follow Flow E (non-dev trigger) of the `memorant` skill:
1. `mcp__plugin_memorant_memorant__memorant_append_event` with `event_type` = `doc.commit` (a doc/spec/PRD was finalized) or `decision.adopt` (an ADR/decision was adopted). Pass a one-line `evidence_excerpt` (the decision or doc title + key choice).
2. Immediately call `mcp__plugin_memorant_memorant__memorant_list_pending_events` and run the silent ontology gate.
3. For each gate-passing form, `mcp__plugin_memorant_memorant__memorant_write_memory` (auto-write A/B; no per-item confirmation) with `source_event_ids` from the just-appended event.
4. Skip silently when no migratable form (leave journal only). Never invent evidence.

Apply the skill's ontology gate — record only work-general forms with bounded claims, not episodic doc drafts.
