---
name: memorant
description: Use when developing — search 书童 · Memorant for past bugs/snippets/ADRs before debugging or making tech choices, prompt to record non-trivial bugs/reusable snippets/architectural decisions after solving them, generate daily logs at session end, and promote daily "待升" leads into permanent bugs/snippets/ADR entries. Triggers on debugging, error messages, tech selection, "记到 memorant", "memorant", "vault", "经验库", daily summary, ADR.
---

# 书童 · Memorant Orchestrator

Orchestrates Memorant MCP tools into seven flows: **Recall**, **Capture** (legacy vault), **Promote** (daily leads), **Commit**, **Distill**, **Trust Route**, and **Feedback**. Hooks capture deterministic journal events; you (host Claude) distill meaning into A/B Memory Envelopes; MCP validates, ranks, and promotes.

**Two write tracks (do not conflate):**
- **Memorant memories** (`memorant_write_memory`, Flows E/F): A/B **auto-write** — no per-item confirmation.
- **Legacy vault** (`vault_*` / bugs/snippets/daily/arch, Flows B/C/D): still **preview + ask** before writing Obsidian-facing notes. `vault_*` responses are marked deprecated.

## Memorant layout (do not deviate)

| Type | Path | Filename | Notes |
|---|---|---|---|
| journal | `journal/YYYY/MM/DD/` | `<ts>-<event_id>.md` | immutable events |
| memory | `memories/` | `<memory_id>.md` | A/B shared directory |
| activity | `activity/` | `YYYY-MM-DD.md` | append-only audit |
| bug | `bugs/` | `{stack}-{短描述}-{YYYYMMDD}.md` | legacy verified |
| snippet | `snippets/` | `{场景}-{技术栈}.md` | legacy verified |
| daily | `daily/` | `{YYYY-MM-DD}.md` | legacy inbox |
| arch | `arch/` | `adr-{序号}-{项目}-{短描述}.md` | legacy verified |

`.memorant/` may hold rebuildable cursors/locks only — never the business source of truth.

---

## Flow A — Search before acting (passive)

**Trigger**: user hits a bug / error, debugs, or makes a tech choice (cache / DB / framework / deployment).

1. Before diving in, call `vault_search` with the error keyword + stack, or the concept word.
   - For bugs: use the **exact error string** + stack name as `query`.
   - For tech choices: use the **concept** (`cache strategy`, `queue split`) + filter `dirs=["arch"]`.
   - Narrow with `dirs` (e.g. `["bugs"]`) and `project` when you know the context.
2. Read matches. If a past bug/ADR applies, follow its 避坑 / Decision — don't repeat the wrong path.
3. If nothing matches, proceed normally — but keep the vault in mind for Flow B.

**Don't** call search for trivial things (typo, port-in-use) — that's noise.

---

## Flow B — Record after solving (active prompt)

**Trigger**: after solving something non-trivial, or at session end. Evaluate the threshold; if met, **ask the user before writing**.

### Bug threshold (record if ANY)
- Localization took ≥ 5 minutes **and** ≥ 2 wrong paths tried
- Error message's first Google result wasn't the answer (framework pitfall / version combo / your-env-specific)
- "I'll step on this again next time" gut feeling
- Production-grade / multi-person / cross-service impact

**Don't record**: typos, var-name slips, port-in-use, SO-copyable generic errors, pure config misses (unless you've missed it 3×).

### Snippet threshold (record if ALL three)
- Reusable (not one-off)
- Scenario-specific (not "how to connect DB" generic)
- Pitfall-earned (you fought it / has 禁忌)

**Don't record**: 30-sec-googleable logic, one-off scripts, unverified GPT code, overly generic patterns. Nginx/Redis/Docker config + git aliases + ESLint rules **count** as snippets.

### ADR threshold (record if ANY)
- Tech selection (cache/DB/MQ/framework/deployment)
- Impact ≥ 2 modules / ≥ 2 person-weeks
- Wrong choice = ≥ 1 week rework
- Will be asked "why this choice" by new hires / other teams
- Supersedes an existing ADR (MUST write, set `supersedes`, flip old ADR status to `superseded`)

**Don't record**: single-module implementation details (→ snippet/bug), temp workarounds (→ daily+bug unless long-lived), pure product logic (unless tech-bound).

### Recording procedure
1. **Dedup first**: before writing a bug or ADR, `vault_search` for the topic. If a matching entry exists, **update it** (link the new context) instead of duplicating.
2. **Collect required fields** per the template (`templates/bug|snippet|daily|arch.md`). If any required field is missing, **ask the user** to fill it — don't write incomplete entries.
3. Call `vault_create_entry` once with all fields:
   - bug: `type="bug"`, pass `stack`, frontmatter must include `version` + `status`.
   - snippet: `type="snippet"`, pass `stack` + `where` + `dont` + `version`.
   - arch: `type="arch"`, pass `project` (filename needs it). **Do not pass sequence** — server auto-assigns.
   - daily: `type="daily"`, `project` is a list.
4. If `VALIDATION_ERROR` returns, read which field failed, ask user to supply it, retry.
5. Link `related` with `[[wikilinks]]` so Obsidian's graph picks it up.

---

## Flow C — Promote daily leads (migration)

**Trigger**: daily note has `【待升bugs】` / `【待升snippets】` / `【待补ADR】` lines, at session end or when user asks to tidy.

Daily is the inbox; bugs/snippets/arch are the permanent home. Promote in three steps:

1. **Create** the new permanent entry (via Flow B procedure, from the daily line's content) — `vault_create_entry`.
2. **Remove** the migrated line from the daily file:
   - If the lead is one line among many → use `vault_append_entry` is wrong; instead rewrite the daily section (read it, drop the line, `vault_update_frontmatter` not applicable — ask user, then `Edit` the daily via the host). **Simplest**: tell the user which line to remove and let them confirm; use `vault_update_frontmatter` only for the counter.
   - `vault_update_frontmatter(path="daily/YYYY-MM-DD.md", key="pending_review", value=<new>)` to decrement the counter.
3. **Link**: add `related` from the new entry back to the daily source, and from relevant bugs/snippets to a new ADR if it supersedes.

**ADR supersede special case**: when a new ADR supersedes an old one, after creating the new ADR, `vault_update_frontmatter` the **old** ADR: `status` → `superseded`. The new ADR's frontmatter `supersedes` points to the old id.

**Always ask the user to confirm** before deleting/rewriting daily content — promote is a judgment call, not a mechanical move.

---

## Flow D — Commit-triggered bug evaluation

**Trigger**: the `on-git-commit.sh` PostToolUse hook fires after a `git commit` run via the Bash tool and re-injects a `git commit landed: <hash>` / `message: …` / `initial_verdict: <lean> (prefix: …)` / `files:` / `mechanical_evidence:` system reminder. Use that reminder as a **deterministic trigger** to evaluate whether the commit fixes a recordable bug — this bypasses the unreliable "Claude proactively decides" path. Only commits made through the Bash tool in-session trigger this flow (terminal/IDE commits do not).

**Division of labor (hard rule).** The script does the deterministic parts; you (Claude) do the analytic parts, and the two cross-confirm:
- **Script (deterministic, already done by the time you see the reminder)**: classified the commit prefix into an `initial_verdict` lean; listed the changed files; grepped the session transcript for debugging keywords and listed the **raw matched lines** under `mechanical_evidence:` (it reports only what it found — it does not interpret).
- **You (analytic)**: read the raw evidence + the live session context; judge whether each raw hit is a *genuine* wrong-path (a keyword hit can be a quote, a discussion, or unrelated code — you decide); corroborate or overturn the `initial_verdict` lean.

**NO FABRICATION (top-level red line).** You must never assert a debugging fact you cannot point to a concrete source for:
1. Every corroborating claim must **quote a raw line from `mechanical_evidence:`** (a literal excerpt) OR be explicitly tagged `UNVERIFIED-BY-SCRIPT` with a one-line reason. "I recall we tried X" / "we probably hit Y" is fabrication and is forbidden.
2. If `mechanical_evidence:` is `0 keyword hits` or `(transcript unavailable)`, you may NOT claim a debugging process existed on context alone. You may say: "mechanical scan found no evidence; my read of the context suggests Z, but this is unverified — please confirm." Do NOT silently record on that basis.
3. If a hit is ambiguous (could be a quote / unrelated), you may use it **only** after explaining in your preview why it is a genuine wrong-path — and that explanation is your judgment, stated as judgment, not as fact.

### Evaluation (two layers, cross-confirming)

1. **Initial verdict (from the reminder's `initial_verdict`)** — the script's deterministic lean from the commit prefix (B1: prefix sets a *default lean*, overridable):
   - `worth-recording` (`fix`/`feat`/`refactor`/`perf`/`build`) → lean toward record.
   - `not-worth-recording` (`chore`/`docs`/`style`/`test`/`ci`) → lean toward skip.
   - `unknown` → no lean; decide purely on corroboration.
2. **Corroboration (your analysis of `mechanical_evidence:` + session context)** — does the evidence confirm or overturn the lean? Use the **Flow B bug threshold clauses** as the corroboration check (`≥ 5 min and ≥ 2 wrong paths` / `first Google result wasn't the answer` / `step on this again` / `production-grade · multi-person · cross-service impact`), and every clause you cite must be backed by a quoted raw line.

### Four outcomes (the only valid branches)

1. **Lean "worth-recording" + corroborated by quoted raw evidence** → judge **qualified**. Preview to the user — *"commit `<hash>`: initial verdict `<lean>`; corroboration: `<quoted raw line(s)>` satisfies Flow B clause `<clause>`."* — and only on user confirmation run Flow B's recording procedure (dedup → collect fields → `vault_create_entry`) in the current turn.
2. **Lean "worth-recording" + `mechanical_evidence` is empty/0-hits/unavailable** → do **not** finalize on the lean alone. Tell the user plainly: *"initial verdict leans worth-recording, but the mechanical scan found no script evidence for this commit's debugging; my read of the context suggests `<X>` (UNVERIFIED-BY-SCRIPT) — supply the debugging process or confirm whether to record."* Do not silently record, do not silently drop.
3. **Lean "not-worth-recording" + no overturning quoted evidence** → tell the user the skip reason (e.g. *"diff is a 1-line typo; `mechanical_evidence` shows no wrong-path hits"*) and do not write.
4. **Lean "not-worth-recording" + quoted raw evidence overturns it** (e.g. a `docs:` commit whose `mechanical_evidence` shows a schema-pitfall traceback) → overturn the lean, judge **qualified**, preview + ask the user as in branch 1.

### Rules
- **Never silent.** Whether qualified or not, the verdict + its script-evidence basis (or the explicit `UNVERIFIED-BY-SCRIPT` tag) must be visible to the user.
- **Never auto-write.** Writing to the vault is writing to Obsidian — outward-facing, requires user confirmation (user red line). Always preview + ask first.
- The **bug threshold** is Flow B's; the **recording procedure** is Flow B's (dedup → collect required fields → `vault_create_entry`); if a field is missing, ask the user rather than writing an incomplete entry.
- The `mechanical_evidence` scan reads `transcript_path` from the hook's stdin (the official session-transcript JSONL path); if it is absent, the block reports `(transcript unavailable)` and you proceed on the lean + your own context read only — but with every claim tagged `UNVERIFIED-BY-SCRIPT` (no fabrication).
- This flow does **not** replace the Stop hook — daily-log generation still goes through the Stop hook at session end. Flow D is specifically the bug-recording trigger anchored on `git commit`.

### Limitation: transcript scan may lag the current turn
The `mechanical_evidence` scan reads the session transcript via the hook's `transcript_path` stdin field (officially supported). Per Claude Code docs, the transcript is written asynchronously and may not yet contain the just-run `git commit` tool call when the PostToolUse hook fires — but it WILL contain the prior debugging turns, which is exactly what the scan targets (wrong-paths tried, errors hit before the fix). So the async lag does not weaken the evidence for this use case. If `transcript_path` is absent, the block reads `(transcript unavailable)` and Flow D proceeds on the lean + Claude's context read only (all claims tagged `UNVERIFIED-BY-SCRIPT`).

---

## Flow E — Distill pending journal events (proactive; required)

**Trigger (deterministic — do not wait for the user to say「提炼」)**:
- Hook `additionalContext` after `test.success` / meaningful `git.commit`
- `PreCompact` / `SessionEnd` distill reminders
- Activity notes pending events

**Hard rule — proactive**: When a Distill trigger appears, **immediately** run this flow in the current turn. Do **not** ask whether to distill. Auto-write qualified forms; skip silently (journal remains) when the ontology gate fails.

**Persona** (from `memorant.settings.json`, defaults `rigorous` + `tone=warm`): follow Distill `additionalContext` guidance for selectivity/voice/guardrail. Never let persona break constitution — no silent reject, no fake forms, privacy > diligence, Agent injection stays neutral structured.

### Silent ontology gate (internal; never a user questionnaire)

Before each `memorant_write_memory`, answer internally:

1. Wood vs form — is there a migratable **form** (scene fingerprint + bounded claim), not only episodic noise?
2. Does the form still hold if tonight's paths/ticket IDs are stripped?
3. Am I registering a form, or giving a fleeting impression a passport?
4. Does an existing memory share the same form? → reinforce / evolve / correct — do not mint a near-duplicate.
5. Modality: usable / suspicious / negative-trust / dusty — tone must match (never deep-trust a dusty form).
6. Is a false form worse than skipping? If yes and unsure → skip write.
7. What is the single core entity and its edge to existing nodes?

Gate fail → do **not** call `memorant_write_memory`. Optionally note skip aggregates at SessionEnd (`skip:no_form`, `skip:episodic_only`, `skip:duplicate_same_form`). Never quiz the user with ontology questions.

### Scene fingerprint (work-general; required on write)

Include in body or claim scaffolding (general slots, not stack dialects as primary):

- problem shape / trigger appearance / key constraints / decision type / weak context anchors
- one applicability sentence in plain language  
No fingerprint → gate fail (懂得不记).

### Write contract (refuse to claim completion if missing)

1. Call `memorant_list_pending_events` (optional `session_id` / `project`).
2. Distill **only** from those events — never invent process details from model memory.
3. For each **gate-passing** candidate, call `memorant_write_memory` with **all** of:
   - `claim`, `kind`, `confidence`, `trust_tier`
   - `evidence[]` (≥1) quoting event excerpts
   - `source_event_ids` (≥1) from pending events
   - `origin_session_ids` when known
   - `project` / `project_key` inherited from source events (key must match sources)
   - `recurrence_cadence` — only when the form is a **recurring seasonal/annual experience** (e.g. year-end review, fiscal-cycle quirk). Marks it for the seasonal clock: within its recurrence window it stays recallable-with-verification instead of being dusted by absolute days. Default `ad-hoc` (don't set for one-offs). See §3.4.
4. **A and B auto-write** — no per-item confirmation for Memorant memories.
5. Prefer one solid memory over three thin ones; same form → update edge, don't spam cards.
6. `needs_attention` only for: cannot redact safely, unresolved conflict, or high-risk bad memory — not for ordinary gate skips.

## Flow F — Trust Route (A/B + field)

- **A (`verified`)**: failure→fix→success closed loop (or tool-proven fact) with citable evidence → `trust_tier=verified`, `lifecycle=active`.
- **B (`provisional`)**: architecture preference, unverified root cause, thin evidence → still auto-write with `trust_tier=provisional` (recall: suspicious / HOLD).
- **B→A**: only via `memorant_promote` / `memorant_feedback(successful_reuse)` when a **different** `session_id` supplies a **success** outcome. Same-session retries do not promote.
- Conflicts: via user-confirmed `memorant_feedback` — with `replacement_claim` → `corrected` + new memory; without → `rejected` (audited delist). Never silent overwrite / silent reject.
- **Recall modalities**: usable / suspicious / negative-trust (`corrected`/`rejected` 反面服役 + Agent `DENY`) / dusty (久未验证 → `VERIFY-FIRST`, no deep-trust steps). Obey injected rails; human short-asks stay rare.

## Flow G — Feedback & Activity

- Users correct asynchronously with `/memorant-feedback` → `memorant_feedback`.
- `/memorant-activity` shows today's writes/promotions/conflicts — audit only, not an approval gate.
- SessionEnd shows one non-blocking summary line; interrupt only on conflicts / high-risk bad memory.

## Quick reference

### Memorant (preferred)
- `memorant_append_event` / `memorant_list_pending_events`
- `memorant_write_memory` / `memorant_recall`
- `memorant_feedback` / `memorant_promote` / `memorant_activity`

### Legacy vault_* (compat)
- Search: `vault_search(query, dirs?, project?, limit?)`
- Create: `vault_create_entry(type, title, body, frontmatter, stack?, project?, date_str?)`
- Append: `vault_append_entry(path, content, section?)`
- Update frontmatter: `vault_update_frontmatter(path, key, value)`
- Delete: `vault_delete_entry(path, confirm=true)`
- Recent: `vault_get_recent(dir, limit)`

The compatible Stop hook (`hooks/vault-stop.sh`) still prompts for legacy daily review. Observer hooks (`hooks/memorant-hook.sh`) capture journal events and inject bounded recall — fail-open, never block the session.
