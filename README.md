# 书童 · Memorant

Claude Code 的本地长期记忆运行时。Hooks 采集确定性事件，当前会话中的 Claude 提炼为可追溯 Memory Envelope（A 已验证 / B 待验证），Markdown/Obsidian 是唯一业务事实源。

Tagline: *The companion that remembers what your agents learn.*

## What it does

- **Observer** — Session / Failure / Test / Commit / PreCompact 写入不可变 Event Journal
- **Distiller** — 当前 Claude Code 按 Skill 将 pending events 提炼为 A/B 记忆（自动写入，不再逐条确认）
- **Event-driven recall** — SessionStart / UserPromptSubmit / Failure 注入有界召回（B 类降权并标注「待验证」）
- **B→A promotion** — 仅当**不同会话**出现成功 outcome 时升级
- **Memory Activity** — Obsidian `activity/` + `/memorant-activity` 异步审计；冲突才即时提醒
- **Legacy vault** — 既有 `bugs/` / `snippets/` / `daily/` / `arch/` 与 `vault_*` 工具在兼容期内继续可用

## Memorant layout

```
$MEMORANT_ROOT/
├── journal/YYYY/MM/DD/<ts>-<event_id>.md   immutable events
├── memories/<memory_id>.md                 A/B Memory Envelopes
├── activity/YYYY-MM-DD.md                  append-only audit trail
├── .memorant/                              rebuildable cursors/locks only
├── bugs/ snippets/ daily/ arch/            legacy vault (still read)
└── arch/.sequence
```

## Install / upgrade from vault-experience

```
/plugin marketplace add starme/vault-experience-plugin
/plugin install memorant@memorant-marketplace
/reload-plugins
```

Configure root (new names preferred; old names still work):

```bash
export MEMORANT_ROOT=/path/to/your/knowledge-base
# fallback: VAULT_ROOT, .claude/memorant.local.md, .claude/vault.local.md
mkdir -p "$MEMORANT_ROOT"/{journal,memories,activity,bugs,snippets,daily,arch}
echo 0 > "$MEMORANT_ROOT"/arch/.sequence
```

**Requirements:** `uvx` (`uv`) for the Python MCP server.

### Privacy model

- Journal 默认只存有界摘要 + payload hash，不存完整 transcript / 完整工具参数 / 环境变量
- 写入前脱敏：token、password、private key、Authorization header 等
- 无法安全摘要时只保留 hash 与来源类型
- 召回内容作为不可信数据注入，带 trust/source 标签，不得覆盖系统或用户指令

### Auto-write semantics

| Tier | Write | Recall |
|---|---|---|
| A `verified` | auto | normal weight |
| B `provisional` | auto | downweighted + 「待验证」 |
| Conflict | new memory + old `corrected`/`superseded` | old stops normal recall |

### Feature flags (defaults: all on for first compat release)

Set via env `MEMORANT_<FLAG>=true|false` or `.claude/memorant.local.md` frontmatter:

| Flag | Effect when false |
|---|---|
| `auto_capture` | Hooks skip journal append |
| `auto_write_verified` | Refuse A-tier `memorant_write_memory` |
| `auto_write_provisional` | Refuse B-tier `memorant_write_memory` |
| `event_recall` | Hooks skip recall injection |
| `activity_summary` | SessionEnd skips one-line Activity summary |

### Rollback

关闭新 Hook / 自动写配置并恢复使用旧 Skill 即可。Journal、Memory、Activity Markdown **保留**，不做破坏性迁移。

### Data export

整库即 Markdown 目录：复制 `$MEMORANT_ROOT` 即可导出；Obsidian 直接打开该目录。

## Commands

| Command | Purpose |
|---|---|
| `/memorant-search` | 搜索 legacy vault |
| `/memorant-log` / `/memorant-adr` | 兼容的人工记录入口 |
| `/memorant-activity` | 当天 Memory Activity |
| `/memorant-feedback` | 纠正 / 替代 / 确认成功复用 |

Legacy `/vault-*` 命令仍可用（deprecated aliases）。

## MCP tools

**Memorant:** `memorant_append_event`, `memorant_list_pending_events`, `memorant_write_memory`, `memorant_recall`, `memorant_feedback`, `memorant_promote`, `memorant_activity`

**Compat:** `vault_search`, `vault_create_entry`, `vault_append_entry`, `vault_update_frontmatter`, `vault_delete_entry`, `vault_get_recent`（结果可带 deprecated 语义）

See `skills/memorant/SKILL.md` for Distill / Trust Route / Feedback flows.
