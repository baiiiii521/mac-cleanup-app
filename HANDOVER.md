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
│   ├── index.html          ★ 主界面（单文件、零第三方依赖、离线可用）——1194 行，改这里
│   ├── index-v2-archive.html  v2「档案抽屉」备份（对照用，不参与运行）
│   ├── index-classic.html     最早的极简版界面（对照用）
│   ├── server.py           ★ 清理引擎的本地服务（569 行）：HTTP 外壳 + 状态机 + 安全闸门
│   ├── launch              网页版外壳的启动器（shell；把 server.py 丢后台后自己退出）
│   └── Info.plist / AppIcon.png
├── native/
│   ├── main.swift          ★ 原生 App（895 行）：窗口 + WKWebView + 引擎拉起/回收 + --selftest
│   ├── build_native.sh     ★ 构建并安装到 ~/Applications（swiftc，只需 CommandLineTools）
│   └── Info.plist          （含 NSPrincipalClass / NSAllowsLocalNetworking）
├── build.sh                备用：把网页版外壳装成「Mac 清理助手（网页版）.app」
├── tools/make_icon.py      生成图标源图
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

**引擎规则只在一处**：`~/.hermes/skills/devops/macos-deep-optimize/scripts/{scan,report,clean,judge}.py`
（`server.py` 通过 `MAC_CLEAN_SKILL` 找它，找不到才用 app 内自带的副本）。**不要把这些规则复制进 UI 或另写一份。**

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
| POST | `/api/clean` | `{mode:"preview"\|"apply", confirm?, paths?[]}` | `{ok,output,selected,free_before_gb,free_after_gb}`。**`mode:"apply"` 必须 `confirm==="确认清理"`；`paths` 为空数组会被拒**；传 `paths` 时引擎写 `select.json` 并只做**交集**（不会越过白名单扩大范围） |
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

## 9. 当前待办（按优先级）

1. **等 v3 的完工评审裁定**（已派出，评审包里有 desktop/mobile/drum-closeup 三张图）。
   回来的裁定若含 `fix`，按它列的清单一次修完 → 重建 → **回同样的视口重拍** → 交回同一评审跑"回执评分"。
2. **鼓的性能上限**：canvas 内部固定 904×904，DPR 未封顶。高分屏/多显示器下建议
   `const DPR = Math.min(2, devicePixelRatio)` 并让内部尺寸随之缩放；50+ 张卡时保持 ≥55fps。
3. **无障碍补完**：鼓是纯视觉仪表，需要给读屏用户一条等价文本通道
   （例如给 `.drum-wrap` 加 `role="img"` + `aria-label`，内容为"磁盘已用 66.1%、可写入 40.8 GB、清单 53 项"）。
4. **`?debug=1` 钩子**：确认它不会出现在任何用户路径（只在查询串里）——已在源码里这么做，保持即可。
5. 可选：把「已退役」的历史（`operations.log`）做成抽屉里的"最近退役"小列表（新增功能 → 需先问用户）。
6. 可选：鼓的卡片在液面附近会有遮挡，属物理合理；若要更清楚，可让液下卡片只画轮廓不填充。

**别做的事**：不要把清理规则写进 UI；不要为了好看牺牲"第一次看就懂"；不要引入任何外部资源；
不要绕过安全链（任何"为了省事"的直接删除都会破坏这个产品的立身之本）。
