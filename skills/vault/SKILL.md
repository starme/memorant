---
name: vault
description: Use when developing — search the dev experience vault for past bugs/snippets/ADRs before debugging or making tech choices, prompt to record non-trivial bugs/reusable snippets/architectural decisions after solving them, generate daily logs at session end, and promote daily "待升" leads into permanent bugs/snippets/ADR entries. Triggers on debugging, error messages, tech selection, "记到 vault", "vault", "经验库", daily summary, ADR.
---

# Vault Experience Orchestrator

Orchestrates the `vault_*` MCP tools into three flows: **search before acting**, **record after solving**, **promote daily leads**. The MCP server handles atomic file I/O + schema validation; this skill decides *when* to search, *whether* to record, and *where* to migrate.

## Vault layout (do not deviate)

| Type | Path | Filename | Project coupling |
|---|---|---|---|
| bug | `bugs/` | `{stack}-{短描述}-{YYYYMMDD}.md` | frontmatter (optional) |
| snippet | `snippets/` | `{场景}-{技术栈}.md` | frontmatter (optional) |
| daily | `daily/` | `{YYYY-MM-DD}.md` | frontmatter `project[]` (cross-project = one file) |
| arch | `arch/` | `adr-{序号}-{项目}-{短描述}.md` | filename (strong) |

`date` is the unified field across all types. `arch/.sequence` holds the global ADR counter (server auto-increments).

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

## Quick reference

- Search: `vault_search(query, dirs?, project?, limit?)`
- Create: `vault_create_entry(type, title, body, frontmatter, stack?, project?, date_str?)`
- Append: `vault_append_entry(path, content, section?)`
- Update frontmatter: `vault_update_frontmatter(path, key, value)`
- Delete: `vault_delete_entry(path, confirm=true)` — rare, only when a daily lead is its own whole file
- Recent: `vault_get_recent(dir, limit)`

The Stop hook (`hooks/vault-stop.sh`) will remind you at session end: review the session for recordable bugs/decisions, generate/append the daily log, and on Fridays prompt to tidy `pending_review`. The hook only **prompts** — you (Claude) perform the actual writes via the tools above, after user confirmation.
