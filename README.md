# 书童 · Memorant

A Claude Code plugin that turns daily development work into a searchable local knowledge base. While you code, Claude searches past bugs/snippets/ADRs before acting and prompts you to record new ones after solving them.

## What it does

- **Search before acting** — when you hit a bug or make a tech choice, Claude checks the vault first so you don't repeat a wrong path.
- **Record after solving** — after a non-trivial bug, reusable snippet, or architectural decision, Claude prompts you to record it (never auto-writes).
- **Daily logs** — at session end, the day's work is summarized and appended to one `daily/YYYY-MM-DD.md` (cross-project days land in the same file).
- **Promote flow** — daily "待升" leads migrate into permanent `bugs/` / `snippets/` / `arch/` entries with one confirmation.

## Memorant structure

```
$MEMORANT_ROOT/
├── bugs/      {stack}-{短描述}-{YYYYMMDD}.md      framework pitfalls, root-caused bugs
├── snippets/  {场景}-{技术栈}.md                  reusable, scenario-specific code/config
├── daily/     {YYYY-MM-DD}.md                     time-first dev log (project in frontmatter)
├── arch/      adr-{序号}-{项目}-{短描述}.md        architecture decisions, global ADR numbering
└── arch/.sequence                              global ADR counter
```

Project coupling differs by type: `arch` is project-bound (project in the filename); `bugs`/`snippets` are weakly bound (project in optional frontmatter); `daily` is time-first (project in a frontmatter array, so a cross-project day merges into one file).

## Install

Plugins ship through a marketplace, so installing is two steps: add this repo as a marketplace, then install the plugin from it.

Inside a Claude Code session:

```
/plugin marketplace add starme/vault-experience-plugin
/plugin install memorant@memorant-marketplace
/reload-plugins
```

Or from the terminal (defaults to user scope):

```bash
claude plugin marketplace add starme/vault-experience-plugin
claude plugin install memorant@memorant-marketplace
```

You can also browse and install via the `/plugin` panel's Discover tab.

### Set your Memorant path (one-time)

Either an env var in your shell profile:

```bash
export MEMORANT_ROOT=/path/to/your/knowledge-base
```

…or a plugin settings file read by the Stop hook:

```bash
mkdir -p .claude
cat > .claude/memorant.local.md <<'EOF'
---
MEMORANT_ROOT: /path/to/your/knowledge-base
---
EOF
```

Then create the four directories:

```bash
mkdir -p "$MEMORANT_ROOT"/{bugs,snippets,daily,arch}
echo 0 > "$MEMORANT_ROOT"/arch/.sequence
```

**Requirements:** `uvx` (the `uv` tool) for the Python MCP server runtime.

## Commands

| Command | Purpose |
|---|---|
| `/memorant-search <query>` | Search past bugs/snippets/ADRs/daily notes by keyword or error text |
| `/memorant-log <bug\|snippet> <desc>` | Record a bug or snippet after solving something non-trivial |
| `/memorant-adr <desc>` | Write an Architecture Decision Record |

The legacy `/vault-search`, `/vault-log`, and `/vault-adr` commands remain compatibility aliases. ADRs and daily logs are written through the Skill's recording flow (`/memorant-adr` for ADRs, session-end hook for daily).

## How it works

| Layer | Responsibility |
|---|---|
| MCP server (`mcp-server/`) | atomic file I/O, frontmatter schema validation, path whitelist, ripgrep search |
| Skill (`skills/memorant/`) | orchestration: search/record thresholds, dedup checks, promote migration, template filling |
| Hook (`hooks/vault-stop.sh`) | session-end triggers — prompts only, never writes |
| Commands (`commands/`) | manual entry points for search / record / ADR |

The server validates frontmatter (required fields, naming rules) and rejects path traversal, so writes stay inside `$MEMORANT_ROOT`. Legacy `VAULT_ROOT` and `.claude/vault.local.md` configuration remain supported.

Available MCP tools (exposed by the server): `vault_search`, `vault_create_entry`, `vault_append_entry`, `vault_update_frontmatter`, `vault_get_recent`, `vault_delete_entry`.

See `skills/memorant/SKILL.md` for the full recording thresholds and promote flow.
