<div align="center">

# Mac 清理助手

**先看清楚，再动手。** 只读体检、三色分级、逐项勾选、移入废纸篓可恢复的 macOS 磁盘清理工具。

[![macOS 13+](https://img.shields.io/badge/macOS-13%2B-000000?style=flat-square&logo=apple&logoColor=white)](#安装) [![Swift + Python](https://img.shields.io/badge/Swift%20%2B%20Python-3.9%2B-F05138?style=flat-square&logo=swift&logoColor=white)](#技术构成) [![Local only](https://img.shields.io/badge/network-127.0.0.1%20only-6FF7C8?style=flat-square)](#隐私) [![No dependencies](https://img.shields.io/badge/front--end-zero%20dependencies-3FA66A?style=flat-square)](#技术构成)

**简体中文** · [English](README.en.md)

<img src="docs/assets/hero.jpg" alt="Mac 清理助手主界面：磁盘档案鼓、三色清单与清理操作" width="100%">

<sub>主界面 · 左侧档案鼓显示磁盘占用与清单体量，右侧按可安全清理 / 需你判断 / 系统管控分组</sub>

</div>

---

## 为什么做这个

macOS 磁盘快满时，大多数清理工具要求你相信一个黑盒：点一下「优化」，然后接受一个说不清来源的释放数字。

Mac 清理助手反过来做：**先把每个字节的来源摊开，再让你决定动不动。**

- **只读体检**：扫描阶段不改任何文件，先出一份完整报告。
- **三色分级**：绿色是纯缓存 / 可重建，黄色含用户数据或判断不了，红色不建议动。
- **选择只收窄**：勾选只能从白名单里减，永远不会越过安全边界扩大范围。
- **默认可逆**：清理是移到废纸篓，不是 `rm -rf`；想真正释放空间，再单独清空废纸篓。
- **字面诚实**：服务端返回 `ok:true` 才盖「已退役」章；全部跳过就说「磁盘没有变化」，不虚报。

## 功能

| 能力 | 说明 |
|---|---|
| **只读体检** | 扫描缓存、日志、构建产物、卸载残留、安装包与大文件，通常 1–3 分钟 |
| **档案鼓** | 外圈液面 = 磁盘占用率，鼓身每张卡 = 清单一项，卡高 = 体量（对数刻度）；可拖拽转动，勾选时卡片抽出 |
| **三格抽屉** | 可安全清理 / 需你判断 / 系统管控；每张卡给出编号、体量刻度、大小、路径与处置说明 |
| **逐项勾选** | 单卡勾选或快捷选择：全选、全不选、≥100 MB、只选缓存类、反选 |
| **完整报告** | 生成一份静态 HTML：磁盘总览、内存 / 负载诊断、三色清单、后台服务、大文件清单 |
| **可信回执** | 执行后只报告真正处理掉的项数与空间变化；失败会给出原因，不会误盖成功章 |
| **AI 判断（可选）** | 规则表认不出的目录，调用 TypeSafe Jev 给出类别、可删概率、可恢复性与置信度 |
| **保护名单与历史** | 加入保护名单的路径永不清理；每次操作都有日志 |
| **三种外壳** | 原生 App 窗口（WKWebView）、网页版 `.app`、`--classic` AppKit 原生控件版 |

<a href="docs/assets/drum.jpg"><img src="docs/assets/drum.jpg" alt="档案鼓细节" width="420" align="right"></a>

### 界面：档案馆 · 除尘与退役

界面的视觉世界借自档案馆：字体分级、克制的调色、紧凑的密度，以及一个签名动作——清理时卡片散成尘埃旋入鼓身，可用空间随之跳格。

动效只用来指示状态与因果，不遮挡内容。窄屏下触控热区不小于 44px，长路径安全换行；`prefers-reduced-motion` 时停掉粒子与入场动画，但保留状态反馈。

## 安全模型

三层来自清理引擎，两层来自应用：

1. **白名单**：只处理报告判为可清理的路径，其余一律拒绝。
2. **硬保护**：系统目录、HOME 本身与一级目录、登录凭据（`~/.ssh`、Keychains、Mail、Safari）、备份与安全软件、虚拟机 / 容器数据、AI 模型缓存，即使规则表判成绿灯也拒绝。
3. **用户保护**：`~/.config/macos-deep-optimize/whitelist` 中的路径跳过。
4. **默认预览**：不勾选、不预览，就不会执行。
5. **确认文本**：执行清理必须输入「确认清理」，后端同样校验；绕过界面直接调接口也会被拒。

此外还有一道**可重建性闸门**：构建产物要同时满足「向上找到项目根且有锁文件（26 种）」「不是今天还在改」「git 无未提交改动」，才会被判为可安全重建。

日志与崩溃报告按天保留（默认 7 天），只删更旧的——它们是排障证据，不是垃圾。

## 技术构成

```text
WKWebView / 浏览器
        │  HTTP（仅 127.0.0.1）
        ▼
app/server.py            本地 HTTP 外壳 · 状态管理 · 不实现清理规则
        │
        ├─ scan.py          只读扫描 · 体量与可重建性判断
        ├─ report.py        三色分级 · 静态 HTML 报告
        ├─ clean.py         白名单 · 硬保护 · 移入废纸篓
        └─ judge.py         可选：TypeSafe Jev 判断
```

- 界面是**单文件 HTML**，零第三方 JS/CSS 依赖，完全离线可用。
- 原生外壳是 Swift + WKWebView；清理引擎是 Python 脚本，来自 `macos-deep-optimize` 技能，本仓库 `app/skill-scripts/` 是权威副本。
- 唯一的外发请求是用户主动点击的「运行 AI 判断」：发送目录路径、大小、最后修改时间与子目录名，不发送文件内容。

## 安装

要求：macOS 13+、Xcode Command Line Tools（提供 `swiftc` 与 `python3`）。

```bash
git clone https://github.com/baiiiii521/mac-cleanup-app.git
cd mac-cleanup-app

# 原生 App → ~/Applications/Mac 清理助手.app
bash native/build_native.sh

# 或者：网页版外壳（界面在浏览器里）
bash build.sh
```

构建脚本会做 ad-hoc 签名。首次打开若被 Gatekeeper 拦截，右键点图标 →「打开」，或执行：

```bash
xattr -dr com.apple.quarantine ~/Applications/Mac\ 清理助手.app
```

## 使用

1. **开始体检**（只读扫描）——顶部实时显示磁盘可用空间。
2. **看报告**——三色清单、后台服务与大文件；也可以在新标签页打开完整 HTML。
3. **逐项勾选**——绿色默认全选，黄色只允许纯缓存子目录，红色与 AI 未判断项不可自动清理。
4. **先预览，再执行**——预览只列勾选项；执行后给出移入废纸篓的项数与空间变化。想真正释放空间，再点「清空废纸篓」（需输入确认语）。

### 自检

不点按钮也能验证整条链路：

```bash
"$HOME/Applications/Mac 清理助手.app/Contents/MacOS/MacCleanup" --selftest
```

走的是界面按钮同一条代码路径：引擎是否就绪、清单条数、AI 结果是否并入、勾选 2 项是否只处理 2 项、确认文本错误是否被拒、空勾选是否被拒等 10 项检查。

## 项目结构

```text
mac-cleanup-app/
├── app/
│   ├── launch                 .app 入口：挑 Python → 起服务 → 开界面
│   ├── server.py              本地 HTTP 外壳与状态管理
│   ├── skill-scripts/         清理引擎（权威副本，已入库）
│   ├── index.html             界面（单文件、零外部资源）
│   └── Info.plist             应用元信息
├── native/                    Swift + WKWebView 原生外壳
├── tools/                     图标、截图、交互验证脚本
├── docs/mole-notes/           清理规则调研笔记
├── promo/                     宣传片素材与渲染脚本
└── build.sh                   网页版外壳打包
```

运行状态与日志：

```text
~/.local/state/mac-cleanup-app/                  scan.json / report.html / port / server.log
~/.local/state/macos-deep-optimize/operations.log 清理操作日志
```

## 实测

本机 2026-10-18 复测（P7 之后）：

- 磁盘 228.3 GB，已用 152.3 GB，可用 47.2 GB（占用 67%）。
- 🟢 可自动清理 **9 项 4.65 GB**；🟡 需你判断 **42 项 29.3 GB**；🔴 1 项。
- 扫描耗时 **53.2 s**（优化前 86.7 s，−39%）。
- 识别出 4 组已卸载残留、12 项可重建构建产物、2 个残留安装包。

数字来自真实扫描，不是演示数据。

## 已知限制

- 仅支持 macOS；依赖系统自带的 `python3`（缺失时执行 `xcode-select --install`）。
- 应用为 ad-hoc 签名，没有 Apple 公证；首次打开需要手动放行一次。
- 清理默认进废纸篓，不会立刻释放空间；清空废纸篓不可恢复，需要单独确认。
- AI 判断需要 TypeSafe Key（环境变量 `TYPESAFE_API_KEY` 或 `~/.config/typesafe/key`），且会外发目录元数据；不开启不影响其它功能。
- 绿色清单偏保守，宁可少清也不误删；想清更多需要逐项判断。
- 没有自动化测试套件；改动后用 `--selftest` 与 `node tools/verify-interaction.js` 验证。

## 致谢

- 清理规则与安全模型来自 `macos-deep-optimize` 技能
- `docs/mole-notes/` 参考了 [mole](https://github.com/tw93/Mole) 的规则与保护清单
- AI 判断由 TypeSafe Jev 提供

## License

未声明开源许可证。如需复用代码或规则，请先联系作者。
