---
description: Write an Architecture Decision Record (ADR) to the vault
argument-hint: <short description of the decision>
allowed-tools: mcp__plugin_memorant_memorant__vault_search, mcp__plugin_memorant_memorant__vault_create_entry, mcp__plugin_memorant_memorant__vault_update_frontmatter, mcp__plugin_memorant_memorant__vault_get_recent
---

Write an ADR for an architectural decision: **$ARGUMENTS**

Procedure (Flow B of the vault skill):

1. **Dedup**: call `mcp__plugin_memorant_memorant__vault_search` with the decision topic (filter `dirs=["arch"]`). If a similar ADR exists, tell the user — maybe this supersedes it instead of being new.
2. **Gather the four required sections** by asking the user:
   - Context: current state, pain point, driver (data/numbers preferred)
   - Options: ≥2 alternatives, each with 做法/优势/劣势/结论 (include the弃 reason)
   - Decision: which option, effective date, owner
   - Consequences: 正向 + 负向/待跟
   - Plus frontmatter: `status` (proposed/active/deprecated/superseded), `decision_by[]`, `project` (filename needs it), `related` wikilinks.
3. If this ADR **supersedes** an old one: set frontmatter `supersedes: "adr-NNN"`, then after creating, call `mcp__plugin_memorant_memorant__vault_update_frontmatter(path="arch/<old-adr-file>.md", key="status", value="superseded")` to flip the old one.
4. Call `mcp__plugin_memorant_memorant__vault_create_entry(type="arch", ...)`. **Do not pass a sequence** — the server auto-assigns from `arch/.sequence`. Confirm the resulting `adr-NNN` number to the user.
5. Use `mcp__plugin_memorant_memorant__vault_get_recent` when recent ADR context is needed.
6. Link `related` to落地 snippets and踩坑 bugs if they exist.

Threshold reminder: only write an ADR if it meets the skill's threshold (tech selection / ≥2 modules / ≥1 week rework / supersedes old / "why this choice" gets asked). Pure implementation details belong in snippets, not ADRs.
