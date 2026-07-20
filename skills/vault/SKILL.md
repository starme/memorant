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

## Quick reference

- Search: `vault_search(query, dirs?, project?, limit?)`
- Create: `vault_create_entry(type, title, body, frontmatter, stack?, project?, date_str?)`
- Append: `vault_append_entry(path, content, section?)`
- Update frontmatter: `vault_update_frontmatter(path, key, value)`
- Delete: `vault_delete_entry(path, confirm=true)` — rare, only when a daily lead is its own whole file
- Recent: `vault_get_recent(dir, limit)`

The Stop hook (`hooks/vault-stop.sh`) will remind you at session end: review the session for recordable bugs/decisions, generate/append the daily log, and on Fridays prompt to tidy `pending_review`. The hook only **prompts** — you (Claude) perform the actual writes via the tools above, after user confirmation.
