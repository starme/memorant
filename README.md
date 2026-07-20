# Vault Experience Plugin

A Claude Code plugin that turns your daily development into a searchable experience vault. Automatically surfaces and records **bugs / snippets / daily logs / ADRs** to a local Obsidian vault, shared across all your projects.

## What it does

- **Passive search**: when you hit a bug, debug, or make a tech choice, Claude searches your vault for past experience before acting
- **Active recording**: after solving a non-trivial bug or making an architectural decision, Claude prompts you to record it
- **Daily logs**: session-end summaries appended to `daily/`
- **Promote flow**: daily "待升"线索 migrate to bugs/snippets/ADR with one confirmation

## Vault structure

```
$VAULT_ROOT/
├── bugs/      {stack}-{短描述}-{YYYYMMDD}.md      framework pitfalls, root-caused bugs
├── snippets/  {场景}-{技术栈}.md                  reusable, scenario-specific code/config
├── daily/     {YYYY-MM-DD}.md                     time-first dev log (project in frontmatter)
├── arch/      adr-{序号}-{项目}-{短描述}.md        architecture decisions, global ADR numbering
└── arch/.sequence                              global ADR counter
```

Four categories differ in project coupling: `arch` is project-bound (filename carries project); `bugs`/`snippets` are weakly bound (project in optional frontmatter); `daily` is time-first (project in frontmatter array, cross-project days land in one file).

## Install

```bash
claude plugin install <your-github-user>/vault-experience-plugin
```

Then set your vault path (one-time):

```bash
# Option A: env var in shell profile
export VAULT_ROOT=/path/to/your/vault

# Option B: plugin settings (read by the Stop hook)
mkdir -p .claude
cat > .claude/vault.local.md <<'EOF'
---
VAULT_ROOT: /path/to/your/vault
---
EOF
```

Create the four directories:

```bash
mkdir -p "$VAULT_ROOT"/{bugs,snippets,daily,arch}
echo 0 > "$VAULT_ROOT"/arch/.sequence
```

Requirements: `uvx` (the uv tool) for the Python MCP server runtime.

## How it works

| Layer | Responsibility |
|---|---|
| MCP server (`mcp-server/`) | atomic file I/O + frontmatter schema validation + path whitelist + ripgrep search |
| Skill (`skills/vault/`) | orchestration: thresholds, dedup checks, promote migration, template filling |
| Hook (`hooks/vault-stop.sh`) | timing triggers at session end; prompts only, never writes |
| Commands (`commands/`) | manual entry points: `/vault-search`, `/vault-log`, `/vault-adr` |

The server validates frontmatter (required fields, naming rules) and refuses path traversal — so writes stay inside `$VAULT_ROOT`. Business logic (when to record, where to migrate) lives in the Skill, not the server, so it stays adaptable.

See `skills/vault/SKILL.md` for the full recording thresholds and promote flow.
