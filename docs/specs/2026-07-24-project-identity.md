# 产品规格：Project Identity（召回对齐）

日期：2026-07-24  
状态：草案（待实现）  
动机：多仓库 / 多语言日常下，仅用 `cwd` 目录名当 `project` 会导致 journal=`kpi`、memory=`talent-kpi` 这类漂移，召回按 project 过滤时对不齐。  
约束：**不依赖用户记忆命名习惯**；系统推导稳定身份，显示名可友好。

---

## 1. 产品原则

1. **稳定键 vs 显示名分离**  
   - `project_key`：机器稳定 ID，用于匹配、去重、召回过滤  
   - `project_label`：给人看的短名（可变，不影响匹配）
2. **同一工作区多种叫法应合并**  
   目录名、包名、Claude 随口写的 scope，都只是 alias，不能各自变成孤岛。
3. **零配置默认可用**  
   用户不配「项目词典」也能工作；可选覆盖给高级用户。
4. **跨仓关联是一等场景**  
   例如 `talent-kpi`（PHP）与 `fe-pc-kpi`（Vue）常一起改——默认可分 key，但支持「工作区组 / workspace」做联合召回。

---

## 2. 身份如何推导（优先级从高到低）

Hook / MCP 在写 journal 或 memory 时解析 `cwd`（或显式 override）：

| 优先级 | 信号 | `project_key` 规则 |
|---|---|---|
| 1 | Git `origin` remote URL | 规范化后 SHA256 前 16 位，或 `host/path` 去 `.git` 的 slug（产品实现选一种并固定） |
| 2 | 最近 git 根目录的真实路径 | `sha256(realpath(git_root))[:16]` |
| 3 | 语言清单文件名 | 如 `composer.json` `name`、`package.json` `name`、`go.mod` module——作 **label 候选**，也可作 key 的辅信号 |
| 4 | `cwd` basename | 仅 fallback；同时记入 aliases |

规范化 remote 示例：

- `git@github.com:org/talent-kpi.git` → `github.com/org/talent-kpi`
- `https://github.com/org/talent-kpi.git` → 同上

**禁止**把未规范化的 cwd basename 当作唯一权威 key（当前 MVP 的问题根源）。

---

## 3. 数据模型

### 3.1 事件 / 记忆字段（增量，兼容旧数据）

Journal / Memory 增加（或并行保留旧 `project`）：

```yaml
project_key: "a1b2c3d4e5f67890"   # 稳定
project_label: "talent-kpi"       # 显示
project_aliases: ["kpi", "talent-kpi"]  # 可选快照
```

过渡期：

- 读写仍接受旧字段 `project` / `scope.project`  
- 写入时若只有旧字段：按 label 查 registry，能解析则补上 `project_key`  
- 召回过滤：`project_key` 优先；否则用 alias 集合匹配

### 3.2 项目注册表（可重建索引，非业务权威）

路径：`$MEMORANT_ROOT/.memorant/projects.json`（删除不丢 journal/memory 正文，可扫描重建）

```json
{
  "projects": {
    "a1b2c3d4e5f67890": {
      "label": "talent-kpi",
      "aliases": ["kpi", "talent-kpi", "php/kpi"],
      "signals": {
        "git_remote": "github.com/org/talent-kpi",
        "git_root": "/Users/tal/projects/php/kpi"
      },
      "workspace_ids": ["ws_kpi"]
    }
  },
  "workspaces": {
    "ws_kpi": {
      "label": "KPI 产品线",
      "project_keys": ["…talent-kpi…", "…fe-pc-kpi…"]
    }
  }
}
```

规则：

- 首次见到新 `project_key`：自动注册，`label` 取 basename 或 package name  
- 同一 `project_key` 下出现新 basename / 显式 project 字符串：自动 append alias（有上限，防污染）  
- Workspace 默认不自动乱建；可用启发式：同 remote org + 名称前缀，或后续 MCP `memorant_link_projects`（可选）

---

## 4. 召回行为（产品体验）

`memorant_recall(project=?)` / SessionStart 注入：

1. 把入参 `project`（无论是 key、label 还是 alias）解析成 `project_key` 集合 S  
2. 若该 key 属于某个 workspace，可选扩大为 workspace 内全部 keys（SessionStart 默认扩大；精确调试可关）  
3. 匹配 memory：`scope.project_key ∈ S` **或** `scope.project` / aliases 与 S 的 label/alias 相交  
4. Legacy bugs/snippets/arch：同样经 registry 解析 frontmatter `project` 再匹配

这样用户/模型写 `kpi` 或 `talent-kpi` 都能打到同一项目经验。

---

## 5. Distill 约束（与 P0 一并）

Flow E / `memorant_write_memory`：

- 必须带 `project_key`（服务端可从 `source_event_ids` 反查 journal 补全）  
- `project_label` 可与事件不一致，但 **key 必须与源事件一致**  
- 禁止只写自由文本 project 却丢掉 key

---

## 6. 非目标（本规格不做）

- 强制用户维护命名表  
- 用向量相似度猜「是不是同一个项目」  
- 云端统一项目 ID  
- 自动把所有同名文件夹合并（误伤高）

---

## 7. 验收标准

1. 在 `…/php/kpi` 与仅 basename `kpi` 的 hook 写入，生成相同 `project_key`（若 git root/remote 相同）  
2. Memory 误写 `talent-kpi` label、journal 为 `kpi`：召回 `project=kpi` 仍能命中（经 alias / 同源 key）  
3. 删除 `.memorant/projects.json` 后，扫描 journal+memory 可重建 aliases，不丢业务 md  
4. 无 git 的临时目录仍能 fallback，不炸 hook（fail-open）  
5. 旧数据无 `project_key` 时召回不崩，行为不低于今天

---

## 8. 实现切片（建议）

1. `project_identity.py`：resolve(cwd) → key/label/aliases；stdlib-safe 供 hook 使用  
2. Journal schema + hook_cli 写入新字段  
3. Registry 读写 + 重建  
4. `memorant_write_memory` / recall 解析与匹配  
5. Skill：禁止「只改 label 不继承事件 key」  
6. 迁移：一次性 backfill 扫描现有 Memorant 根

与验收 backlog P0「project 对齐」合并为本规格，不再写成「用户命名规范」。
