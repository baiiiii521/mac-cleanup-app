# Mac 清理助手（mac-cleanup-app）

把 `macos-deep-optimize` 技能（只读扫描 + 三色分级 + 安全清理）封装成**双击就能用的 macOS 应用**：
不用开终端、不用记命令，打开就是界面。

> 已安装：**~/Applications/Mac 清理助手.app**
> 打开方式：Finder → 应用程序 →「Mac 清理助手」；或启动台搜索；或 `open ~/Applications/Mac\ 清理助手.app`
>
> 窗口里就是这个界面（同一份 HTML 由 App 的 WKWebView 承载，手机/浏览器打开也是它）。
> 菜单里的「重新载入界面 / 在浏览器中打开界面」都指向同一个引擎。

## 形态

| | 现在（默认） | 备用 |
|---|---|---|
| 窗口 | 原生 App 窗口 + 内嵌 WKWebView 显示本界面 | `--classic` 参数启动可回退到 AppKit 原生控件版界面 |
| 网页外壳 | `bash build.sh` 装成「Mac 清理助手（网页版）.app」，界面在浏览器里，手机也能看 | |

三者共用**同一个引擎**（`Resources/server.py` + 技能脚本）：清理规则、安全锁、AI 判断行为完全一致。

## 界面：档案鼓 · 除尘与退役（v3）

界面的视觉世界定稿为「档案馆 · 除尘与退役」，只借四样东西：字体分级、调色、密度、一个签名动作；
布局、导航、控件仍是标准件（Operate 表面：先是个干活的工具，再谈好看）。

- **档案鼓（3D 软件渲染仪表）**：外圈圆盘液面＝磁盘占用率，鼓身立着的每一张卡＝清单里的一项，
  卡的高度是它的体量（对数刻度）；勾选时对应卡片发光抽出；支持拖拽转动。DPR 封顶至 2，
  在 1x / 2x Retina 屏上均保持细腻无锯齿；配备完整的读屏等价文本（`role="img"` + 动态 `aria-label`）。
- **三格抽屉**：可安全清理 / 需你判断 / 系统管控，每张卡＝编号 + 名称 + 体量刻度（刻度线＝100 MB）+
  大小 + 路径 + 说明；勾选卡片抽出；长清单可展开。
- **签名动作与可信回执**：执行清理时卡片散成尘埃旋入鼓身，可用空间随之跳格。只有服务端返回 `ok:true`
  时才盖「已退役」章并附带移入废纸篓项数、前后空间变化与提示；若服务端拒绝则显式给出失败原因，绝不误盖退役章。
- **窄屏适配**：≤560px 下触控热区均 ≥44px，长路径安全换行不溢出。
- 设计标准：Impeccable craft floor，机械检查 0 违规：
  `~/.hermes/skills/impeccable/scripts/impeccable detect --json app/index.html`

## 自检（不点按钮也能验一遍）

```bash
"$HOME/Applications/Mac 清理助手.app/Contents/MacOS/MacCleanup" --selftest
```

走的是界面里按钮**同一条代码路径**（同一个 `api()` 调用、同样的请求体），打印：引擎是否起来、
清单条数、磁盘信息、AI 判断是否并入、勾选 2 项是否只处理 2 项、确认文本不对是否被拒、空勾选是否被拒……
实测 10 项全过。

## 打不开？看这里

**原生版**：点了图标没看到窗口？先看是不是被别的窗口盖住了——现在已经用 `orderFrontRegardless`
强制抬到最前，再点一次 Dock 图标也会把窗口带到前面（`applicationShouldHandleReopen`）。
实在想看细节，终端里跑一次自检：`".../MacCleanup" --selftest`。

**网页版**：双击图标没反应时，**先看日志**：`~/.local/state/mac-cleanup-app/launch.log`（每次启动都留痕）。

- 现象：点了图标什么都没有
  原因：这个应用没有窗口，界面在浏览器里。启动器曾把服务挂在前台运行，macOS 于是认为
  「App 已在运行」，之后再点图标只去激活那个没有窗口的进程。现在改成服务丢后台、启动器立刻退出，
  每次点击都会重新打开界面（实测连点两次都能打开）。
- 现象：想手动兜底
  终端里直接开：`open "http://127.0.0.1:$(cat ~/.local/state/mac-cleanup-app/port)/"`，
  端口号就在 `~/.local/state/mac-cleanup-app/port`。
- 现象：服务卡住 / 想彻底停掉
  界面上点「关闭服务」，或者终端 `pkill -f "Mac 清理助手.app/Contents/Resources/server.py"`。
  再点图标会自动重新起来。
- 现象：macOS 提示「无法验证开发者」
  本机自建的应用是 ad-hoc 签名。若被拦，右键点图标 →「打开」→ 确认一次即可；或终端执行
  `xattr -dr com.apple.quarantine ~/Applications/Mac\ 清理助手.app`。

## 界面流程（四步，都是点按钮）

1. **体检**（只读扫描，通常 1-3 分钟）→ 顶部实时显示磁盘可用空间
2. **打开完整报告** → 新标签页打开 HTML 报告：磁盘总览（三色分段进度条）、内存/负载诊断、🟢🟡🔴 三色清单、后台服务、大文件清单
3. **逐项勾选** → 结果分三组列出：🟢 可安全清理（默认全选）、🟡 需你判断（只有纯缓存子目录可勾）、
   🤔 规则表认不出（显示 AI 判断，仅供看，不可自动清理）。快捷按钮：全选 / 全不选 / 只选 ≥100MB / 只选缓存类 / 反选
4. **预览选中项 → 执行** → 预览只列出勾选的那些；执行要在输入框敲「确认清理」，默认**移到废纸篓（可逆）**，
   并显示可用空间前后对比；想真正释放空间再点**清空废纸篓**（需敲「清空废纸篓」确认）

### 🤖 AI 判断（TypeSafe / Jev）

规则表只认得写死的模式，遇到未知 App 的数据目录、UUID 命名容器就没办法。点「运行 AI 判断」会调用
TypeSafe 的 Jev，对**规则表认不出的目录**返回带概率的类型化判断（类别 / 可删概率 / 可恢复性 / 置信度），
清单里会显示 🤖 徽章、报告里会多一块「Jev 判断」。

- key 读取顺序：环境变量 `TYPESAFE_API_KEY` → `~/.config/typesafe/key`（界面上可以直接填，权限 600）
- ⚠️ 这是**外部 API**：会把目录路径、大小、最后修改时间、子目录名发到 TypeSafe 服务器
- 判断结果是**参考**不是判决，最终仍由你拍板；每个目录问 3 个问题、5 个一批（`--top` 控制范围）

工具箱里还有：**保护名单**（加入后永不清理）、**操作历史**（每次执行都记日志）、**关闭服务**。

## 安全保障（三层，来自技能）

1. **白名单** —— 只处理报告判为可清理的路径，其余一律拒绝
2. **硬保护** —— 系统目录、HOME 本身、HOME 的一级目录永不删除
3. **用户保护** —— `~/.config/macos-deep-optimize/whitelist` 里的路径跳过

再加应用层两条：清理**默认预览**；**执行必须输入确认文本**（后端也校验，绕过界面直接调接口同样会被拒）。

**数据不出本机**：服务只监听 `127.0.0.1`，没有任何外网上传；报告是纯静态 HTML，离线可看。

## 结构

```
mac-cleanup-app/
├── app/
│   ├── Info.plist              应用元信息（名称/标识/图标）
│   ├── launch                  .app 的入口：挑 python → 起服务 → 开浏览器（已运行则只开浏览器）
│   ├── server.py               唯一自研代码：HTTP 外壳 + 状态管理（不实现任何清理规则）
│   ├── index.html              界面（深色、单页、零外部资源）
│   └── AppIcon.png             图标源图（由 tools/make_icon.py 生成）
├── tools/make_icon.py          纯标准库生成图标（黄昏渐变 + 圆角）
├── build.sh                    组装并安装到 ~/Applications（含图标、ad-hoc 签名）
└── README.md
```

**清理规则全部来自技能**（`~/.hermes/skills/devops/macos-deep-optimize/scripts/`）：
`scan.py`（只读扫描）· `report.py`（三色分级 + HTML 报告）· `clean.py`（安全清理执行器）· `judge.py`（可选 Jev 判断）。
运行时会**优先使用实时技能目录**，所以改技能立刻生效；app 内的 `skill-scripts/` 只是技能目录不存在时的兜底副本。

## 重建 / 更新

```bash
cd ~/Developer/mac-cleanup-app
# 默认：构建原生 macOS 应用并安装到 ~/Applications（swiftc，几秒完成）
bash native/build_native.sh
# 备用：组装网页版外壳应用
bash build.sh
# 功能自检（10 项全通）
"$HOME/Applications/Mac 清理助手.app/Contents/MacOS/MacCleanup" --selftest
```

技能脚本更新后**不需要重建**（app 直接读技能目录）。只有当你想更新 app 自带的兜底副本或界面时才重建。

状态与日志：

```
~/.local/state/mac-cleanup-app/          # scan.json / report.html / port / server.log
~/.local/state/macos-deep-optimize/operations.log   # 清理操作日志（技能写的）
```

## 排错

| 现象 | 处理 |
|---|---|
| 双击没反应 | 看 `~/.local/state/mac-cleanup-app/server.log`；多半是找不到 python3 → `xcode-select --install` |
| Gatekeeper 提示"无法验证开发者" | 右键 → 打开（只需一次）；build.sh 已做 ad-hoc 签名 |
| 浏览器没自动打开 | 手动访问 `http://127.0.0.1:$(cat ~/.local/state/mac-cleanup-app/port)/` |
| 清空废纸篓失败 | 系统会弹 Finder 授权窗（自动化权限），同意后再点一次 |
| 清理后空间没变 | 正常——内容进了废纸篓，点「清空废纸篓」才真正释放 |
| 想关掉服务 | 界面右下角「关闭服务」，或到 Dock 上退出该应用 |

## 首次实测（2026-10-17，本机）

- 磁盘：228.3 GB 总 / 147.7 GB 已用 / 46-48 GB 可用（占用 65%）
- 🟢 可自动清理 **5.0 GB**（微信缓存 1.9 GB、uv 缓存 858 MB、Hotwave-Finance/node_modules 637 MB、
  opencode 更新包 635 MB + 485 MB、Chrome 缓存 371 MB、playwright 206 MB、日志）
- 🟡 需你判断 **22.4 GB**（Claude 10.6 GB、Zalo 6.1 GB、LarkShell 2.8 GB、微信开发者工具 747 MB…）
- 🔴 不建议手删：Spotlight 索引（`~/Library/Metadata`）

## 已知修复（本次封装时发现）

技能里 `report.py` / `clean.py` **对同一路径可能重复计数**（项目构建产物 `node_modules` 同时来自
children 与 project_artifacts），表现为预览里同一项出现两次、执行时第二次必然失败。
已在两处加去重（`report.build_model` 查 `seen`、`clean.collect` 按 realpath 去重），预览项数 9 → 8。
原文件备份在技能目录：`*.bak-20261017T225130`。
