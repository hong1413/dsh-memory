# 提交到 DSH 插件市场（awesome-dsh-plugin）

插件市场（Plugins → Discover）的目录**不在市场应用仓库里**，而是来自策展仓库
[`awesome-dsh-plugin/awesome-dsh-plugin`](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)。
被收录后，市场里能搜到、能一键安装，站点会生成插件页
`https://awesome-dsh-plugin.com/p/hong1413/dsh-memory/`（slug = owner/仓库名）。

> 仓库：<https://github.com/hong1413/dsh-memory>（Public，默认分支 `main`，扁平布局）。
> 安装简写：`github:hong1413/dsh-memory`。

> 市场限制：只允许安装注册表里登记的来源，所以**先收录、才能一键装**。
> 收录前可以用 README 里的 `install_bundle`（本地目录）或
> `dsh plugin --profile desktop add github:hong1413/dsh-memory` 手动安装，两者都可用。

---

## 一、提交前：仓库必须满足的条件

| 条件 | 状态 |
|---|---|
| 公开 GitHub 仓库，根目录就是插件包（含 `package.json` 的 `dsh.bundle.patch`） | ✅ 已就绪 |
| `package.json` 声明 `engines.dsh`（市场用它做兼容过滤） | ✅ `>=0.2.1-rc.5`（本机实测版本） |
| 有 README（中英双语）、LICENSE、截图 | ✅ |
| 运行时不依赖私有服务 / 硬编码个人路径 | ✅（数据目录可用 `dataDir` 配置） |
| 记录插件会用到哪些能力（capabilities） | 见下方条目 |

本插件声明的能力：`shell`（启动本机 python 子进程）、`fs-write`（写记忆库 JSON）、`fs-read`。
不联网、不读环境密钥、不启用子代理。

---

## 二、要提交的条目（两种写法，按仓库当时格式选一种）

下面已按 `hong1413/dsh-memory` 填好；如果你把仓库改名成 `dsh-memory`，把 `hong1413/dsh-memory` 全局替换即可。

### JSON

```json
{
  "name": "dsh-memory",
  "owner": "hong1413",
  "url": "https://github.com/hong1413/dsh-memory",
  "category": "memory",
  "description": {
    "en": "Long-term experience memory for any LLM in DeepSeek Harness: five model tools (search / get / status / save / feedback) plus an interactive 3D star map where zones are nebulae, subzones are clusters, units are stars, and pending proposals fall as clickable meteors. Writes go through the approval service and fail closed; conflicts are kept as pairs; maturity moves only on explicit feedback. No npm dependencies, Python 3.8+ only.",
    "zh": "给 DeepSeek Harness 里任意大模型装上跨会话记忆：五个工具（检索/读原文/概览/沉淀/回写效果），外带一张可交互的 3D 星图——大区是星云、小区是星团、每条经验是一颗星，待批提案化作可点的流星。写入走审批服务、失败关闭；冲突经验成对保留；成熟度只由显式回写推进。无 npm 依赖，只要 Python 3.8+。"
  },
  "npm": null,
  "install": "dsh plugin --profile desktop add github:hong1413/dsh-memory",
  "capabilities": ["shell", "fs-write", "fs-read"],
  "screenshots": [
    "https://raw.githubusercontent.com/hong1413/dsh-memory/HEAD/starmap-3d.png",
    "https://raw.githubusercontent.com/hong1413/dsh-memory/HEAD/starmap-empty.png"
  ],
  "icon": "https://raw.githubusercontent.com/hong1413/dsh-memory/HEAD/icon.svg"
}
```

### YAML

```yaml
- name: dsh-memory
  owner: hong1413
  url: https://github.com/hong1413/dsh-memory
  category: memory
  description:
    en: >-
      Long-term experience memory for any LLM in DeepSeek Harness: five model tools
      (search / get / status / save / feedback) plus an interactive 3D star map where zones
      are nebulae, subzones are clusters, units are stars, and pending proposals fall as
      clickable meteors. Writes go through the approval service and fail closed; conflicts are
      kept as pairs; maturity moves only on explicit feedback. No npm dependencies, Python 3.8+ only.
    zh: >-
      给 DeepSeek Harness 里任意大模型装上跨会话记忆：五个工具（检索/读原文/概览/沉淀/回写效果），
      外带一张可交互的 3D 星图——大区是星云、小区是星团、每条经验是一颗星，待批提案化作可点的流星。
      写入走审批服务、失败关闭；冲突经验成对保留；成熟度只由显式回写推进。无 npm 依赖，只要 Python 3.8+。
  npm: null
  install: dsh plugin --profile desktop add github:hong1413/dsh-memory
  capabilities: [shell, fs-write, fs-read]
  screenshots:
    - https://raw.githubusercontent.com/hong1413/dsh-memory/HEAD/starmap-3d.png
    - https://raw.githubusercontent.com/hong1413/dsh-memory/HEAD/starmap-empty.png
  icon: https://raw.githubusercontent.com/hong1413/dsh-memory/HEAD/icon.svg
```

`category` 用注册表里已有的 **`memory`（记忆）** 分类，和本插件最贴。

---

## 三、提交流程

1. 先把仓库推到 GitHub（见 `PUBLISH.md`），确认 `https://github.com/hong1413/dsh-memory` 可匿名访问。
2. Fork + 克隆 `https://github.com/awesome-dsh-plugin/awesome-dsh-plugin`。
3. 在读到的插件清单文件里按既有格式**追加一条**（就是上面那份条目；字段名以仓库当前文件为准）。
4. 提交 PR，标题与正文参考：

```markdown
Title: Add dsh-memory — long-term memory + 3D star map for DSH

Body:
Adds `dsh-memory`, a Cordis bundle that gives any model in DSH cross-session experience memory.

- 5 tools: memory_search / memory_get / memory_status / memory_save / memory_feedback
- Writes go through the approval service and fail closed; conflicts are kept as pairs
- Settings page renders an interactive 3D star map (Canvas, no npm deps)
- Pending proposals appear as clickable meteors; approve/reject is one click
- Engine is a bundled single-file Python CLI (3.8+) with a 16-assertion self-test
- No npm dependencies, no build step, data stays in a local JSON directory

Category: memory
Capabilities: shell, fs-write, fs-read
Requires: DSH >= 0.2.1-rc.5 (declared via engines.dsh), Python 3.8+

Repo: https://github.com/hong1413/dsh-memory
Screenshots: starmap-3d.png, starmap-empty.png
```

5. 等合并（README 说通常一天内站点与市场同步）。合并后检查：
   - `https://awesome-dsh-plugin.com/p/hong1413/dsh-memory/` 能打开
   - 市场 Discover 里搜 `dsh-memory` 能搜到
   - 卡片显示 DSH 版本要求、截图、能力标签
   - 一键安装走的是 `github:hong1413/dsh-memory`

---

## 四、可选：发布到 npm，换"一键秒装"

市场优先用 npm 包安装（`dsh plugin --profile desktop add dsh-memory`），比 Git 源码安装快很多。
需要你自己的 npm 账号：

```bash
npm login
cd dist/dsh-memory        # 或仓库根目录
npm publish               # 包名 dsh-memory，先确认没被占用
```

发布成功后，把条目里的 `npm` 改成 `"dsh-memory"`、`install` 改成
`dsh plugin --profile desktop add dsh-memory`，并在 PR 里说明两个渠道都可用。

> 若 npm 上 `dsh-memory` 这个名字已被占用，改用 `@<你的scope>/dsh-memory`，
> 同时把 `package.json` 的 `name` 与 `cordis.patch.yml` 里的 `name` 一起改掉。

---

## 五、可选：先在市场里看到自己（自定义索引源）

市场的 Discover 支持添加自定义索引源，因此不必等收录，就能验证"卡片长什么样"：

1. 把仓库推到 GitHub 后，`plugins.json` 就变成可访问的索引文件（记得把 `hong1413` 换成你的用户名）
2. 打开 **插件 → 插件市场 → 发现 → 索引源（Index sources）**
3. 添加 `https://raw.githubusercontent.com/hong1413/dsh-memory/main/plugins.json`
   （国内网络可换 `https://cdn.jsdelivr.net/gh/hong1413/dsh-memory@main/plugins.json`）
4. 刷新后搜 `dsh-memory`

`plugins.json` 是按官方 `plugins.json` 的结构写的自测索引；如果某个版本的市场对索引
字段有额外要求，以官方 catalog 为准调整即可。

---

## 六、本地先"提前体验"市场效果

不等收录也能让别人装：

```bash
# 任意机器，任意 profile
dsh plugin --profile <profile> add github:hong1413/dsh-memory
```

装完刷新页面；3D 星图是客户端插件，**首次装完要重启一次应用**。
