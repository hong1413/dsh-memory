# 提交到 DSH 插件市场（awesome-dsh-plugin）

收录规则来自策展仓库的 [`contributing.md`](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin/blob/main/contributing.md)，
**投稿不是改 README，而是新增一个 YAML 文件**：

```
data/plugins/hong1413__dsh-memory.yml
```

两个 README 由脚本从 `data/plugins/*.yml` 生成，手工改 README 会被打回。
一个 PR 最多 3 条。

---

## 一、本仓库的达标情况

| CI / 评审要求 | 状态 |
|---|---|
| `package.json` 声明 `dsh.bundle`（只有 `dsh.client` 会失败） | ✅ `dsh.bundle.patch: ./cordis.patch.yml` + 根目录有该 patch |
| 仓库创建满 1 天 | ✅ 2026-06-02 创建 |
| 有真实可用代码 | ✅ 5 个工具 + 3D 星图 + 自带 16 项自检的 Python 引擎 |
| 仓库有 `dsh-plugin` topic | ⚠️ **需要你在 Settings → Topics 里加** |
| 图标是 GitHub 托管的**位图**（SVG 会被拒） | ✅ 已提供 [icon.png](icon.png)（256×256，9 KB） |
| 截图（可选，推荐） | ✅ 仓库根有 [screenshots.json](screenshots.json)，市场详情页会展示 3 张 |

> ⚠️ 上传 `icon.png` 与 `screenshots.json` 之前，图标链接是 404，PR 会被 CI 拦。

---

## 二、要提交的 YAML（原样复制）

文件路径：`data/plugins/hong1413__dsh-memory.yml`

```yaml
url: https://github.com/hong1413/dsh-memory
name: hong1413/dsh-memory
category: memory
description:
  en: 'Long-term experience memory for any LLM in DeepSeek Harness: five model tools (search, get, status, save, feedback) plus an interactive 3D star map where pending proposals appear as clickable meteors. Writes go through the approval service; the engine is a bundled Python CLI.'
  zh: '给 DeepSeek Harness 里的任意大模型装上跨会话记忆：五个工具（检索/读原文/概览/沉淀/回写效果），外加一张可交互的 3D 星图，待批提案化作可点的流星。写入走审批服务；引擎是随包内置的 Python CLI。'
icon: https://raw.githubusercontent.com/hong1413/dsh-memory/main/icon.png
```

两个格式注意点（来自投稿指南）：

- 描述里出现 `: `（冒号+空格）**必须加引号**，否则 YAML 会解析成嵌套键——上面英文已用单引号包住。
- `url` 必须与仓库地址完全一致；`name` 是列表里显示的链接文字。

描述只陈述事实、不加营销词；评审会拿代码核对（我们写的"五个工具""3D 星图""写入走审批"都与代码一致）。

---

## 三、提交流程

1. 确认 `icon.png` 已上传：<https://raw.githubusercontent.com/hong1413/dsh-memory/main/icon.png> 能打开
2. 给仓库加 topic：仓库首页 → ⚙️ → **Topics** → 填 `dsh-plugin`
3. Fork <https://github.com/awesome-dsh-plugin/awesome-dsh-plugin>
4. 在 fork 里新建文件 `data/plugins/hong1413__dsh-memory.yml`，内容同上
5. 提 PR：

```markdown
Title: Add dsh-memory — long-term memory + 3D star map for DSH

Body:
Adds `hong1413/dsh-memory`, a Cordis bundle that gives any model in DSH cross-session experience memory.

- 5 tools: memory_search / memory_get / memory_status / memory_save / memory_feedback
- Writes go through the approval service and fail closed; conflicting lessons are kept as a pair
- Settings page renders an interactive 3D star map (Canvas, no npm dependencies)
- Pending proposals appear as clickable meteors; approve/reject without leaving the UI
- Engine is a bundled single-file Python CLI (3.8+) with a 16-assertion self-test
- Data stays in a local JSON directory; no network calls

`dsh.bundle` is declared in package.json and `cordis.patch.yml` sits at the repo root.
Repo: https://github.com/hong1413/dsh-memory
```

6. 等 CI：条目数 ≤3、`dsh.bundle`、仓库年龄、`awesome-lint` 与站点构建；失败会指明改哪行，推同一分支即可。
7. 合并后站点与市场会自动重建，页面是
   `https://awesome-dsh-plugin.com/p/hong1413/dsh-memory/`

---

## 四、可选：先在市场里看到自己（自定义索引源）

不必等收录，市场的 Discover 支持添加索引源：

1. 仓库根已有 `plugins.json`（本仓库自测用索引）
2. **插件 → 插件市场 → 发现 → 索引源**，添加
   `https://raw.githubusercontent.com/hong1413/dsh-memory/main/plugins.json`
3. 刷新后搜 `dsh-memory`

---

## 五、后续更新条目

只改**你自己那一个** `data/plugins/hong1413__dsh-memory.yml`，再提 PR；不要手工改 README。
描述需要同步时（例如新增了工具），记得让文字与代码保持一致。
