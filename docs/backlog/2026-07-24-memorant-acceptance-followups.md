# Memorant 验收后续清单（2026-07-24）

首轮真实项目验收（`/Users/tal/Documents/Memorant`，kpi / xlswriter 导出 400）之后整理。  
状态：MVP 已合入 main（PR #1）；下列为**尚未做或未做实**的事项，避免遗忘。

**北星原则**：见 [`docs/specs/2026-07-24-product-doctrine.md`](../specs/2026-07-24-product-doctrine.md)  
→ **主动**接住经验；**相似场景**下可参考/可应用（抗遗忘、减重复劳动）；经验随新做法**自我迭代**。  
→ 「对得上项目」只是匹配信号，不是产品目的。
证据样本：

- journal ×5（session.start / tool.failure×2 / git.commit×2）
- memories ×1（B · provisional，xlswriter 中文文件名截断）
- activity ×1（`memory.write`）

---

## P0 — 主动闭环（产品最高优先）

用户已确认：主动优先于「等人说请提炼」。

- [ ] **Skill 默认主动 Distill**：在 `test.success` / 有意义的 `git.commit` / PreCompact / SessionEnd 之后，当前 Claude **必须**跑 Flow E（查 pending → 写 A/B），无需用户再口述「请提炼」
- [ ] **Hook 提醒足够强硬**：边界事件的 `additionalContext` 明确要求「立即 Distill pending events，自动写入，勿询问确认」
- [ ] **主动召回保持开启**：SessionStart / UserPromptSubmit / Failure 注入（已有骨架；配合 Project Identity 对齐）
- [ ] 验收标准：修完非平凡 bug 且测试变绿后，**用户未额外吩咐**仍应产生 `memories/` 写入或 `needs_attention`

## P0 — Distill 质量（主动时必须写对）

主动写垃圾等于主动添乱——与主动原则一并做。

- [ ] Distill 写入时**强制**填充 `source_event_ids`
- [ ] 填充结构化 `evidence[]` 与 `origin_session_ids`
- [ ] **Project Identity**：[`docs/specs/2026-07-24-project-identity.md`](../specs/2026-07-24-project-identity.md)
- [ ] Skill 硬性检查：缺证据/event_ids/`project_key` 不得声称完成；key 继承源事件

## P1 — Observer / Journal 信号质量

- [ ] Bash `PostToolUseFailure` 证据加厚（command / stderr 有界摘要）
- [ ] 噪声策略：极瘦 failure、纯 `session.start` 的 pending/展示策略
- [ ] Journal 生命周期：已引用归档；长期未引用清理（首版不做自动删）

## P2 — 命令与使用面

- [ ] 文档标明 Claude Code 命令 vs Cursor MCP
- [ ] 安装：`/reload-plugins`、先 add 再 install
- [ ] 正式 release notes（含主动 Distill 默认开启的说明）

## P2 — 通用工作扩展（主动原则不变）

- [ ] 非开发节点的主动捕获口令/适配器（PRD 定稿、ADR 采纳等）——换 Observer，不换「等人吩咐」
- [ ] kind / workstream 扩展；PRD 正文外置、结论进 memory

## P3 — 明确不做（首期仍成立）

- 后台常驻 Agent / 本地模型自动 Distill（主动仍在当前宿主会话）
- 向量数据库、Web Dashboard、云同步、团队权限
- 强制迁移既有 `bugs/` `snippets/` `daily/` `arch/`

---

## 建议下次动手顺序

1. **主动 Distill**（Skill + Hook 文案）——对齐北星  
2. Distill 契约（event_ids / evidence / project_key）——主动时写对  
3. Project Identity —— 召回对得上  
4. Bash failure 证据加厚  
5. 文档与 release notes  

## 相关路径

- 用户库：`/Users/tal/Documents/Memorant/`
- 原则：`docs/specs/2026-07-24-product-doctrine.md`
- Skill：`skills/memorant/SKILL.md`
- 计划原文：`~/.cursor/plans/memorant-full-product_3144b9ca.plan.md`（勿改计划文件本身）
