# Mac 清理助手（mac-cleanup-app）

把 `macos-deep-optimize` 技能（只读扫描 + 三色分级 + 安全清理）封装成一个**双击就能用的 macOS 应用**——
不用开终端、不用记命令，打开就是界面。

> 已安装：**~/Applications/Mac 清理助手.app**
> 打开方式：Finder → 应用程序 → 「Mac 清理助手」；或启动台按 F4 搜索；或 `open ~/Applications/Mac\ 清理助手.app`

## 界面流程（四步，都是点按钮）

1. **体检**（只读扫描，通常 1-3 分钟）→ 顶部实时显示磁盘可用空间
2. **打开完整报告** → 新标签页打开 HTML 报告：磁盘总览（三色分段进度条）、内存/负载诊断、🟢🟡🔴 三色清单、后台服务、大文件清单
3. **预览将清理的内容**（默认只预览，什么都不删）→ 看清楚每一项的大小和路径
4. **执行清理** → 需要在输入框敲「确认清理」；执行后默认**移到废纸篓（可逆）**，并显示可用空间前后对比
   - 想真正释放空间再点**清空废纸篓**（需敲「清空废纸篓」确认）——废纸篓和原文件同卷，不清空不释放

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
bash build.sh                 # 重新组装 + 安装到 ~/Applications
python3 tools/make_icon.py app/AppIcon.png   # 重新生成图标（可选）
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
