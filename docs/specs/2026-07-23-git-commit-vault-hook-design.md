# Design: Git Commit Hook → Vault Bug Recording

**Date**: 2026-07-23
**Status**: Design approved (decisions A2/B1/C2), pending spec review
**Owner**: star

## 背景与问题

vault-experience plugin 的 bug 记录依赖"Claude 主动判断该不该存"——这个机制不可靠。实测：snippets 有内容（靠用户显式 `/vault-log` 触发），bugs 仅 1 条且非业务 bug。根因不是"没价值"，是触发链断了。

现有 `Stop` hook（`hooks/vault-stop.sh`）在 session 结束时输出笼统提示让 Claude 写 daily/bug，但：
1. 提示太笼统，Claude 容易跳过
2. session 结束时上下文已长，判断成本高
3. 没有锚定具体"解决了什么"，只是"你这次解决问题了吗"

**目标**：把 bug 记录的触发点钉死在 `git commit` 这个确定性动作上，绕过 Claude 的主动性惰性。

## 解决思路

commit 是用户确定会发生的动作，且 commit message 自带语义（fix/feat/refactor）。挂一个 PostToolUse hook，在 git commit 完成后把 commit 信息回注给 Claude 做**初判**；再用**当前 session context** 里捞出的排查过程真凭实据（试了几条错路、Google 首条是否答案等）**佐证**初判，两层一致才判达标，询问用户确认后记录。

## 机制（已验证可行）

依据 Claude Code hooks 官方文档（https://code.claude.com/docs/en/hooks.md）：

- **触发**：`PostToolUse` + `matcher: "Bash"` + `if: "Bash(git commit*)"`。`if` 字段在 hook 层就过滤掉非 commit 的 Bash 调用，噪音零成本（非 commit 的 Bash 不生成 hook 进程）。
- **数据**：hook 从 stdin 收到 JSON，含 `tool_input.command`（含 commit message）+ `tool_response.stdout`（含 commit hash + files changed）。
- **回注**：hook 输出 `{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: "..."}}`，Claude 在同回合以系统提醒形式收到，在工具结果旁。纯 stdout 文本在 PostToolUse **不**显示给 Claude，必须输出 JSON。
- **限制**：`additionalContext` 上限 10,000 字符。

## 设计决策

### 决策 1（A2）：hook 回注内容带 commit message + 改动文件列表

- message 一行 + hash + `git diff --name-only HEAD~1` 文件列表。
- 不带 diff 全文（体积大、噪音多；Claude 要看 diff 自己调）。
- 文件列表对判断"跨模块还是单点"最有价值，体积小（远低于 10k 上限）。

### 决策 2（B1，修订）：hook 放行所有 commit；前缀只定默认倾向，门槛靠 session context 真凭实据

- `if: "Bash(git commit*)"` 匹配所有 commit，hook 层无法按 message 前缀过滤——`if` 匹配命令字符串不匹配 message 内容。
- hook 解析 message 后原样交给 Claude，**前缀不决定是否记录，只决定默认倾向**：
  - `fix`/`feat`/`refactor` → 默认倾向"值得记"
  - `chore`/`docs`/`style`/`test` → 默认倾向"不值得记"
  - 任何默认倾向都**可被 session context 的真凭实据推翻**：如 `docs:` 写 ADR 时踩了 schema 坑、`test:` 复现了一个棘手 bug，只要 context 里有排查过程，就推翻默认判达标。
- 好处：改触发范围/倾向规则不用动 hook 脚本；不靠前缀硬规则漏判。

### 决策 3（C2，修订）：commit 信息初判 + session context 真凭实据佐证

**判断分两层**：

1. **初判（commit 信息）**：commit message 的前缀 + 内容是最直接的信号——`fix:` 本身就比 `chore:` 更可能是非平凡 bug，message 里的根因描述也给了第一手线索。初判给出"疑似值得记 / 疑似不值得记"的倾向。
2. **佐证（session context 真凭实据）**：初判后，Claude 在当前 session 对话上下文里捞这个 commit 对应的排查过程（试了几条错路 / Google 首条是否答案 / 是否踩坑才得出），用来**证实或推翻初判**。

**为什么两层都要**：commit 信息说明"改了什么 + 可能的根因"，但无法说明"排查过程有多难"（试了几条错路、Google 没救）；session context 有排查深度，但单看 context 不知道哪个 commit 对应哪段排查。commit 信息初判定位嫌疑、context 佐证深度，两者一致才判达标——只靠任一一边都不够。

**完整判断逻辑**：
1. 收到 hook 提醒 → Claude 用 commit message 前缀 + 内容**初判**（结合决策 2 的默认倾向）。
2. 在当前 session context 里捞此 commit 对应的排查过程做**佐证**：
   - 初判"值得记" **且** context 有真凭实据（如试了 ≥2 错路）→ 达标 → 预览分析给用户："commit abc1234 初判非平凡 bug，佐证：[context 捞出的排查依据 + 门槛对照]。是否记录到 vault？" → 用户确认 → 走 bug 记录流程（dedup → 收集字段 → 创建）。
   - 初判"值得记" **但** context 查无对应排查（commit 的是 session 前改好的代码 / 排查在别的 session）→ 如实告知用户"初判疑似值得记，但当前 session 无此 commit 的排查上下文佐证，无法确认深度，需你补充排查过程或确认是否记录"。**不靠初判直接落定**。
   - 初判"不值得记" **且** context 无推翻性证据 → 告知跳过理由（如"diff 仅 1 行 typo，context 无排查过程"），不写。
   - 初判"不值得记" **但** context 有真凭实据推翻（如 `docs:` 踩了 schema 坑）→ 推翻初判，判达标、问用户。
3. 符合用户红线"对外通信先预览"——写 vault 是写入 Obsidian，算对外。不静默跳过：无论达标与否，判断结论 + 初判/佐证依据都要让用户看见。

## 组件

### 1. hook 脚本 `hooks/on-git-commit.sh`

职责单一：解析 stdin JSON → 提取 commit message/hash → 跑 `git diff --name-only HEAD~1` 取文件列表 → 拼 additionalContext JSON 输出。不做任何门槛判断（判断全在 Claude）。

输入（stdin JSON）关键字段：
- `tool_input.command`：如 `git commit -m "fix: handle rate limit window reset"`
- `tool_response.stdout`：如 `[main abc1234] fix: ...\n 3 files changed, 10 insertions(+)`

输出（stdout JSON）：
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "git commit landed: abc1234\nmessage: fix: handle rate limit window reset\nfiles:\n- src/RateLimitGate.php\n- tests/RateLimitTest.php\n\nIf this fixes a non-trivial bug, evaluate against vault bug threshold and ask the user before recording."
  }
}
```

边界处理：
- 非 commit 的 Bash（`if` 已过滤，但 hook 内仍做一道 `case "$cmd" in *git\ commit*)` 保险）→ exit 0 无输出。
- 解析失败 → exit 0 无输出（不阻塞 commit）。
- `HEAD~1` 不可用（首次 commit / 无父）→ 文件列表留空，只带 message + hash。
- 文件列表超长（>50 文件）→ 截断 + `... (N more)`，防超 10k 上限。

### 2. plugin.json 注册 hook

在 `hooks` 下加 `PostToolUse`：

```json
{
  "hooks": {
    "Stop": [ { "hooks": [ { "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/vault-stop.sh" } ] } ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "if": "Bash(git commit*)",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/on-git-commit.sh"
          }
        ]
      }
    ]
  }
}
```

### 3. SKILL.md 增 Flow D — Commit-triggered bug evaluation

新增一节，定义 Claude 收到 additionalContext 后的行为。**commit 信息初判 + session context 真凭实据佐证，两层一致才判达标**：

1. **初判（commit 信息）**：解析 message 前缀 + 内容，结合默认倾向（决策 2）给出"疑似值得记 / 疑似不值得记"。
2. **佐证（session context）**：在当前 session 对话上下文里捞此 commit 对应的排查过程（试了几条错路 / Google 首条是否答案 / 是否踩坑才得出），证实或推翻初判：
   - 初判"值得记" 且 context 有真凭实据（如试了 ≥2 错路）→ 达标 → 预览分析给用户："commit abc1234 初判非平凡 bug，佐证：[context 排查依据 + 门槛对照]。是否记录到 vault？" → 用户确认 → 走 bug 记录流程（dedup → 收集字段 → 创建，Claude 在当前回合程序化执行）。
   - 初判"值得记" 但 context 查无对应排查（session 前改好的代码 / 排查在别的 session）→ 如实告知用户"初判疑似值得记，但当前 session 无此 commit 的排查上下文佐证，无法确认深度，需你补充排查过程或确认是否记录"。**不靠初判直接落定**。
   - 初判"不值得记" 且 context 无推翻性证据 → 告知跳过理由（如"diff 仅 1 行 typo，context 无排查过程"），不写。
   - 初判"不值得记" 但 context 有真凭实据推翻（如 `docs:` 踩了 schema 坑）→ 推翻初判，判达标、问用户。
3. **不静默跳过**：无论达标与否，判断结论 + 初判/佐证依据都要让用户看见。

## 数据流

```
用户/Claude 运行 git commit
  → PostToolUse:Bash 触发 (if:"Bash(git commit*)" 已在 hook 层过滤)
  → on-git-commit.sh 读 stdin JSON
    → 提取 command, stdout(commit hash, message)
    → git diff --name-only HEAD~1 → 文件列表
    → 输出 additionalContext JSON
  → Claude 收到系统提醒（在工具结果旁）
  → Claude 初判：解析 commit message 前缀+内容 → 疑似值得记 / 疑似不值得记
  → Claude 佐证：在当前 session context 捞此 commit 的排查过程
    ├─ 初判值得记 + context 无佐证 → 告知用户"无上下文佐证，需补充"，不靠初判落定
    ├─ 初判不值得记 + context 无推翻 → 告知跳过理由
    ├─ 初判不值得记 + context 有推翻证据 → 推翻初判，问用户
    └─ 初判值得记 + context 有真凭实据 → 预览分析（附初判+佐证）+ 问用户
         → 确认 → bug 记录流程 (dedup → 收集字段 → 创建)
         → 拒绝 → 不写
```

## 错误处理

- hook 脚本异常绝不阻塞 commit（PostToolUse 本就在 commit 完成后触发，无法阻塞；但也要保证 exit 0 + 无输出，避免噪音）。
- `git diff --name-only` 失败（非 git 仓库 / HEAD~1 不存在）→ 文件列表置空，继续输出 message + hash。
- additionalContext 超 10k → 截断文件列表 + 标注截断。
- Claude 判断环节如果当前 session context 里没有此 commit 的排查过程（commit 的是 session 前改好的代码 / 排查在别的 session）→ 初判仍可由 commit message 给出倾向，但**缺佐证无法落定**，如实告知用户"初判疑似[值得记/不值得记]，但当前 session 无此 commit 的排查上下文佐证，无法确认深度，需你补充排查过程或确认是否记录"。不靠初判直接落定，也不静默归入"不达标"。

## 测试

- hook 脚本：构造模拟 stdin JSON（含/不含 commit message、首次 commit 无 HEAD~1、超长文件列表）→ 验证输出 JSON 正确、边界处理正确、非 commit 命令静默。
- 端到端（初判 + 佐证）：
  - **初判值得记 + 佐证成立**：`fix:` commit，session 里刚排查了对应 bug（试了 2 条错路）→ Claude 收到提醒 → 初判值得记 → context 捞出排查依据佐证 → 预览 + 问用户 → 确认 → 记录。
  - **初判不值得记 + 无推翻**：`chore:` 改个 typo，context 无排查过程 → Claude 告知"初判不值得记，无推翻证据"，不写。
  - **初判值得记 + 无佐证**：`fix:` commit，但 commit 的是 session 前改好的代码、session 无对应排查 → Claude 告知"初判疑似值得记，但当前 session 无排查上下文佐证，无法确认深度，需你补充或确认"，不靠初判落定。
  - **初判被推翻**：`docs:` commit（初判不值得记），但 context 里有踩 schema 坑的排查过程 → 推翻初判，判达标、问用户。
- 回归：非 commit 的 Bash（`ls`/`git status`）不触发 hook、无提醒注入。

## 范围与非目标

- **非目标**：不自动写 vault（仍需用户确认）。不处理 `git commit -m` 之外的提交方式（`git commit -F file`、IDE 提交）——这些不走 Bash 工具，hook 不触发，超出范围。
- **范围**：只覆盖 Claude Code session 内通过 Bash 工具的 git commit。用户在终端直接 `git commit` 不触发（那是 Claude Code 之外）。

## 关联

- 依赖现有：`/vault-log bug`（Flow B）、vault bug 门槛定义（SKILL.md）。
- 不替代 Stop hook：daily 生成仍靠 Stop hook（本设计只管 bug 记录触发，daily 另算）。
