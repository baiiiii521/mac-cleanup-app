# Codex 提示词 · Mac 清理助手（mac-cleanup-app）

> 用法：把下面 **从"你是…"开始到最后一行的整段**复制给 Codex（或用 `codex` 打开
> `/Users/bai/Developer/mac-cleanup-app` 后直接把它作为第一条消息）。
> 先让它读 `HANDOVER.md`（仓库里已有），再按里面的任务清单推进。

---

你是接手 **Mac 清理助手** 的资深 macOS 前端 + 图形工程师。这个项目已经能用了，你的任务不是重写，
而是在**不破坏既有契约**的前提下把它做得更深、更稳、更好看。

## 仓库与必读顺序

仓库：`/Users/bai/Developer/mac-cleanup-app`（git 仓库，主分支 `main`）
按这个顺序读，读完再动手：

1. `HANDOVER.md` —— 交接文档：目录地图、引擎接口契约、安全链、构建与验证命令、已踩过的坑、当前待办
2. `PRODUCT.md` —— 产品事实（用户是谁、确认模型、不能破的约束）
3. `DESIGN.md` —— 视觉系统（世界、token、字体、材料、签名动作、组件、状态、无障碍）
4. `app/index.html` —— 你要改的主界面（单文件，1194 行）
5. `native/main.swift` + `app/server.py` —— 只读，理解 App 怎么把引擎拉起来、界面怎么跟引擎说话
6. 设计标准（这个项目用 Impeccable 的工程质量地板）：
   - `~/.hermes/skills/impeccable/reference/craft-floor.md`（**必须先读**：质量地板与明令禁止项）
   - `~/.hermes/skills/impeccable/reference/mode-operate.md`（工具界面的规矩）

## 硬约束（违反任何一条都算没完成）

1. **功能与安全链一个字都不许动**：扫描只读；勾选**只做交集**（只能收窄范围，绝不扩大）；
   预览不删任何东西；执行＝移到废纸篓（可逆）；**只有「清空废纸篓」不可逆**。
   确认成本与不可逆程度挂钩：可逆的操作一次点击完成，不可逆的必须手打确认语。
   后端契约：`POST /api/clean` 在 `mode:"apply"` 时必须带 `confirm:"确认清理"`（由界面在用户点击确认后填入）。
2. **清理规则只存在一处**：`~/.hermes/skills/devops/macos-deep-optimize/scripts/{scan,report,clean,judge}.py`。
   不要把规则复制进 UI，也不要另写一份判断逻辑。要改规则 → 改技能脚本。
3. **`app/index.html` 必须保持单文件、零第三方依赖、完全离线可用**：不引 CDN、不引外部字体、不引框架；
   所有图形用 canvas / inline SVG 自己画。
4. **键盘可达**（Tab 走完全部操作、`:focus-visible` 焦点环可见）、**正文对比度 ≥4.5:1**（大字号 ≥3:1）、
   **`prefers-reduced-motion: reduce` 下关掉粒子/光柱/自转/过渡**（但状态变化要保留）。
5. **世界已经定稿**：档案馆 · 除尘与退役（v3 是「档案鼓」）。要改视觉就按 Impeccable 流程走
   （见下），不要凭手感另起一套。Operate 模式下世界只能提供**字体、调色、密度、一个签名动作**——
   布局、导航、控件必须是标准件，任何一个控件读不出是"按钮/复选框/输入框"都算失败。
6. **不要为了炫技牺牲"第一次看就懂"**：这个产品的用户包括偶尔借用电脑的人。

## 定义完成（每条都必须给证据，不能只写"已完成"）

```bash
cd /Users/bai/Developer/mac-cleanup-app

# 构建 + 安装（swiftc，几秒）
bash native/build_native.sh && open -a "$HOME/Applications/Mac 清理助手.app"

# 功能自检：10 项必须全过（含"确认语打错被拒且什么都没删"）
"$HOME/Applications/Mac 清理助手.app/Contents/MacOS/MacCleanup" --selftest

# 机械设计检查：必须 0 条
~/.hermes/skills/impeccable/scripts/impeccable detect --json app/index.html

# JS 语法体检
python3 -c "import re;open('/tmp/c.js','w').write(re.search(r'<script>(.*)</script>',open('app/index.html',encoding='utf-8').read(),re.S).group(1))" && node --check /tmp/c.js

# 单独起引擎改 UI（省得和 App 自带的那份互相干扰）
MAC_CLEAN_OPEN=0 MAC_CLEAN_PORT=58901 /usr/bin/python3 app/server.py   # 浏览器开 http://127.0.0.1:58901/
```

- **两个视口各拍一次整页截图**（1440 宽、390 宽），存进 `.impeccable/review/`
  （沿用 `desktop.png` / `mobile.png`，需要时再加特写，例如 `drum-closeup.png`）。
- 关键交互要**实测请求体**，不能靠"应该会"：用浏览器注入一层 `fetch` 拦截，确认
  · 单条清理只带那一条 path · 批量清理只带勾选项 · 确认语错时**没有发生任何删除**。
- 有动画的表现（鼓的液位、粒子、跳格）在无头浏览器里帧率会被压到 1Hz 以下，**截图会骗你**：
  用 `http://127.0.0.1:58901/?debug=1` 暴露的 `window.__drum.force(0.661)` 把状态钉死后再截图；
  动画本身必须**按时间推进**（`ease(k)=1-exp(-k·dt)`），不允许纯按帧推进。
- 收尾时让 `DESIGN.md` 与 `.impeccable/design.json` 反映**实际构建出来的东西**（不是计划里的东西）。

## 工作方式（小循环，别一口气改完再验）

1. 改一处 → `detect` + `node --check` → 起引擎 → 同一批截图 → 自己先挑毛病 → 修完再看**一次**就收。
   不要无休止自审；两轮是上限。
2. 每次提交前跑一遍 `--selftest`，把结果写进 commit message。
3. 视觉大改（换世界/加新表面）才走完整流程：
   `impeccable concept-seed --scope direction --mode operate` → 把结果做成决策页让用户选 →
   方向契约写进 `.impeccable/surfaces/app-index-html.md` → 再动代码。
4. 完工后派一个**全新上下文**的评审子代理（读 `~/.hermes/skills/impeccable/reference/degraded/finish-reviewer.md`
   + `craft-floor.md`），给它：原始要求、确认过的答案、制品路径、**两个视口的截图**、方向契约、detector 结果。
   它会给 `ship / fix / rebuild / recapture`；收到 `fix` 就一次修完、重拍同样的视口、交回同一评审复核。

## 具体任务（按优先级；每条都给了验收标准）

### P0 · 接住 v3 的评审裁定
上一版（档案鼓 v3）已派出一份独立完工评审。先把它的裁定处理掉：
- 若 `ship`：把结论写进 `HANDOVER.md` 的"当前待办"，然后做 P1。
- 若 `fix`：按它列的清单**一次修完** → 重建 → 回同样视口重拍 → 交回同一评审复核。
- 若 `rebuild`：按它点名的区域重做，不要打补丁。

### P1 · 鼓的性能与画质上限
`app/index.html` 里 `#drum` 的 canvas 内部固定 904×904，未做 DPR 封顶。
**验收**：`const DPR = Math.min(2, devicePixelRatio)` 并让内部尺寸随之缩放；53 项清单 + 拖动能保持
≥55fps（在页面里用 `requestAnimationFrame` 计时统计 3 秒平均帧间隔，把数字写进 commit message）；
在 1x/2x 屏幕上鼓的线条都不发虚（贴两张截图）。

### P2 · 无障碍：给 3D 仪表一条等价文本
鼓是纯视觉的。给 `.drum-wrap` 加 `role="img"` + 动态 `aria-label`
（内容形如"磁盘已用 66.1%、可写入 40.8 GB；清单 53 项，其中 12 项可清理 4.56 GB"），
并在 `refresh()` 里更新它。**验收**：读屏能读出这句；键盘 Tab 不会掉进 canvas 里。

### P3 · 清理后的"回执"更可信
现在执行完只是把行标成「已退役」。**验收**：执行后那几行显示可核对的结果
（移入废纸篓的项数 + 前后可写入空间 + 一句"要真正释放空间请清空废纸篓"），
并且**只有服务端返回 `ok:true` 时才标记**（失败的行要显式标出失败原因）。
不要新增接口，用现有 `/api/clean` 的返回字段。

### P4 · 窄屏（≤560px）的实操体验
**验收**：390 宽下"勾选 → 单条清理 → 批量清理 → 打确认语清空废纸篓"这四条路径都能一次走通；
所有可点目标 ≥44px；长路径不截断（可换行）；贴一张 390 宽的操作后截图。

### P5 · 顺手清账
- `.impeccable/review/` 里的 `desktop-1x.png` / `mobile-1x.png` 是第一轮的旧图，删掉或明确标注。
- `README.md` 与 `HANDOVER.md` 的命令必须与现状一致（改了什么就同步什么）。

## 不要做的事

- ❌ 不要把清理规则搬进 UI，或让 UI 自己决定删什么
- ❌ 不要引入任何外部资源（字体/CDN/图片托管）
- ❌ 不要让动画挡住读数或让用户必须先看动效才能操作
- ❌ 不要用 emoji 当图标、渐变文字、零偏移彩色光晕、硬偏移阴影、卡片套卡片（detector 会报，craft floor 也禁）
- ❌ 不要在没有截图证据的情况下声称"已完成"；也不要在评审给出 `fix` 后只改一半
- ❌ 不要去改 `~/.hermes/skills/` 下的其他技能，除非任务明确要求

## 交回来给我的格式

1. 一句话结论（做完了什么、现在什么状态）
2. 每条任务：`做了什么 → 怎么验的（命令 + 数字/截图路径） → 结果`
3. `--selftest` 与 `detect` 的原始输出（贴关键的几行）
4. 改了哪些文件、commit hash
5. 还没解决/需要我拍板的（一条条列，附上你的建议）

先读 `HANDOVER.md`，然后告诉我：你打算按什么顺序做，以及 P0 的评审结论是什么。读完再动代码。
