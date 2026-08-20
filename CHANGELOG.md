# Changelog

本仓库（书童 · Memorant）的发布记录。版本号对齐 `.claude-plugin/plugin.json`。

## [0.2.0] — 2026-08-20

品牌统一、历史数据迁移与多宿主适配。

### Breaking changes
- **移除 `vault_*` MCP 工具**（`vault_search` / `vault_create_entry` / `vault_append_entry` / `vault_update_frontmatter` / `vault_delete_entry` / `vault_get_recent`）与 `/vault-*` 命令；只保留 `memorant_*` 入口。
- **移除 `vault-mcp` entry point**，只保留 `memorant-mcp`。
- **移除 `VAULT_ROOT` / `vault.local.md` 兼容层级**，root 解析优先级收敛为 `MEMORANT_ROOT > settings.json > memorant.local.md`。
- 升级用户：历史 `bugs/`/`snippets/`/`daily/`/`arch/` 数据在首次 SessionStart 会提示迁移；运行 `/memorant-migrate`（或 `memorant_migrate(confirm=true)`）迁移为 Memory Envelope。

### 数据迁移（P0）
- 新增 `memorant_migrate` 工具与 `/memorant-migrate` 命令：历史四类 notes → Memory Envelope。
- 迁移是「投影生成」：原始目录保留原位并先备份到 `.memorant/migration-backup/<timestamp>/`（永久保留，可回滚）。
- 幂等、冲突不覆盖、单条失败隔离；迁移报告落盘 `.memorant/migration-report-<timestamp>.md`，全部 excerpt 脱敏。
- `daily` 仅提取「## 踩坑/线索」下 `【待升bugs】/【待升snippets】/【待补ADR】` 前缀行（provisional），纯流水跳过。

### 多宿主适配（P1）
- 新增 `host_adapters/` 包：Claude Code（全能力）、Codex CLI（无自动事件采集/提炼）、Codex 云端/IDE（只读 + 手动写入）。
- 新增 `memorant_host_info` 只读工具，上报当前宿主能力矩阵。
- 通用核心 host-agnostic；CI 断言核心不 import `host_adapters`。

## [0.1.0] — 2026-07-28

首个可用版本：本地长期记忆运行时（Hooks + MCP + Skill），A/B 信任场、主动 Distill、默认严谨书童。

### 主动闭环（P0）
- **主动 Distill**：修完非平凡 bug 且测试变绿 / 有意义 `git.commit` 后，宿主 Claude 读 Hook 注入的 reminder，自动跑静默本体论门禁、主动写 A/B memory——**无需用户口述"请提炼"**
- **静默门禁**：写入前对内自问（木头 vs 型、可迁移性、近亲去重、模态判定），无型则静默 skip，journal 留痕；不对人问卷
- **Distill 契约**：`MemoryWriteInput` + `MemoryEnvelope` 的 `source_event_ids` 强制 `min_length=1`；evidence ≥1；写入时从 source journal events 继承 `project_key` / `project`
- **Project Identity**：稳定 `project_key`（cwd 派生），hook 写入事件时携带；召回按项目对齐

### Settings / 性格（P1）
- `memorant.settings.json`：用户级 + 项目级 deep-merge（项目覆盖用户）
- 行为预设 `rigorous`（默认）/ `diligent` / `silent`，映射 selectivity / voice / guardrail / freshness
- `tone`：`serious` / `warm`（默认）/ `playful`，影响对人嗓门，不影响 Agent 注入（中性结构化）
- 兼容旧 `MEMORANT_*` env / `memorant.local.md`；`discover_root` 优先级 `env > settings.json > local.md > VAULT_*`

### 信任场与服役（P2）
- 四态分类：`usable` / `suspicious` / `negative` / `dusty`
- 召回分层 × 信任口吻：高相似×热正信任注入可应用骨架；可疑短问裁决；负信任近坑短谏 + Agent 否定护栏；尘封禁自动深信
- 负信任反面服役：`corrected` / `rejected` 记忆在近坑时主动提醒「曾误判为 X，实际应 Y」
- 校正审计：`corrected`（有 replacement）/ `rejected`（无 replacement，可审计摘牌）/ `superseded`；无静默改写、无静默 reject
- 潮汐：久未验证可见且带谦虚口吻；深尘封禁止深信式完整注入（季节钟精调待续）

### Observer / Journal 信号质量
- Bash `PostToolUseFailure` 证据加厚：`command` / `exit_code` / `stderr` / `stdout` 带标签
- Journal append-only，按事件 payload hash 去重
- Activity 审计底账：`memory.write` / `memory.recall` / `memory.promote` / `memory.feedback` 均留痕

### 宿主支持
- **Claude Code（完整）**：Hooks + slash commands + Skill + MCP 全链路；主动 Distill 成立
- **Cursor（仅 MCP 子集）**：被动查询/写入可用，无 Hooks 无主动闭环

### 默认值
- 行为：`rigorous`（selectivity high / voice mid / guardrail high）
- tone：`warm`
- 隐私：`cloud_projection = line_draft`（云端注入默认只给 claim/标题/做法骨架，禁止 evidence 原文与实体）
- flags：`auto_capture` / `auto_write_verified` / `auto_write_provisional` / `recall` / `activity_summary` 全开（首期兼容）

### 修复（验收暴露）
- **Flow D vs Flow E 指令矛盾**（[PR #2](https://github.com/starme/vault-experience-plugin/pull/2)）：`git.commit` 上两 hook 注入相反行为指令致宿主放弃主动 Distill。修复：Flow D 让位 Flow E，只报机械事实；详见 vault ADR-002「hook 职责分工原则」
- **`MemoryEnvelope.source_event_ids` 缺 `min_length=1`**：envelope 层旁路，已补齐

### 已知限制 / 非首期目标
- 后台常驻 Agent、本地模型 Distill、向量库、Web Dashboard、云同步、团队权限
- `test.success` 触发路径待真实会话补验（已验证 `git.commit` 路径）
- Journal 噪声策略 / 生命周期清理待续
- 季节钟精调、摘牌 UX 文案待续

### 相关
- 需求冻结：`docs/specs/2026-07-24-memorant-requirements-freeze.md`（docs 已移出版本控制，本地可查）
- 北星：`docs/specs/2026-07-24-product-doctrine.md`
- Settings / 性格：`docs/specs/2026-07-24-memorant-settings-and-persona.md`
