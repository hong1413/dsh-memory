# 发布到 GitHub（两种方式，任选一种）

仓库内容已经准备好，本目录就是仓库根目录。下面两种方式都不需要改任何代码。

---

## 方式 A：网页上传（不需要 git）

1. 打开 <https://github.com/new>
   - **Repository name**：`dsh-memory`
   - **Description**：`Long-term experience memory for any LLM in DeepSeek Harness — 5 tools + interactive 3D star map`
   - 选 **Public**，不要勾选 "Add a README file"（仓库里已经有）
2. 创建后进入仓库 → **Add file → Upload files**
3. 把本目录**里面的所有文件和文件夹**拖进去（`index.js`、`client.js`、`vendor/`、`locale/`、`screenshots/`、`README.md`、`LICENSE` …）
4. Commit message 填 `feat: dsh-memory 1.2.0`，点 **Commit changes**
5. 打标签（可选）：**Releases → Draft a new release** → Tag `v1.2.0` → 标题 `dsh-memory v1.2.0` → 正文见文末
6. 设置仓库话题（可选但推荐）：**Settings → Topics**
   `deepseek-harness` `cordis-plugin` `llm-memory` `agent-memory` `3d-visualization` `plugin`

> 网页上传会把 `.gitignore`、`publish.ps1` 也一起传上去，没问题。

---

## 方式 B：命令行（需要 git）

```powershell
# 1. 装 git（已装可跳过），装完重开终端
winget install --id Git.Git -e

# 2. 在 GitHub 建空仓库（Public，不要初始化 README），拿到地址：
#    https://github.com/hong1413/-.git

# 3. 一键发布
cd <本目录>
.\publish.ps1 -RepoUrl https://github.com/hong1413/-.git -Message "feat: dsh-memory 1.2.0"
```

首次 push 会弹浏览器登录 GitHub。

手动等价命令：

```bash
git init
git add -A
git commit -m "feat: dsh-memory 1.2.0"
git branch -M main
git remote add origin https://github.com/hong1413/-.git
git push -u origin main
```

---

## 发布后：把安装链接写进 README（可选）

README 里的克隆地址现在是占位符 `hong1413`。推送后把它替换成真实地址：

```
https://github.com/hong1413/-
```

---

## Release 正文模板（v1.2.0）

```markdown
## dsh-memory v1.2.0

Long-term experience memory for any LLM in DeepSeek Harness.

### Highlights
- 5 model tools: search / get / status / save / feedback
- Interactive 3D star map: nebulae → clusters → stars, orbit camera, click-to-focus
- Pending proposals fall as clickable meteors; approve or reject without leaving the UI
- Human-in-the-loop writes; conflicts kept as pairs; maturity driven only by feedback

### Install
```bash
git clone https://github.com/hong1413/-.git
```
Then in DeepSeek Harness:
`plugin_manager install_bundle target=<path to dsh-memory>`
Restart the app once so the star map page loads.

### Requirements
DeepSeek Harness with plugin support · Python 3.8+ · no npm dependencies

### License
MIT
```

---

## 检查清单

- [ ] 仓库为 Public
- [ ] 首页能看到 `screenshots/starmap-3d.png`（README 第一屏）
- [ ] Releases 有 `v1.2.0` 标签与正文
- [ ] Topics 已填
- [ ] 安装命令里的路径示例正确
