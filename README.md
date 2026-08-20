# 书童 · Memorant

Claude Code / Codex 的本地长期记忆运行时。Hooks 采集确定性事件，当前宿主 Agent 提炼为可追溯 Memory Envelope（A 已验证 / B 待验证），Markdown/Obsidian 是唯一业务事实源。

Tagline: *The companion that remembers what your agents learn.*

> **v0.2.0** — 品牌统一 + 历史数据迁移 + 多宿主适配。完整发布记录见 [CHANGELOG](CHANGELOG.md)。

## What it does

- **Observer** — Session / Failure / Test / Commit / PreCompact 写入不可变 Event Journal
- **Distiller** — 宿主 Agent 按 Skill 将 pending events 提炼为 A/B 记忆（自动写入，不再逐条确认）
- **Event-driven recall** — SessionStart / UserPromptSubmit / Failure 注入有界召回（B 类降权并标注「待验证」）
- **B→A promotion** — 仅当**不同会话**出现成功 outcome 时升级
- **Memory Activity** — Obsidian `activity/` + `/memorant-activity` 异步审计；冲突才即时提醒
- **Legacy migration** — 历史 `bugs/`/`snippets/`/`daily/`/`arch/` 可迁移为 Memory Envelope（幂等、可回滚、可审计）

## Memorant layout

```
$MEMORANT_ROOT/
├── journal/YYYY/MM/DD/<ts>-<event_id>.md   immutable events
├── memories/<memory_id>.md                 A/B Memory Envelopes
├── activity/YYYY-MM-DD.md                  append-only audit trail
├── .memorant/                              rebuildable cursors/locks + migration backup/report
├── bugs/ snippets/ daily/ arch/            legacy notes (Obsidian-facing, migrated on demand)
└── arch/.sequence
```

## Install

```
/plugin marketplace add starme/memorant
/plugin install memorant@memorant-marketplace
/reload-plugins
```

`marketplace add` 只需一次；之后 `/plugin install memorant@memorant-marketplace`（**先 add 再 install**，否则 marketplace 未注册会找不到插件）。升级时重跑 install + `/reload-plugins`。

Configure root / persona (preferred: `memorant.settings.json`; `MEMORANT_ROOT` still wins):

```bash
# ~/.claude/memorant.settings.json — also <repo>/.claude/memorant.settings.json
# { "root": "/path/to/Memorant", "persona": { "behavior": { "preset": "rigorous" }, "tone": "warm" } }

export MEMORANT_ROOT=/path/to/your/knowledge-base
# fallback: settings.json root → memorant.local.md
mkdir -p "$MEMORANT_ROOT"/{journal,memories,activity}
```

Defaults: behavior `rigorous`, tone `warm`. Recall is trust-aware（敢用 / 可疑 HOLD / 负信任 DENY / 尘封 VERIFY-FIRST）。

**Requirements:** `uvx` (`uv`) for the Python MCP server.

### Migrating from v0.1.x legacy notes

升级后首次 SessionStart 若检测到历史 `bugs/`/`snippets/`/`daily/`/`arch/` 数据，宿主会提示「是否迁移」，**不会自动迁移**。确认后运行 `/memorant-migrate`（或调用 `memorant_migrate(confirm=true)`）：

- 迁移前原始目录整体备份到 `.memorant/migration-backup/<timestamp>/`（永久保留，可回滚）。
- 迁移是「投影生成」：原始目录不删除，同时生成 `memories/` 下的 Memory Envelope。
- 幂等：重复迁移不产生重复记忆；冲突目标不覆盖；单条失败不阻断其他条目。
- 迁移报告落盘 `.memorant/migration-report-<timestamp>.md`。

### Testing

Python tests use the project environment and must run from `mcp-server/`:

```bash
cd mcp-server
uv sync --dev
uv run python -m pytest
```

The shell hooks intentionally use stdlib-only `python3 -B -S` and are tested from the repository root:

```bash
cd ..
bash tests/test-memorant-hook.sh
bash tests/test-on-git-commit.sh
```

Local `.venv/` directories and the generated `mcp-server/uv.lock` are disposable and are not part of the plugin distribution.

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

迁移后发现异常，从 `.memorant/migration-backup/<timestamp>/` 恢复原文件即可（备份永久保留）。或关闭新 Hook / 自动写配置并恢复使用旧流程。Journal、Memory、Activity Markdown **保留**，不做破坏性删除。

### Data export

整库即 Markdown 目录：复制 `$MEMORANT_ROOT` 即可导出；Obsidian 直接打开该目录。

## Commands

| Command | Purpose |
|---|---|
| `/memorant-search` | 召回相关记忆 |
| `/memorant-log` / `/memorant-adr` | 记录 bug/snippet/ADR 为 Memory |
| `/memorant-migrate` | 迁移历史 bugs/snippets/daily/arch 为 Memory |
| `/memorant-activity` | 当天 Memory Activity |
| `/memorant-feedback` | 纠正 / 替代 / 确认成功复用 |

## Host support（Claude Code / Codex CLI / Codex 云端/IDE）

通用核心（journal / memory-store / recall / promotion / activity / migration）与宿主适配层分离；宿主能力差异在适配层显式表达。完整能力矩阵与降级说明如下：

| 能力 | Claude Code | Codex CLI | Codex 云端/IDE |
|---|---|---|---|
| 事件采集 | 完整（Hooks） | 不可用（无宿主事件钩子，需显式 `memorant_append_event`） | 不可用（手动 `memorant_append_event`） |
| 召回 | 完整（Hook 注入） | 可用（`memorant_recall`） | 只读（`memorant_recall`） |
| 自动提炼 | 完整（Hook 注入 Distill reminder） | 不可用（手动 `memorant_list_pending_events` + `memorant_write_memory`） | 不可用（手动 `memorant_write_memory`） |
| A/B promotion | 完整 | 可用（`memorant_promote`） | 可用（`memorant_promote`，依赖手动） |
| Activity 审计 | 完整 | 可用（`memorant_activity`） | 只读（`memorant_activity`） |

### Claude Code（完整）

装上即得全链路——修完 bug 跑测试变绿 / commit 后，宿主 Claude 读到 Hook 注入的 Distill reminder，自动跑静默门禁、主动写 A/B memory。

### Codex CLI

在 `~/.codex/config.toml` 注册同一个 MCP server：

```toml
[mcp_servers.memorant]
command = "uvx"
args = ["--from", "<plugin>/mcp-server", "memorant-mcp"]
```

Codex CLI 无宿主事件钩子，事件采集需显式调用 `memorant_append_event`；召回用 `memorant_recall`。自动提炼**不可用**——手动 `memorant_list_pending_events` + `memorant_write_memory`。

### Codex 云端/IDE

通过 Codex 平台的 MCP server 配置接入同一 server。能力为**只读召回 + 手动写入**（`memorant_recall` / `memorant_write_memory` / `memorant_promote` / `memorant_activity`）。无自动事件采集与自动提炼，适配层如实声明并返回一致的降级提示。

## MCP tools

`memorant_append_event`, `memorant_list_pending_events`, `memorant_write_memory`, `memorant_recall`, `memorant_feedback`, `memorant_promote`, `memorant_activity`, `memorant_migrate`, `memorant_host_info`

See `skills/memorant/SKILL.md` for Distill / Trust Route / Feedback flows.
