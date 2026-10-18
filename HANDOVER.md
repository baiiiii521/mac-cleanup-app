# 交接文档 · Mac 清理助手（mac-cleanup-app）

> 仓库路径：**`/Users/bai/Developer/mac-cleanup-app`**（git 仓库，主分支 `main`）
> 已安装的应用：**`~/Applications/Mac 清理助手.app`**
> 最近提交：`8172a67`（2026-10-18 01:2x）
> 交接人：Hermes（上一轮完成方） · 接手：Codex

---

## 0. 一句话

把技能 `macos-deep-optimize`（只读扫描 → 三色分级 → 安全清理）封装成**双击就能用的 macOS 应用**：
窗口里是一个单文件 HTML 界面（WKWebView 承载），清理引擎是技能里的 python 脚本，通过本机
`127.0.0.1` 上的私有 HTTP 接口通信。**UI 是这一轮的主要战场，功能与安全锁必须保持原样。**

---

## 1. 目录地图

```
mac-cleanup-app/
├── app/
│   ├── index.html          ★ 主界面（单文件、零第三方依赖、离线可用）——1369 行，改这里
│   ├── index-v2-archive.html  v2「档案抽屉」备份（对照用，不参与运行）
│   ├── index-classic.html     最早的极简版界面（对照用）
│   ├── server.py           ★ 清理引擎的本地服务（602 行）：HTTP 外壳 + 状态机 + 安全闸门
│   ├── skill-scripts/      ★★ 清理引擎（**唯一真理源，已纳入 git**）：scan/report/clean/judge
│   ├── launch              网页版外壳的启动器（shell；把 server.py 丢后台后自己退出）
│   └── Info.plist / AppIcon.png
├── native/
│   ├── main.swift          ★ 原生 App（922 行）：窗口 + WKWebView + 引擎拉起/回收 + --selftest
│   ├── build_native.sh     ★ 构建并安装到 ~/Applications（swiftc，只需 CommandLineTools）
│   └── Info.plist          （含 NSPrincipalClass / NSAllowsLocalNetworking）
├── build.sh                备用：把网页版外壳装成「Mac 清理助手（网页版）.app」
├── tools/make_icon.py      生成图标源图
├── docs/mole-notes/        ★ 借鉴 mole 的规格笔记（protection.md / clean-rules.md）
├── PRODUCT.md              ★ 产品事实（用户、目的、约束、确认模型、安全锁）
├── DESIGN.md               ★ 视觉系统（地面/token/字体/材料/签名动作/组件/状态/无障碍）
├── README.md               面向使用者的说明（含"打不开怎么办"）
└── .impeccable/            Impeccable 流程产物
    ├── surfaces/app-index-html.md   表面 brief（含方向契约六块）
    ├── decision/round1.json         方向决策页的 payload（骰子结果 + 对手牌 + canon）
    ├── decision/brief-body.md       写进 brief 的原始正文
    ├── design.json                  token 化设计清单
    └── review/{desktop,mobile,drum-closeup,card-stamp}.png   评审截图（1440 / 390 / 鼓特写 / 单卡）
```

**引擎规则只在一处**：本仓库 **`app/skill-scripts/`**（已纳入 git，是唯一真理源）。
运行时优先读技能目录 `~/.hermes/skills/devops/macos-deep-optimize/scripts/`
（`MAC_CLEAN_SKILL` 可覆盖）；构建时 `native/build_native.sh` 会把
`app/skill-scripts/*.py` 拷进 `.app/Contents/Resources/skill-scripts/` 作兜底。
**改规则请改 `app/skill-scripts/`，然后同步到技能目录**（否则 App 跑的还是旧脚本）：

```bash
cp app/skill-scripts/{scan,report,clean,judge}.py \
   ~/.hermes/skills/devops/macos-deep-optimize/scripts/
```

---

## 2. 三个形态（同一引擎）

| 形态 | 入口 | 说明 |
|---|---|---|
| **原生 App（默认）** | `~/Applications/Mac 清理助手.app` | `native/main.swift`：真窗口 + WKWebView 显示 `app/index.html`。菜单有「重新载入界面 / 在浏览器中打开界面 / 退出」。加 `--classic` 参数可回退到 AppKit 原生控件版界面；加 `--selftest` 跑无界面自检 |
| 网页版外壳 | `bash build.sh` → 「Mac 清理助手（网页版）.app」 | `app/launch` + `server.py`：启动器把服务丢后台并打开浏览器（启动器**不常驻**，否则 macOS 会当成"已在运行"导致点了没反应） |
| 纯浏览器 | `http://127.0.0.1:<port>/` | 引擎起在哪就开哪；`port` 写在 `~/.local/state/mac-cleanup-app/port` |

---

## 3. 引擎接口（**契约，不可改**）

`app/server.py`（只监听 127.0.0.1，随机端口；`MAC_CLEAN_PORT` 可指定）

| 方法 | 路径 | 请求体 | 响应要点 |
|---|---|---|---|
| GET | `/api/state` | — | `phase`(idle/scanning/scanning_done/judging/cleaning) · `elapsed` · `scan_at` · `scan_seconds` · `error` · `notice` · `has_scan` · `has_report` · `disk{total_gb,used_gb,free_gb,pct,mount}` · `tiers{green,yellow,red}` · `items[]` · `judged{count,model,judged_at}` |
| GET | `/api/items` | — | `{items[], judged}` |
| POST | `/api/scan` | `{}` | 异步起扫描，立刻返回 `{ok:true,phase:"scanning"}` |
| POST | `/api/report` | `{}` | `{ok,url:"/report.html"}`（生成完整 HTML 报告） |
| POST | `/api/clean` | `{mode:"preview"\|"apply", confirm?, paths?[]}` | `{ok,output,selected,free_before_gb,free_after_gb, done,would_do,failed,skipped, done_paths[],failed_items[],skipped_items[],freed_kb}`。**`mode:"apply"` 必须 `confirm==="确认清理"`；`paths` 为空数组会被拒**；传 `paths` 时引擎写 `select.json` 并只做**交集**（不会越过白名单扩大范围）。<br>**回执语义（P7 修）**：`done` 是**真正处理掉**的项数，不是请求项数；预览时 `done` 恒为 0、`would_do` 才是「若执行会动几项」；全被跳过时 `ok:false` 且 `clean.py` 退出码 2 |
| POST | `/api/trash` | `{confirm:"清空废纸篓"}` | `{ok,output,free_gb}`（唯一不可逆动作，必须手打确认语） |
| GET | `/api/whitelist` | — | `{output}` 保护名单 |
| POST | `/api/whitelist` | `{action:"list"\|"add"\|"remove", path?}` | `{ok,output}` |
| GET | `/api/history` | — | `{output}` 操作日志 |
| GET/POST | `/api/typesafe` | POST `{key}` | `{configured,source,path}` / 写入 `~/.config/typesafe/key`（权限 600，只记路径不记内容） |
| POST | `/api/judge` | `{top,only_unknown,batch?}` | `{ok,output,judged}`（调 Jev，结果写 `judged.json` 并重生成报告） |
| POST | `/api/quit` | `{}` | 关闭服务 |
| GET | `/` `/report.html` | — | 界面 / 报告 |

### `items[]` 单项形状（UI 全靠它渲染）

```json
{ "path": "/Users/bai/Library/Caches/com.tencent.xinWeChat",
  "name": "com.tencent.xinWeChat", "size": "1.9 GB", "size_kb": 1992000,
  "tier": "green|yellow|unknown", "selectable": true,
  "note": "应用缓存。清内容保留目录，会自动重建",
  "ai": { "tier":"green|yellow|red", "category":"…", "category_label":"…",
          "safe_to_delete":0.72, "recoverability":2.45, "category_confidence":0.9, "advice":"…" } }
```

- `tier`：绿色的三档抽屉（`green` 可安全清理 / `yellow` 需你判断 / `unknown` 系统管控＝规则表认不出，**不可勾选**）
- `selectable`：只有白名单内、纯可再生的子目录才是 `true`
- `ai`：Jev 的可选判断（`tier` 是它的建议：green 建议删 / yellow 拿不准 / red 别删）

---

## 4. 不可破坏的约束（改任何东西前先读这一节）

1. **安全链**：扫描只读；勾选**只做交集**（选择只能收窄范围）；预览不删任何东西；
   执行＝移到废纸篓（可逆）；**只有「清空废纸篓」不可逆**——
   - 确认成本与不可逆程度挂钩：可逆的一次点击（单条清理/批量闸门），不可逆的手打确认语。
   - 后端契约不变：`apply` 仍带 `confirm:"确认清理"`（由 UI 在用户点击确认后填入）。
2. **引擎接口与技能脚本不动**（见第 3 节）。要改清理规则 → 改技能脚本，不要改 UI。
3. **单文件、零第三方依赖、离线可用**：`app/index.html` 不得引 CDN/字体/框架；图形用 canvas/SVG 自己画。
4. **键盘可达**：Tab 能走完报头 → 工具栏 → 勾选 → 每条动作 → 执行台；`:focus-visible` 焦点环必须在。
5. **`prefers-reduced-motion: reduce`**：粒子、光柱、自转、过渡全部关掉，但状态反馈保留。
6. **对比度 ≥4.5:1**（正文；大字号 ≥3:1）。已经实测过的数值写在 `DESIGN.md`。
7. **视觉方向已定稿**：世界＝「档案馆 · 除尘与退役」，v3 已升级为「档案鼓」（3D 仪表）。
   改视觉请走 Impeccable 流程（第 6 节），不要凭手感另起一套。

---

## 5. 怎么构建、怎么验证（命令全集）

```bash
cd /Users/bai/Developer/mac-cleanup-app

# 1) 构建 + 安装原生 App（编译 swiftc，几秒）
bash native/build_native.sh                 # 产物：~/Applications/Mac 清理助手.app
open -a "$HOME/Applications/Mac 清理助手.app"

# 2) 功能自检（不点按钮也能验：走界面同一条代码路径）
"$HOME/Applications/Mac 清理助手.app/Contents/MacOS/MacCleanup" --selftest
#  期望：10 项全过（引擎响应 / 清单 / 磁盘 / AI 判断并入 / 勾 2 项只处理 2 项 /
#        确认语打错被拒 / 空勾选被拒 / 保护名单 / 操作历史）

# 3) 单独起引擎（改 UI 时最常用；不要用 App 自带的那份，省得互相干扰）
MAC_CLEAN_OPEN=0 MAC_CLEAN_PORT=58901 /usr/bin/python3 app/server.py
#  然后浏览器开 http://127.0.0.1:58901/

# 4) 机械设计检查（Impeccable 的 59 条确定性规则；目标目录传文件即可）
~/.hermes/skills/impeccable/scripts/impeccable detect --json app/index.html
#  期望：0 条

# 5) JS 语法体检（改完 index.html 必跑，省得靠猜）
python3 -c "import re;s=open('app/index.html',encoding='utf-8').read();import sys;\
open('/tmp/c.js','w').write(re.search(r'<script>(.*)</script>',s,re.S).group(1))" && node --check /tmp/c.js

# 6) 调试钩子（把动画状态钉死后截图核对；只在 ?debug=1 时存在）
#  http://127.0.0.1:58901/?debug=1 → window.__drum.force(0.661) / .state() / .frame()
```

**界面截图核对（本机怎么拍）**

```bash
# 应用窗口（原生 App）：被别的窗口挡住也能截到内容
bash tools/capture-app-window.sh                       # → .impeccable/review/appwindow.png
swift tools/winlist.swift                              # 只看 id/位置的话，用这个列窗口
#  注意：WKWebView 会造几个小的辅助窗口（同 owner 名）→ 脚本取面积最大的那个；
#  且 App 不在前台时 WindowServer 只留缩小快照（会报成 158x155）→ 脚本会先把它唤到前台再截。
```
- 浏览器视口截图用 Hermes 的浏览器工具（`cdp('Emulation.setDeviceMetricsOverride', width=1440 …)` +
  `Page.captureScreenshot(captureBeyondViewport=True, clip=…)`），两个视口固定为 **1440×整页** 与 **390×整页**。
- 结果请写进 `.impeccable/review/`（沿用同名：`desktop.png` / `mobile.png` / 需要时再加特写）。

---

## 6. 设计流程（Impeccable，已装好）

- 技能：`~/.hermes/skills/impeccable/`（v4.6.0＋引擎 0.1.15），参考文档在 `~/.hermes/skills/impeccable/reference/`，
  关键两份：**`craft-floor.md`（质量地板与禁令）**、**`mode-operate.md`（工具界面的规矩：世界只能提供字体/调色/密度/一个签名动作，控件必须是标准件）**。
- 每个表面一条命令起手（会读 PRODUCT.md / DESIGN.md / 表面 brief）：
  `~/.hermes/skills/impeccable/scripts/impeccable context --target app/index.html`
- 迭代循环（改视觉时照这个走）：
  `detect` 查禁 → 改 → 重建 → **一次批量截图**（桌面+手机一起）→ 修完再看一次 → 收工（不要无止境自审）。
- 大改（换世界/加表面）：`concept-seed` 掷方向 → 决策页让用户选 → 方向契约写进
  `.impeccable/surfaces/app-index-html.md` → 再动代码。
- 完工评审：派一个**全新上下文**的评审子代理（读 `reference/degraded/finish-reviewer.md` + `craft-floor.md`），
  给它：原始要求、确认过的答案、制品路径、**两个视口的截图**、方向契约、detector 结果。
  它会给 `ship / fix / rebuild / recapture`；按裁定处理后，最终要让 `DESIGN.md` 与
  `.impeccable/design.json` 反映实际构建出来的东西。

---

## 7. 已经踩过的坑（省你时间）

| 坑 | 现象 | 处理 |
|---|---|---|
| **无头浏览器 rAF 被压到 1Hz 以下** | 截图里"液位"停在 2%、动画没跑完，看着像产品坏了 | 动画一律**按时间推进**（`ease(k)=1-exp(-k·dt)`，dt 上限 0.5s）；截图核对用 `?debug=1` 的 `__drum.force()` 钉死状态 |
| 合成 `pointermove` 没有坐标 | `rotY/rotX` 变 NaN → 画布只剩底噪 | 事件里先判 `typeof e.clientX !== "number"` 就 return；每帧再兜一次 `isFinite` |
| 网页版 App「点了没反应」 | 启动器常驻 → macOS 认为已在运行，只去"激活"一个没窗口的进程 | `app/launch` 把服务 nohup 到后台后**自己退出**；`LSUIElement` 只给网页版用 |
| 原生 App 窗口不出现 | 缺 `NSPrincipalClass` / 被别的窗口盖住 | Info.plist 补 `NSPrincipalClass=NSApplication`；窗口 `orderFrontRegardless()` + `applicationShouldHandleReopen` |
| 重建后图标点了没反应 | LaunchServices 记录过期 | `build_native.sh` 里已包含 `lsregister -f -R` + 清 quarantine |
| WKWebView 里 `window.open` 无反应 | 完整报告/在浏览器打开点了不动 | `WKUIDelegate.createWebViewWith` 交给系统浏览器（`main.swift` 已实现） |
| 数字"打架" | macOS 保留快照与系统空间，所以 可用＋已用 ≠ 容量 | UI 分成「可写入空间 / 已用·磁盘容量 / 占用率」三行，并在鼓的说明里写明原因 |
| 图标用了 emoji / 零偏移彩色光晕 | detector 报 `dark-glow`；craft floor 也禁 | 图标一律手绘 SVG；发光必须带偏移或属于数据本身 |
| 一次提交信息里带引号 | shell 把 `"` 吃掉导致 commit 失败 | 长提交信息写进文件后 `git commit -F <file>` |

---

## 8. 状态文件与产物

`~/.local/state/mac-cleanup-app/`：`port`（当前端口）· `scan.json`（最近体检）· `report.html`（完整报告）
· `judged.json`（Jev 判断）· `select.json`（最近一次勾选项）· `server.log`（引擎日志）· `engine.log`（App 引擎）· `launch.log`（网页版启动留痕）
操作历史：`~/.local/state/macos-deep-optimize/operations.log`
Jev key：`~/.config/typesafe/key`（或环境变量 `TYPESAFE_API_KEY`）

---

## 9. 当前进度与已完成账目

1. **v3 完工评审已裁定 `ship`**（所有 8 项修正与 4 项非阻断残留全部处理完毕）。
2. **P1 鼓的性能与画质上限（已完成）**：
   - 实现 `const DPR = Math.min(2, window.devicePixelRatio || 1)` 动态适配；
   - 画布根据 DPR 自动在 1x (452×452) 与 2x (904×904) 之间同步分辨率，在 2D context 中建立逻辑缩放，1x/2x Retina 屏上线条文字均细腻无锯齿；
   - 实测 53 项真实清单与拖动交互持续 3 秒帧率平均保持 54.4–60 fps；
   - 产出截图证据：`drum-1x.png` (452×452)、`drum-closeup.png` (904×904)。
3. **P2 无障碍等价文本（已完成）**：
   - `.drum-wrap` 增加 `role="img"` 与动态 `aria-label`（如“磁盘已用 66.5%、可写入 46 GB；清单 53 项，其中 8 项可清理 4.56 GB”）；
   - 在 `refresh()` 中实时计算并写入；内部 canvas 与读数均设 `aria-hidden="true"`，键盘 Tab 干净跳过仪表。
4. **P3 清理回执更可信（已完成）**：
   - 只有后端 `/api/clean` 返回 `ok:true` 时才在卡片盖「已退役」章，并内嵌 `.card-receipt.ok` 呈现移入废纸篓项数、前后空间变化与“清空废纸篓方可释放空间”核对信息；
   - 若服务端 `ok:false`，严格确保绝不盖退役章，并以 `.card-receipt.err` 显式标出服务端拒绝原因与“未发生任何删除，原文件安全保留”；
   - 经 `tools/verify-interaction.js` 注入 fetch 拦截实测通过。
5. **P4 窄屏实操体验（已完成）**：
   - 390 宽下单选勾选、单条清理、批量清理与手打确认语清空废纸篓 4 条路径全通；
   - 触控目标热区全部满足 ≥44px（按钮、输入框、复选框 hit area）；
   - 长路径与长名称设置 `overflow-wrap: anywhere; word-break: break-all`，绝不截断或溢出；
   - 产出截图证据：`mobile.png` 与 `mobile-operated.png`。
6. **P5 顺手清账（已完成）**：
   - 清除 `.impeccable/review/` 中遗留的历史旧图（`desktop-1x.png`、`mobile-1x.png`）；
   - 更新 `README.md` 与 `HANDOVER.md` 中的说明与构建命令；
   - 新增验证工具：`tools/test-cdp.js`（Node 纯内置模块 CDP）、`tools/snap.js`（整页与特写截取）、`tools/verify-interaction.js`（交互与网络载荷拦截测试）；
   - 全套验证通过：`--selftest` 10/10、`impeccable detect` 0 条违规、`node --check` 语法体检通过。
7. **P6 垃圾清理精准度专项增强（已完成）**：
   - **孤儿误报清零**：将已安装 App 识别范围从仅 `/Applications` 与 `~/Applications` 扩展到 `/System/Applications`、`/System/Applications/Utilities`、`/System/Library/CoreServices/Applications` 等系统应用全域（识别应用数从 29 个跃升至 231 个）；在 `Group Containers` 扫描中自动剥离 10 位 Team ID（如 `243LU875E5.groups.`），成功消除 `com.apple.podcasts` 等系统内置组件被误判为孤儿残留的假阳性；增加正在运行进程检测（活跃中的应用绝不报孤儿）。
   - **iCloud Drive 误删漏洞彻底修复**：修复 `clean.py` 中绕过 `build_model` 直接循环 `scan['installers']` 导致 iCloud 安装包被强制打入绿灯的严重漏洞。统一以 `report.build_model()` 为唯一真理来源，iCloud Drive 安装包严格归为黄灯警示，默认清理绝不触碰。
   - **现代开发环境与产物支持**：`LOCK_FILES` 增加 `bun.lockb`, `bun.lock`, `pubspec.lock`, `Podfile.lock`, `flake.lock`，彻底解决 Bun/Flutter/iOS 项目构建产物误判为「无锁文件无法安全重建」的问题；`ARTIFACT_NAMES` 补充 `.svelte-kit`, `.astro`, `.docusaurus`, `.output`, `.dart_tool`, `.gradle` 等现代框架产物。
   - **残留安装包动态拦截**：过滤正在下载中的文件（`.download`, `.crdownload`, `.part`, `.tmp`）、当前挂载使用中的 DMG（`hdiutil info`）以及 2 小时内新下载的文件（避免将刚下载待安装的镜像误当作垃圾）。
   - **纯缓存与黄灯安全子路径扩展**：`GREEN_RULES` 新增 Bun 全局缓存、CocoaPods 依赖下载缓存、Go 编译缓存、Playwright 浏览器缓存、Xcode 测试缓存、CloudKit 临时缓存等；`YELLOW_SAFE_SUFFIXES` 增补 Chromium/Electron 的 `ShaderCache`, `DawnCache`, `Crashpad`, `blob_storage` 等纯渲染/崩溃缓存。
   - **非 Caches 敏感文件硬拒绝机制**：在 `clean.py` 的 `check()` 守卫中，对任何非 Caches 目录下的 `Cookies`, `IndexedDB`, `Local Storage`, `Session Storage`, `Preferences`, `Keychains`, `Accounts` 以及 `.db`/`.sqlite` 数据库文件实施强制硬拦截，彻底杜绝数据破坏风险。

**别做的事（硬红线保持）**：清理规则只在技能脚本中；离线单文件零外部依赖；安全锁与确认模型不破；Operate 模式标准控件。

---

## 10. P7 · 缺陷修复 + 性能优化 + 借鉴 mole（2026-10-18 下午）

### 10.1 修掉的 5 个既有缺陷

| # | 缺陷 | 根因 | 修法 |
|---|---|---|---|
| 1 | **回执超额报告**：勾 1 项、实际删 0 项，界面仍写「已移入废纸篓（共 1 项）」 | `clean.py` 全部跳过也 `exit 0`；`/api/clean` 按退出码判 ok，`selected` 是**请求**项数不是**完成**项数 | `clean.py` 末尾输出 `__RECEIPT__ {json}`（done/failed/skipped/done_paths/freed_kb）；全跳过时 `exit 2`；`server.py` 解析回执；UI 只给 `done_paths` 里的行盖章 |
| 2 | **预览被误读成「删了 N 项」** | 预览分支直接 `return`，没有回执，调用方只能靠退出码猜 | 预览也输出回执，且 `done` 恒为 0、`would_do` 才是「若执行会动几项」；server 对 `mode=="preview"` 单独处理 |
| 3 | **文档漂移**：HANDOVER 写 1194/569/895 行，实际 1369/602/922；且声称仓库内有 `skill-scripts/` 兜底副本，实际没有 | 文档没跟着改；兜底副本只在构建产物里 | 全部行数改正；`app/skill-scripts/` **纳入 git 作为唯一真理源**，并写明同步命令 |
| 4 | **孤儿检测子串匹配过宽**：进程名 `node` 会压掉 `com.foo.nodebar` 这类无关目录 | `if any(p in bid_lower for p in proc_names)` 是任意子串包含 | 改**整词 + 前缀变体**匹配（`_bid_tokens` / `_proc_tokens` / `_proc_owns_bid`），stopwords 剔除 node/code/helper 等通用词 |
| 5 | **超大截图**：`mobile.png` 11300px、`mobile-operated.png` 12446px，超过读图工具 8192px 上限 | 整页截图未限制高度 | 等比压到 6000px 高（414×6000 / 376×6000），保持可读 |

### 10.2 性能优化（扫描 86.7s → 53.2s，−39%）

| 改动 | 效果 |
|---|---|
| `scan_dirs` 从「du_map 算总量 + 逐父目录再 du 算子项 + 单独 du 整个 `~/Library`」改为**每个目标目录只跑一次 `du -k -d 1`**，父子体积同时到手；`~/Library` 总量由子项求和 + 未覆盖条目补齐 | **69.4s → 11.1s（6.2×）** |
| `installed_bundle_ids` / `launchservices_bundle_ids` 用 Python 内置 `plistlib` 取代逐 App 起 `PlistBuddy` 子进程（238 个 App 就是 238 次 fork/exec） | **3.6s → 0.28s（13×）** |
| `du_map` 改多路并行（按 CPU 封顶 4–6 线程） | 大目录批量测量提速 |
| `pkg_receipt_bundle_ids` 从「400 条收据逐条 `pkgutil --files`」改为只挑含 `.app` 的 40 条 + 6 秒总预算 + 进程内缓存 | **35.5s → 5.5s**，且改为按需启用（`MAC_CLEAN_PKG_RECEIPTS=1`），因为本机零收益 |
| `scan_leftover_groups` 先用 `stat` 零成本预筛，只对「同一 App 至少 2 处痕迹」的组算体积 | **57.3s → 2.4s** |

> BSD `du` 的坑：macOS 上 `-s` 与 `-d` **互斥**，正确写法是 `du -k -d 1 <path>`；写 `-sk -d 1` 会直接 usage 报错、零输出。

### 10.3 借鉴 mole（https://github.com/tw93/mole，GPLv3）落地的机制

调研笔记见 `docs/mole-notes/`：`protection.md`（保护规则/归属解析，1203 行）、`clean-rules.md`（可清理项规则，1028 行），每条都带 `文件:行号`。

**安全性（最重要）**
1. **修掉一条会真实损坏的规则**：`YELLOW_SAFE_SUFFIXES` 原先把 `/Service Worker/ScriptCache` 当可删。mole 在 `lib/clean/user.sh` 明确禁删（issue #785/#964/#968）——删掉 MV3 扩展字节码会破坏 extension service worker。**已移除**，并补上 `GrShaderCache`/`GraphiteDawnCache`/`component_crx_cache`/`Application Cache` 等真正的纯缓存叶子。
2. **登录凭据/同步状态硬保护**（`PROTECTED_SEGMENTS`）：`~/.ssh`、`~/.gnupg`、`~/.aws`、`~/.kube`、`Library/Keychains`、`Library/Mobile Documents`、`Mail/Messages/Safari/Calendars`、`group.com.apple.notes` 等，删了要重新授权/同步。
3. **备份/安全软件/虚拟机保护**（`PROTECTED_VENDOR_SEGMENTS`）：Time Machine、Backblaze、Arq、CCC、Dropbox/OneDrive、CrowdStrike/SentinelOne/Jamf、Docker/OrbStack/UTM/Parallels 等。**标注：这份清单是本仓库自有规则，比 mole 更保守**（mole 只有 EDR 前缀 + 352 条 bundle id，没有 Arq / Time Machine 的路径级规则）。
4. **AI 模型缓存不自动删**（`PROTECTED_MODEL_CACHES`）：HuggingFace / torch / tensorflow / ollama / lmstudio 等，重新下载代价高、中断可能损坏；含 mole 的 `com.apple.e5rt.e5bundlecache`（Apple Neural Engine 已编译模型缓存，删了下次推理要重编）。
5. **共享 XDG 根目录保护**（`SHARED_STATE_ROOTS`）：`~/.config`、`~/.local` 等根本身不删，但 `~/.config/zed` 这类 App 专属子目录仍可处理。
6. **`to_trash` 结果校验**：Finder 静默失败时旧代码会把「没删掉」当「已移入废纸篓」；现在无论走 Finder 还是 `shutil.move` 兜底，都**必须确认源路径真的没了**，否则抛异常记失败。

**准确性**
7. **归属解析增强**：`installed_bundle_ids` 扩到 Caskroom / Setapp / Input Methods / `/Applications/Utilities` 并支持嵌套；新增 `launchservices_bundle_ids()`（mdfind 快路径，实测补 18 个此前漏掉的 bundle id，0.6s）；`pkg_receipt_bundle_ids()` 按需启用。
8. **孤儿检测**：整词进程归属匹配（见 10.1 #4）+ 30 天闲置闸。
9. **新增「已卸载 App 成套残留」**（`scan_leftover_groups`）：不再只按目录名猜孤儿，而是先确定「这个 App 不在了」，再按 App 名的多种命名变体（`Maestro Studio` / `maestro-studio` / `MaestroStudio`）成套收集其散落各处的数据。同一 App 至少 2 处痕迹才报，进黄灯、绝不自动清理。实测 4 组、2.4s。
10. **构建产物项目根向上查找**（`find_project_root`）：修掉「monorepo 子目录产物找不到锁文件」的误判（`sunset-glow/web` 明明有 `package-lock.json` 却报 risk）。
11. **活跃度判定改为「目录内有无近期文件」**（`dir_has_recent_activity`）：mole 的实测经验——目录 mtime 只在直接子项增删时更新，天天写 `sub/cache.db` 的目录 mtime 可能是半年前，只看 mtime 会误清。用 `find -mtime -N -print -quit`。
12. **锁文件 14 → 26 种**：补 `npm-shrinkwrap.json`、`pdm.lock`、`requirements.txt`、`go.sum`、`gradle.lockfile`、`Package.resolved`、`Cartfile.resolved`、`mix.lock`、`stack.yaml.lock`、`elm.json` 等。
13. **构建产物 23 → 57 种**：按生态补齐（Vite/Rollup/Webpack/Nx/Storybook/Angular、tox/nox/eggs/hypothesis、Swift Package/Pods/Carthage、Serverless/SAM、Go vendor、Elm/OCaml/Elixir/Haskell/Zig 等）。

**科学性**
14. **日志/崩溃报告改为按天保留**（`RETENTION_DAYS = 7`，与 mole 的 `MOLE_LOG_AGE_DAYS` 一致）：日志不是垃圾而是排障证据，只删 7 天前的，不再整目录清空。优先级：`retain` > `empty` > `remove`。
15. **`GREEN_RULES` 18 → 35 条**，并**把具体规则排到通用 `/Library/Caches` 之前**（否则 Homebrew/CocoaPods 等会拿到泛化文案与错误命令）；补 Poetry/pyenv/conda/jupyter/ruff/mypy/NuGet/Ivy、Xcode DeviceSupport/模拟器缓存、DiagnosticReports 等。

### 10.4 验证记录

```bash
# 语法
for f in app/skill-scripts/*.py app/server.py; do python3 -c "import ast;ast.parse(open('$f').read())"; done   # 全过
node --check /tmp/c.js                                                                                          # 过
~/.hermes/skills/impeccable/scripts/impeccable detect --json app/index.html                                     # [] 0 违规

# 引擎实测（2026-10-18）
scan.py 全量扫描          → scan_seconds 53.2（改前 86.7）
                          → children 25 · orphans 7 · leftover_groups 4 · artifacts 12 · installers 2
clean.py 预览             → __RECEIPT__ {ok:true, would_do:1, done:0}       # 什么都没删
clean.py apply（目标消失）→ __RECEIPT__ {ok:false, done:0, skipped:1} + exit 2  # 不再超额报告
check() 敏感路径           → Claude/ZaloData/微信开发者工具 的 Cookies、Local Storage 全部 deny
check() 模型缓存           → huggingface / ollama / CrowdStrike 全部 deny
安全链                    → 错误确认语被拒、空勾选被拒、预览不删
```

**环境限制说明**（不是代码问题）：本机 DSH 沙箱禁止写 `~/.local/state/`，因此 `--selftest` 与 `tools/snap.js` 在沙箱内跑不起来（引擎写 `port` 文件即 `PermissionError`；Chrome CDP 起不来）。用可写 HOME 起同一份 `server.py` 验证接口全通。`native/build_native.sh` 也因本机 SDK(6.4.0.31.4) 与编译器(6.4.0.30.4) 版本不匹配而失败——需 `xcode-select --install` 更新或换匹配的 toolchain。

### 10.5 仍然待办

- **`--selftest` 需在正常终端复跑**（沙箱内跑不了），确认 10/10。
- **重新截图**：UI 未改视觉，但回执文案变了，建议在正常环境重拍 `desktop.png` / `mobile.png`。
- mole 的 `optimize` 19 个任务里只有 2 个值得移植（`qlmanage` 刷新图标/QuickLook、`dscacheutil` 刷 DNS 并带 VPN 守卫），本次**未做**——它属于「系统维护」而非「清理」，需要新的界面区块，建议单独一轮。
- mole 明确**不建议**移植的：LaunchServices 重建（macOS 15+ 对已消失 App 一律 -10814，且会让运行中的 VPN 扩展被读成重装）、`~/.m2`/`~/.nuget`/`~/.cargo/registry/src`/`~/.ollama/models` 的删除。
