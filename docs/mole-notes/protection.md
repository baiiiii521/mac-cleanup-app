# Mole 保护规则与归属解析 —— 供 Python 实现的规格说明

> 分析对象：`/tmp/mole`（Mole v1.61.0，commit `8c55e8c`，GPLv3）
> 分析日期：2026-10-18
> 方法：逐行读源码；本文每一条结论都标注 `文件:行号`。凡是源码里**没有**的规则，本文明确写「源码中不存在」，不做推测。

## 0. 先说结论：Mole 的保护模型是 5 层串联

```
删除请求
  ├─ L1 validate_path_for_deletion()      绝对路径/符号链接/../控制字符/关键根目录   file_ops.sh:1138
  ├─ L2 should_protect_path()             路径级精细规则（容器、EDR、偏好、iCloud…）   app_protection.sh:384
  ├─ L3 should_protect_data()             按 bundle id 的数据保护（DATA_PROTECTED_BUNDLES）app_protection.sh:182
  ├─ L4 is_path_whitelisted()             用户白名单（safe_remove 内再查一次）            app_protection.sh:660 / file_ops.sh:1508-1511
  └─ L5 safe_remove 最终身份校验           dev:inode + parent:inode + mtime 三元组         file_ops.sh:1451 / 3210
```

关键设计事实（Python 实现必须复刻的三点）：

1. **默认拒绝 + 每次删除前重算**。`_safe_clean_impl()` 对每个目标先 `should_protect_path` 再 `is_path_whitelisted`（`bin/clean.sh:900-908`），到 `safe_remove` 落地前**再算一遍**（`file_ops.sh:1344-1347`）。判断函数是纯函数、无状态，可以随便重入。
2. **保护 = 判定函数，不是清单**。`should_protect_path` 里既有静态数组也有硬编码 `case`（`app_protection.sh:384-655`），两者是**并列**关系，不是「清单命中才保护」。
3. **「不知道」一律当「危险」**。所有探测函数都是三态：0=确认、1=确认否、≥2 或 ≥128=不知道 → 保护。见 `mole_clean_process_guard`（`base.sh:1710`）、`bundle_has_installed_app`（`bundle_resolver.sh:41`）、`_mole_paths_have_open_handle`（`file_ops.sh:760`）。

---

## 1. 系统关键组件清单（绝不可碰）

### 1.1 `SYSTEM_CRITICAL_BUNDLES`（`app_protection_data.sh:19-160`）

数组本身有 **129** 个元素（`bash` 实测 `${#SYSTEM_CRITICAL_BUNDLES[@]}`），按注释分成 5 组。**注意：全部是大小写敏感的 glob**，不是正则。

**组 1：`/System/Applications` 核心应用**（`app_protection_data.sh:20-64`）

```
com.apple.finder            com.apple.dock              com.apple.Safari
com.apple.mail              com.apple.systempreferences com.apple.SystemSettings
com.apple.Settings*         com.apple.controlcenter*    com.apple.Spotlight
com.apple.notificationcenterui  com.apple.loginwindow   com.apple.Preview
com.apple.TextEdit          com.apple.Notes             com.apple.reminders
com.apple.iCal              com.apple.AddressBook       com.apple.Photos
com.apple.AppStore          com.apple.calculator        com.apple.Dictionary
com.apple.ScreenSharing     com.apple.ActivityMonitor   com.apple.Console
com.apple.DiskUtility       com.apple.KeychainAccess    com.apple.DigitalColorMeter
com.apple.grapher           com.apple.Terminal          com.apple.ScriptEditor2
com.apple.VoiceOverUtility  com.apple.BluetoothFileExchange
com.apple.print.PrinterProxy  com.apple.systempreferences*  com.apple.SystemProfiler
com.apple.FontBook          com.apple.ColorSyncUtility  com.apple.audio.AudioMIDISetup
com.apple.DirectoryUtility  com.apple.NetworkUtility    com.apple.exposelauncher
com.apple.MigrateAssistant  com.apple.RAIDUtility       com.apple.BootCampAssistant
```

**组 2：macOS 26 bundle drift 审计补入**（`app_protection_data.sh:66-100`）。注释明确说「IDs are matched case-sensitively, so the lowercase variants below are the exact values those bundles report」——即下面这些**小写形式就是系统真实上报值**，不能改成驼峰：

```
com.apple.apps.launcher      com.apple.backup.launcher    com.apple.bootcampassistant
com.apple.Chess              com.apple.clock              com.apple.FaceTime
com.apple.findmy             com.apple.freeform           com.apple.games
com.apple.GenerativePlaygroundApp  com.apple.helpviewer   com.apple.Home
com.apple.Image_Capture      com.apple.journal            com.apple.Magnifier
com.apple.Maps               com.apple.mobilephone        com.apple.MobileSMS
com.apple.news               com.apple.Passwords          com.apple.PhotoBooth
com.apple.printcenter        com.apple.QuickTimePlayerX   com.apple.ScreenContinuity
com.apple.screenshot.launcher  com.apple.shortcuts        com.apple.siri.launcher
com.apple.Stickies           com.apple.stocks             com.apple.TV
com.apple.VoiceMemos         com.apple.weather
```

**组 3：系统服务与守护进程**（`app_protection_data.sh:102-124`）—— 这是「系统设置 / TCC / 登录项 / 生物识别 / 后台任务管理」真正的落点：

```
com.apple.SecurityAgent              ← 授权对话框
com.apple.CoreServices*              ← LaunchServices 本体
com.apple.SystemUIServer
com.apple.backgroundtaskmanagement*   ← 后台任务管理（BTM）
com.apple.loginitems*                 ← 登录项
com.apple.sharedfilelist*             ← 最近使用 / sfl
com.apple.sfl*
com.apple.coreservices*
com.apple.metadata*                   ← Spotlight 元数据
com.apple.MobileSoftwareUpdate*
com.apple.SoftwareUpdate*
com.apple.installer*
com.apple.frameworks*
com.apple.security*                   ← 含 securityd/security 工具链
com.apple.keychain*                   ← 钥匙串
com.apple.trustd*                     ← 证书信任评估
com.apple.securityd*
com.apple.cloudd*                     ← iCloud 守护
com.apple.iCloud*
com.apple.WiFi*
com.apple.airport*
com.apple.Bluetooth*
```

**组 4：系统输入法**（`app_protection_data.sh:126-131`）

```
com.apple.inputmethod.*   com.apple.inputsource*    com.apple.TextInput*
com.apple.CharacterPicker*  com.apple.PressAndHold*
```

**组 5：非 `com.apple.*` 的历史遗留模式**（`app_protection_data.sh:133-159`）—— 这些是**裸词**，会被拿去和路径、文件名做 glob，所以极宽：

```
loginwindow  dock  systempreferences  finder  safari  backgroundtaskmanagementagent
keychain*  security*  bluetooth*  wifi*  network*  tcc
notification*  accessibility*  universalaccess*  HIToolbox*
textinput*  TextInput*  keyboard*  Keyboard*  inputsource*  InputSource*
keylayout*  KeyLayout*  GlobalPreferences  .GlobalPreferences
```

> ⚠️ **`tcc` 是一个裸词**（`app_protection_data.sh:145`）。任何路径里含 `tcc` 三个字母（例如 `~/Library/Caches/com.foo.tcchelper`）都会命中。Python 移植时如果照抄，误伤面很大——建议只保留 `com.apple.tcc*` 和 `*/TCC/*` 这类带边界的形态。

### 1.2 补充：`should_protect_data` 的内联快路径（`app_protection.sh:185-242`）

比数组更早生效、也更粗的一层（`case` 语句，`nocasematch` 未开启，故大小写敏感）：

```bash
com.apple.* | loginwindow | dock | systempreferences | finder | safari   → 保护  (:186)
org.cups.*                                                              → 保护  (:192)
backgroundtaskmanagement* | keychain* | security* | bluetooth* | wifi* | network* | tcc  → 保护 (:195)
notification* | accessibility* | universalaccess* | HIToolbox*          → 保护  (:198)
*inputmethod* | *InputMethod* | *IME␠ | textinput* | TextInput*          → 保护  (:201)
keyboard* | Keyboard* | inputsource* | InputSource* | keylayout* | KeyLayout* → 保护 (:204)
GlobalPreferences | .GlobalPreferences | org.pqrs.Karabiner*             → 保护  (:207)
```

> 注意 `:201` 的 `*IME ` **带一个尾随空格**（原文如此，`cat -v` 已确认）。它的本意大概是 `*IME`，但实际语义是「以 `IME` + 空格结尾」。这是 Mole 的一个真实缺陷，Python 侧应写成 `*IME` 并另加词边界判断。

`org.cups.*` 值得单列：注释说明是为了保护 `~/Library/Preferences/org.cups.PrintingPrefs.plist`（默认打印机/最近打印机），因为 CUPS 是 OS 子系统、没有对应的 .app，否则会被当成孤儿（`app_protection.sh:189-193`）。

### 1.3 `is_critical_system_component`：给「容器/应用名」用的粗筛（`app_protection.sh:53-72`）

开启 `nocasematch`，只匹配 10 个词根：

```
*backgroundtaskmanagement*  *loginitems*  *systempreferences*  *systemsettings*
*settings*  *preferences*  *controlcenter*  *biometrickit*  *sfl*  *tcc*
```

调用点：`process_container_cache`（`lib/clean/user.sh:1335`）、Application Support 泛扫（`user.sh:2531`）。注意 `*biometrickit*` 是**全文唯一的生物识别保护**——Mole 没有 `com.apple.BiometricKit*` 这类 bundle id 条目，只有这一个子串匹配。

### 1.4 SIP：Mole 不主动枚举 SIP 路径

源码中**没有** SIP 路径清单，也没有 `csrutil` 前置判断参与删除决策。SIP 是通过**错误码**被吸收的：

- `MOLE_ERR_SIP_PROTECTED=10`（`file_ops.sh:14`）
- sudo 删除的输出里匹配 `*"Operation not permitted"*` → 返回 10，日志写 `sip/mdm protected`（`file_ops.sh:2247-2249`）
- `diagnose_removal_failure` 把它翻译成 `protected by macOS (SIP/MDM)`（`file_ops.sh:4113-4114`）

`csrutil status` 只在 `mo --version` 的信息输出里出现（`lib/manage/update.sh:1044-1049, 1087`），**不参与任何保护判定**。

真正承担「SIP 等价物」的是 `_mole_is_critical_deletion_path()`（`file_ops.sh:1022-1107`）这个纯字符串/inode 清单：

```
/ /bin /bin/* /dev /dev/* /sbin /sbin/* /usr /usr/* /System /System/*
/Library /Library/Apple /Library/Apple/* /Library/Application Support
/Library/Extensions /Library/Extensions/* /Library/Keychains /Library/Keychains/*
/Applications /Applications/Finder.app /Applications/Safari.app
/Volumes /opt /opt/homebrew /Users /Users/Shared /Users/Guest /Users/Guest/*
/private /private/tmp /etc /etc/* /private/etc /private/etc/*
/var /var/db /var/db/* /var/audit /var/audit/* /var/root
/private/var /private/var/tmp /private/var/folders /private/var/db
/private/var/db/* /private/var/audit /private/var/audit/* /private/var/root
```

例外：`/usr/local/*` 与 `/opt/homebrew/*` **显式放行**（`file_ops.sh:1029-1031`），因为 Homebrew 装在那里；但 `/opt/homebrew` 根本身仍在拒绝列表（`:1046`）。

另外还有 inode 级别的兜底（`file_ops.sh:1072-1107`）：对 `exact_roots` 做 `same-existing-file` 比较、对 `protected_trees` 做 `is-within-existing-root` 比较，专门防 `/SYSTEM`、`/OPT/HOMEBREW` 这类**大小写别名**（APFS 大小写不敏感，字符串检查会漏）。

### 1.5 可卸载的 Apple 应用白名单（`app_protection_data.sh:163-175`）

存在它的原因是 `com.apple.*` 不能一刀切，否则 Xcode 卸不掉：

```
com.apple.dt.*          Xcode / Instruments / FileMerge
com.apple.FinalCut*     Final Cut Pro
com.apple.Motion        com.apple.Compressor
com.apple.logic*        Logic Pro
com.apple.garageband*   GarageBand
com.apple.iMovie        com.apple.iWork.*
com.apple.MainStage*    com.apple.server.*
com.apple.Playgrounds   Swift Playgrounds
```

判定顺序**不可颠倒**（`app_protection.sh:126-140`）：先查 `APPLE_UNINSTALLABLE_REGEX`，命中就 `return 1`（允许卸载）；再查 `SYSTEM_CRITICAL_REGEX`。

---

## 2. 终端安全 / VPN / 备份 / 同步类软件清单

### 2.1 必须走官方卸载器的厂商（`app_protection_data.sh:179-187`）

格式：`vendor|bundle-id-前缀(逗号分隔)|名称片段(逗号分隔)`。解析逻辑在 `official_uninstaller_vendor()`（`app_protection.sh:144-179`）：bundle id 与**小写后的**前缀做 `== "$prefix"*` 前缀匹配；名称/路径做 `*"$fragment"*` 子串匹配。命中即返回厂商名，调用方把它放进 `blocked_apps` 并跳过（`lib/uninstall/batch.sh:1894-1898`）。

```
ESET|com.eset.|eset management agent,eset remote administrator agent,eset endpoint security,eset endpoint antivirus
Jamf|com.jamf.,com.jamfsoftware.|jamf connect,jamf protect,jamf self service
CrowdStrike|com.crowdstrike.|crowdstrike,falcon
SentinelOne|com.sentinelone.,com.sentinel-labs.|sentinelone,sentinel agent
GlobalProtect|com.paloaltonetworks.|globalprotect
Cisco|com.cisco.anyconnect,com.cisco.secureclient|cisco secure client,cisco anyconnect
Alibaba Cloud Security|com.alibaba.security.|
```

### 2.2 EDR 缓存前缀：`ENDPOINT_SECURITY_BUNDLE_PREFIXES`（`app_protection_data.sh:195-206`）

这是**最容易误删**的一类，Mole 用独立谓词 `is_endpoint_security_cache_path()`（`app_protection.sh:266-294`）守它：

```
com.crowdstrike.      com.sentinelone.      com.sentinel-labs.   com.eset.
com.jamf.             com.jamfsoftware.     com.paloaltonetworks.
com.cisco.anyconnect  com.cisco.secureclient  com.alibaba.security.
```

判定算法（注意顺序）：

1. **快速拒绝**：路径必须是 `/private/var/folders/*` 或 `/var/folders/*`；否则再做一次 `_mole_path_is_within_existing_root "$path" "/private/var/folders"` 的 realpath 判断（`app_protection.sh:271-279`）。这一步防的是「别的目录里恰好有 `var/folders` 字样」。
2. 开 `nocasematch`，对每个前缀做 `[[ "$path" == *"$prefix"* ]]`（`:286-291`）。

注释说明了代价与动机（`app_protection.sh:255-265`）：删 EDR 传感器容器里的任何东西（Metal/GPU 着色器缓存、`.../C/...`、code-sign clone `.../X/<bundle-id>.code_sign_clone`、`.../T/...`）会触发篡改检测（CrowdStrike `MacFalconSensorTamper`，MITRE T1562.001），企业安全侧会当成恶意软件上报。回收量只有几 MB，所以**永不触碰**。

同一谓词在三个地方生效，保证不靠单点：
- `should_protect_path`（`app_protection.sh:498-500`）
- `validate_path_for_deletion`（`file_ops.sh:1315-1323`），**在 `/private/var/folders` 白名单之前**，这样所有删除调用方都被覆盖
- GPU 缓存扫描（`lib/clean/system.sh:626-628`）
- Go 侧 `cmd/analyze/delete.go:526-556` 有一份**逐字复刻**的 `endpointSecurityBundlePrefixes`

### 2.3 `DATA_PROTECTED_BUNDLES`（`app_protection_data.sh:209-621`）

**33 个分组、352 条 glob**（实测 `${#DATA_PROTECTED_BUNDLES[@]}`）。这是「清理时不许碰、但允许卸载」的清单（对比 `SYSTEM_CRITICAL_BUNDLES` 是「连卸载都不许」）。完整分组与行号：

| 分组 | 行号 | 代表模式 |
|---|---|---|
| 输入法 | 210-218 | `com.sogou.inputmethod.*` `com.baidu.inputmethod.*` `com.googlecode.rimeime.*` `im.rime.*` `*.inputmethod` `*.InputMethod` `*IME` |
| 清理工具（互斥保护） | 220-228 | `com.nektony.*` `com.macpaw.*` `com.freemacsoft.AppCleaner` `com.omnigroup.omnidisksweeper` `com.daisydiskapp.*` `com.tunabellysoftware.*` `com.grandperspectiv.*` `com.binaryfruit.*` |
| 密码管理器 | 230-240 | `com.1password.*` `com.agilebits.*` `com.lastpass.*` `com.dashlane.*` `com.bitwarden.*` `com.keepassx.*` `org.keepassx.*` `org.keepassxc.*` `com.authy.*` `com.yubico.*` |
| IDE / 编辑器 | 242-255 | `com.jetbrains.*` `JetBrains*` `com.microsoft.VSCode` `com.sublimetext.*` `com.apple.dt.Xcode` `com.panic.Nova` `abnerworks.Typora` |
| AI / LLM | 257-280 | `com.todesktop.*` `Cursor` `com.anthropic.claude*` `Claude` `com.openai.chat*` `ChatGPT` `com.openai.codex` `Codex` `codex-runtimes` `com.ollama.ollama` `Ollama` `com.lmstudio.lmstudio` `co.supertool.chatbox` `page.jan.jan` `com.huggingface.huggingchat` `Gemini` `com.perplexity.Perplexity` `com.drawthings.DrawThings` `com.divamgupta.diffusionbee` `com.exafunction.windsurf` `com.quora.poe.electron` `chat.openai.com.*` |
| 数据库客户端 | 282-293 | `com.sequelpro.*` `com.sequel-ace.*` `com.tinyapp.*` `com.dbeaver.*` `com.navicat.*` `com.mongodb.compass` `com.redis.RedisInsight` `com.pgadmin.pgadmin4` |
| API / 网络调试 | 295-304 | `com.postmanlabs.mac` `com.konghq.insomnia` `com.CharlesProxy.*` `com.proxyman.*` `com.getpaw.*` `com.charlesproxy.charles` `com.telerik.Fiddler` `com.usebruno.app` |
| **代理 / VPN（Clash 系）** | 306-326 | `com.clash.*` `ClashX*` `clash-*` `Clash-*` `*-clash` `*-Clash` `clash.*` `Clash.*` `clash_*` `*clash-verge*` `*Clash-Verge*` `clashverge*` `ClashVerge*` `com.nssurge.surge-mac` `*surge*` `*Surge*` `mihomo*` `*openvpn*` `*OpenVPN*` `net.openvpn.*` |
| **代理客户端** | 328-342 | `*ShadowsocksX-NG*` `com.qiuyuzhou.*` `*v2ray*` `*V2Ray*` `*v2box*` `*V2Box*` `*nekoray*` `*sing-box*` `*OneBox*` `*hiddify*` `*Hiddify*` `*loon*` `*Loon*` `*quantumult*` |
| **Mesh / 企业 VPN** | 344-356 | `*tailscale*` `io.tailscale.*` `*zerotier*` `com.zerotier.*` `*1dot1dot1dot1*`（Cloudflare WARP）`*cloudflare*warp*` `org.amnezia.*` `*amnezia*` `*Amnezia*` `com.wireguard.*` `*wireguard*` `*WireGuard*` |
| **商业 VPN** | 358-365 | `*nordvpn*` `*expressvpn*` `*protonvpn*` `*surfshark*` `*windscribe*` `*mullvad*` `*privateinternetaccess*` |
| 屏保 / 壁纸 | 367-371 | `*Aerial.saver*` `com.JohnCoates.Aerial*` `*Fliqlo*` `*fliqlo*` |
| Git / 版本控制 | 373-381 | `com.github.GitHubDesktop` `com.sublimemerge` `com.torusknot.SourceTreeNotMAS` `com.git-tower.Tower*` `com.gitfox.GitFox` `com.github.Gitify` `com.fork.Fork` `com.axosoft.gitkraken` |
| 终端 | 383-392 | `com.googlecode.iterm2` `net.kovidgoyal.kitty` `io.alacritty` `com.github.wez.wezterm` `com.hyper.Hyper` `com.mizage.divvy` `com.fig.Fig` `dev.warp.Warp-Stable` `com.termius-dmg` |
| Docker / 虚拟化 | 394-404 | `com.docker.docker` `dev.orbstack.OrbStack` `dev.orbstack.*` `dev.kdrag0n.MacVirt` `com.utmapp.UTM` `com.vmware.fusion` `com.parallels.desktop.*` `org.virtualbox.app.VirtualBox` `com.vagrant.*` `com.orbstack.OrbStack` |
| 系统监控 | 406-413 | `com.bjango.istatmenus*` `eu.exelban.Stats` `com.monitorcontrol.*` `com.bresink.system-toolkit.*` `com.mediaatelier.MenuMeters` `com.activity-indicator.app` `net.cindori.sensei` |
| 窗口管理 | 415-431 | `com.macitbetter.*` `com.hegenberg.*` `com.manytricks.*` `com.divisiblebyzero.*` `com.koingdev.*` `com.if.Amphetamine` `com.lwouis.alt-tab-macos` `net.matthewpalmer.Vanilla` `com.lightheadsw.Caffeine` `com.contextual.Contexts` `com.amethyst.Amethyst` `com.knollsoft.Rectangle` `com.knollsoft.Hookshot` `com.surteesstudios.Bartender` `com.gaosun.eul` `com.pointum.hazeover` |
| 启动器 / 自动化 | 433-443 | `com.runningwithcrayons.Alfred` `com.raycast.*` `com.raycast-x.*` `com.blacktree.Quicksilver` `com.stairways.keyboardmaestro.*` `com.manytricks.Butler` `com.happenapps.Quitter` `com.pilotmoon.scroll-reverser` `org.pqrs.Karabiner-Elements` `com.apple.Automator` |
| 笔记 | 445-462 | `com.bear-writer.*` `com.typora.*` `com.ulyssesapp.*` `com.literatureandlatte.*` `com.dayoneapp.*` `notion.id` `md.obsidian` `com.logseq.logseq` `com.evernote.Evernote` `com.onenote.mac` `net.shinyfrog.bear` `com.goodnotes.GoodNotes` `com.marginnote.MarginNote*` `com.roamresearch.*` `com.reflect.ReflectApp` `com.inkdrop.*` |
| 设计 / 创意 | 464-488 | `com.adobe.*` `com.avid.mediacomposer*` `com.bohemiancoding.*` `com.figma.*` `com.framerx.*` `com.zeplin.*` `com.invisionapp.*` `com.principle.*` `com.pixelmatorteam.*` `com.affinitydesigner.*` `com.affinityphoto.*` `com.affinitypublisher.*` `com.linearity.curve` `com.canva.CanvaDesktop` `com.maxon.cinema4d` `com.autodesk.*` `com.sketchup.*` `com.native-instruments.*` `com.fabfilter.*` `com.paceap.*` `com.izotope.*` `iZotope` `com.lasersoft-imaging.*` `app.cotypist.Cotypist` |
| 通讯 | 490-511 | `com.tencent.xinWeChat` `com.tencent.qq` `com.alibaba.DingTalkMac` `com.alibaba.AliLang.osx` `com.alibaba.alilang3.osx.ShipIt` `com.alibaba.AlilangMgr.QueryNetworkInfo` `us.zoom.xos` `com.microsoft.teams*` `com.slack.Slack` `com.hnc.Discord` `app.legcord.Legcord` `org.telegram.desktop` `ru.keepcoder.Telegram` `net.whatsapp.WhatsApp` `com.skype.skype` `com.cisco.webexmeetings` `com.ringcentral.RingCentral` `com.readdle.smartemail-Mac` `com.airmail.*` `com.postbox-inc.postbox` `com.tinyspeck.slackmacgap` |
| 任务管理 | 513-526 | `com.omnigroup.OmniFocus*` `com.culturedcode.*` `com.todoist.*` `com.any.do.*` `com.ticktick.*` `com.microsoft.to-do` `com.trello.trello` `com.asana.nativeapp` `com.clickup.*` `com.monday.desktop` `com.airtable.airtable` `com.notion.id` `com.linear.linear` |
| **文件传输 / 同步** | 528-535 | `com.panic.transmit*` `com.binarynights.ForkLift*` `com.noodlesoft.Hazel` `com.cyberduck.Cyberduck` `io.filezilla.FileZilla` `com.apple.Xcode.CloudDocuments` `com.synology.*` |
| **云存储 / 备份** | 537-562 | `com.dropbox.*` `com.getdropbox.*` `*dropbox*` `ws.agile.*` **`com.backblaze.*`** **`*backblaze*`** `com.box.desktop*` `*box.desktop*` `com.microsoft.OneDrive*` `com.microsoft.SyncReporter` `*OneDrive*` `com.google.GoogleDrive` `com.google.keystone*` `*GoogleDrive*` `com.amazon.drive` `com.apple.bird` `com.apple.CloudDocs*` `com.displaylink.*` `com.fujitsu.pfu.ScanSnap*` `com.citrix.*` `org.xquartz.*` `us.zoom.updater*` `com.DigiDNA.iMazing*` **`com.shirtpocket.*`**（SuperDuper）`homebrew.mxcl.*` |
| 远程桌面 | 564-570 | `org.chromium.chromoting*` `com.google.chrome_remote_desktop*` `com.teamviewer.*` `com.realvnc.*` `com.logmein.*` `com.anydesk.*` |
| 截图 / 录屏 | 572-584 | `com.cleanshot.*` `com.xnipapp.xnip` `com.reincubate.camo` `com.tunabellysoftware.ScreenFloat` `net.telestream.screenflow*` `com.techsmith.snagit*` `com.techsmith.camtasia*` `com.obsidianapp.screenrecorder` `com.kap.Kap` `com.getkap.*` `com.linebreak.CloudApp` `com.droplr.droplr-mac` |
| 媒体 | 586-598 | `com.spotify.client` `com.apple.Music` `com.apple.podcasts` `com.apple.BKAgentService` `com.apple.iBooksX` `com.apple.iBooks` `com.blackmagic-design.*` `com.colliderli.iina` `org.videolan.vlc` `io.mpv` `tv.plex.player.desktop` `com.netease.163music` |
| 浏览器 | 600-602 | `Firefox` `org.mozilla.*` |
| 科研 / 专业软件 | 604-613 | `com.crowdstrike.*` `com.kolide.*` `com.sas.*` `com.mathworks.*` `com.ibm.spss.*` `com.wolfram.*` `com.stata.*` `org.rstudio.*` `com.tableausoftware.*` |
| 授权 / 商店 | 615-620 | `com.paddle.Paddle*` `com.quicken.*` `com.setapp.DesktopClient` `com.devmate.*` `org.sparkle-project.Sparkle*` |

### 2.4 关于「Arq / Time Machine」——重要澄清

需求里提到 Arq 和 Time Machine。**源码事实**：

- **Arq：`lib/` 与 `bin/` 中不存在任何 `arq` 匹配**（`grep -rin 'arq'` 只命中 `lib/clean/dev.sh:4149` 的 `~/.sonar/*`，是 `sonar` 里的 `ar` 子串，与 Arq 无关）。Mole **没有** Arq 的保护规则。
- **Time Machine：`app_protection.sh` 和 `app_protection_data.sh` 里没有 TimeMachine 条目**。Mole 对 TM 的保护是**行为式**的，不是清单式的：
  - `lib/optimize/tasks.sh:359` 明确记录「Removed: opt_local_snapshots - Deletes user Time Machine recovery points, breaks backup continuity」——即主动**删掉**了这个功能。
  - 本地快照只做**只读报告**：`tmutil listlocalsnapshots /` + 正则 `com\.apple\.TimeMachine\.[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}` 计数（`lib/clean/user.sh:3420-3431`、`lib/clean/system.sh:1430-1432`）。
  - 失败备份清理有 4 道门：`defaults read /Library/Preferences/com.apple.TimeMachine AutoBackup` 必须是 `^[01]$`（`system.sh:941`）；`tmutil` 状态必须**确认空闲**（`system.sh:975-995`，三态里 running 和 unknown 都跳过）；`/Volumes/*` 下必须真有 `Backups.backupdb` 或 `.MobileBackups`（`system.sh:1003-1012`）；候选目录必须老于 `MOLE_TM_BACKUP_SAFE_HOURS=48` 小时（`base.sh:158`、`system.sh:1099`）。
  - 网络文件系统上的备份卷直接跳过：`nfs | smbfs | afpfs | cifs | webdav | unknown) continue`（`system.sh:1049-1051`）。
- **Backblaze：只有 `com.backblaze.*` 和 `*backblaze*` 两条**（`app_protection_data.sh:542-543`），属于 `DATA_PROTECTED_BUNDLES` 的「云存储 / 备份」组。这条 `*backblaze*` 是**大小写敏感的子串 glob**，能同时命中路径和文件名。
- 其他备份软件：只有 `com.shirtpocket.*`（SuperDuper，`:561`）、`com.DigiDNA.iMazing*`（`:560`）、`ws.agile.*`（`:541`）。**CrashPlan / Resilio / Syncthing / pCloud / Carbon Copy Cloner / Duplicacy 均无规则**。

### 2.5 `LAUNCH_AGENT_NAME_COMMON_WORDS`（`app_protection_data.sh:629`）

当一个 App 的**显示名恰好是**这些词之一时，跳过「按名称匹配 plist」的路径（bundle id 匹配仍然生效）。共 **40** 个词：

```
Music Notes Photos Finder Safari Preview Calendar Contacts Messages Reminders
Clock Weather Stocks Books News Podcasts Voice Files Store System Helper Agent
Daemon Service Update Sync Backup Cloud Manager Monitor Server Client Worker
Runner Launcher Driver Plugin Extension Widget Utility
```

它被 `find_app_files`（用户 LaunchAgents，`app_protection.sh:1574-1577`）和 `find_app_system_files`（系统 LaunchAgents/Daemons，`app_protection.sh:2116`）共用，注释明确说目的是「两个扫描保持对称」（`app_protection_data.sh:623-628`）。

---

## 3. 归属解析（`bundle_resolver.sh`）

### 3.1 接口

```bash
bundle_has_installed_app <bundle_id> [deadline_seconds]
# 0 = 有已安装的 app（或注册了同名 SMJobBless helper）
# 1 = 确认没有
# ≥128 = 被信号中断（必须向上传播）
```

`deadline_seconds` 默认 `SECONDS + MOLE_TIMEOUT_DISK_VERIFY_SEC`（=30s，`timeouts.sh:66`）。它是**绝对截止时间**，不是时长。

### 3.2 用到的数据源（按执行顺序）

**① 输入校验**（`bundle_resolver.sh:46-47`）
```bash
mole_is_reverse_dns_bundle_id "$bundle_id" || return 1
```
`base.sh:1077-1082` 的正则：
```
^[A-Za-z0-9][-A-Za-z0-9]*(\.[A-Za-z0-9][-A-Za-z0-9]*)+$
```
外加拒绝空值和字面量 `unknown`。**这一步是防注入的关键**：`bundle_id` 后面会被拼进 `mdfind` 的查询表达式和 `find` 的参数，不校验就能塞引号或 glob。

**② Spotlight 快路径**（`bundle_resolver.sh:49-72`）
```bash
mdfind "kMDItemCFBundleIdentifier == '$bundle_id'"
```
- 有 `run_with_timeout` 就用 `MOLE_TIMEOUT_QUICK_DETECT_SEC`（=2s），并且用 `_mole_timeout_with_deadline` 取 min（`:56-58`）
- 只取**第一行**：`hit="${hit%%$'\n'*}"`（`:64`）
- `rc ≥ 128`（信号）立刻向上返回（`:63`）
- 有输出就 `return 0`；**没输出/失败则继续往下走**，不直接判定「没装」

源码注释明确解释了为什么不信任 Spotlight（`bundle_resolver.sh:4-7`）：`/Applications` 索引可能关着、Homebrew 安装有时跳过 metadata importer、Spotlight 基本不索引 `.app` 内嵌的 helper。

**③ 文件系统慢路径**（`bundle_resolver.sh:74-191`）

扫描根（`bundle_resolver.sh:21-33`）：
```
/Applications
/Applications/Setapp
/Applications/Utilities
/System/Applications
/System/Applications/Utilities
$HOME/Applications
$HOME/Library/Application Support/Setapp/Applications
/opt/homebrew/Caskroom
/usr/local/Caskroom
/Library/Input Methods
$HOME/Library/Input Methods
```

每个根用 `find -maxdepth N -iname "*.app" -print0`（`:131-133`），**深度按根类型区分**（`:123-127`）：
- `*/Caskroom` 和 `*/Setapp/Applications` → `depth=3`（Caskroom 里是 `Caskroom/<cask>/<version>/<App>.app`）
- 其余 → `depth=1`

对每个 `.app`，按以下顺序判定命中：

| # | 判定 | 行号 | 说明 |
|---|---|---|---|
| a | `[ -e "$app/Contents/Library/LaunchServices/$bundle_id" ]` | 147 | **SMJobBless 特权 helper**：helper 以 bundle id 命名放在父 app 里 |
| b | `plutil -extract CFBundleIdentifier raw "$info"` 转小写后 `== bundle_id_lower` | 160-173 | 主判定 |
| c | 同上，但 `== parent_id_lower` | 172-173 | 见下 |
| d | 命中 `mapped_app_bundles` 白名单 | 177-186 | 仅 Microsoft |

**parent id 的推导**（`bundle_resolver.sh:88-95`）：如果 `bundle_id` 以 `.helper` / `.daemon` / `.agent` / `.xpc` / `.service` 结尾，就去掉这个后缀得到父 id。注释说明了两个真实 issue（`:76-84`）：
- issue #732：Homebrew 装的 `org.keepassxc.KeePassXC`，Spotlight 没索引到它的 launch agent
- issue #733：Adobe Acrobat DC 里的 ARMDC helper 放在 `Contents/Library/LaunchServices/<helper-bundle-id>`

**大小写处理**：macOS bundle id 名义上大小写不敏感，helper 后缀两种大小写都有（`com.foo.app.helper` 与 `com.stclairsoft.AppTamer.Helper`），所以两边都 `tr '[:upper:]' '[:lower:]'` 后比较（`:85-86, 170-171`）。引用 issue #1210。

**Microsoft 特殊映射**（`bundle_resolver.sh:97-108`）：只对两个 helper id 生效
```bash
com.microsoft.autoupdate.helper | com.microsoft.office.licensingV2.helper
```
映射到 Word / Excel / Powerpoint / Outlook / OneNote 五个 app bundle。注意这一支的比较**没有转小写**（`:180` 用 `"$app_bundle" == "$mapped_bundle"`）。

### 3.3 如何避免误报（这是最值得移植的部分）

这是全文件最核心的设计，逐条对应源码：

1. **未知 ≠ 安全**。任何探测失败都 `return 0`（= 当成还装着 = 保护），只有**走完全部扫描根仍没命中**才 `return 1`。具体：
   - 超时/中断：`[[ $app_scan_rc -ge 128 ]] && return "$app_scan_rc"`，否则 `return 0`（`:135-139`）
   - `create_temp_file` 失败 → `return 0`（`:117-119`）
   - `plutil` 读不出来 → `app_scan_found_or_unknown=true; break` → `return 0`（`:162-169`）
   - 循环中到截止时间 → `app_scan_found_or_unknown=true; break`（`:143-146`）
   - **变量名就叫 `app_scan_found_or_unknown`**——源码用命名把「找到了」和「不知道」合并成同一个出口。
2. **只信 plist，不信文件名**。命中必须来自 `Contents/Info.plist` 的 `CFBundleIdentifier`，或 `Contents/Library/LaunchServices/<id>` 这个路径存在性。`.app` 目录名本身不构成证据。
3. **不读 `defaults read`，用 `plutil -extract`**。注释给了实测数据（`app_protection.sh:1918-1921`）：`plutil -extract` ~2.3ms vs `defaults read` ~4.1ms，且 `defaults` 会经 `cfprefsd` 反序列化整个 plist。这个函数在保护循环里逐 app 调用。
4. **临时文件承载 find 输出**（`create_temp_file` + `-print0`）。不用进程替换（`< <(...)`），因为那会隐藏 producer 的退出码——超时后可能读到**半截列表**。同一模式在 `_mole_uninstall_materialize_find0`（`app_protection.sh:788-810`）被显式文档化：失败就把文件清空，宁可「没候选」也不要「半个候选」。
5. **每个根独立失败**。一个根扫不动，`return 0`（保守），而不是继续下一个根然后判「没装」。

### 3.4 调用方（说明它在什么位置生效）

| 调用点 | 用途 |
|---|---|
| `lib/clean/apps.sh:637` | 孤儿清理前的**最终复核**：`resolver 0 means installed or unknown; only an exact 1 authorizes cleanup`（`:640-641` 原注释） |
| `lib/clean/apps.sh:1300` | `/Library/PrivilegedHelperTools` 里的 helper 二进制，查父 app 是否还在 |
| `lib/clean/apps.sh:1500` | 系统服务（LaunchDaemons/PrivilegedHelperTools）孤儿判定 |
| `lib/clean/apps.sh:1698` | 同上的批量路径 |
| `lib/clean/hints.sh:154` | 提示扫描，判断 launch agent 的 app 是否还在 |
| `lib/optimize/tasks.sh:1010` | Spotlight 搜索规则清理：只有 `resolver_rc -eq 1` 才删规则 |
| `lib/uninstall/batch.sh` | 卸载后孤儿检测 |

**额外数据源**（`bundle_resolver` 之外，但属于同一套「这个 app 还在不在」的判断）：

- `scan_installed_apps()`（`lib/clean/apps.sh:150-419`）：**批量**建立已安装 bundle id 集合，供 `is_bundle_orphaned` 做 `grep -Fxq` 快查。扫描根（`:201-210`）比 resolver 多两个：`/System/Applications`、`$HOME/Applications`，同样含两个 Caskroom 与 Setapp。并发 `find -maxdepth 3 -type d -iname '*.app'`（`:227`）。
- 运行中进程（`lib/clean/apps.sh:311-341`）：
  - `osascript -e 'tell application "System Events" to get bundle identifier of every application process'`（`:315`）
  - 回退 `lsappinfo list`，用 `sed -n 's/.*"CFBundleIdentifier"="\([^"]*\)".*/\1/p'` 提取（`:332-334`）
  - 注释说明 `lsappinfo` 比 `osascript` 更可靠（`:330`）
- LaunchAgents 的 label（`lib/clean/apps.sh:344-357`）：`basename "$agent_path" .plist`
- **缓存**：`$HOME/.cache/mole/installed_apps_cache`，TTL 300s（`lib/clean/apps.sh:156-157`），且**必须**有完整性尾行 `# mole-installed-apps-cache:v3:complete`（`:7`、`:173`）才被采信——「不完整的 app 列表不能授权孤儿判定」。
- `mdfind` 结果内存缓存（`lib/clean/apps.sh:434-461`）：`_MOLE_MDFIND_FOUND` / `_MOLE_MDFIND_NOTFOUND` 两个换行分隔字符串，`case` glob 查，避免 bash 3.2 没有关联数组。**超时/错误不写缓存**（`:516-521` 注释：曾因 piped `head` 把 mdfind 超时当成「没装」，删了活 app 的数据）。

### 3.5 LaunchServices 的实际用法：**不用它做归属解析**

这是需求里需要澄清的一点。Mole 里 `lsregister` 只出现在**卸载之后**，从不参与「这个 app 是否还装着」：

- `get_lsregister_path()`（`lib/core/base.sh:124-146`）探测两个路径：
  ```
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
  /System/Library/CoreServices/Frameworks/LaunchServices.framework/Support/lsregister
  ```
  可用 `MOLE_LSREGISTER_PATH` 覆盖（设为空即禁用）。
- `unregister_app_bundle()`（`lib/uninstall/batch.sh:581-601`）：`lsregister -u "$app_path"`，**只删之前**把 app 从 LS 注销，让 Spotlight 的 app 列表不再有僵尸条目。
- `refresh_launch_services_after_uninstall()`（`batch.sh:603-614`）：`lsregister -gc`，**best-effort、有超时、失败忽略**。
- 反例，值得注意：`bin/clean.sh:1807-1813` 明确记录**不**做 stale-LaunchServices 清理——`lsregister -u` 在 macOS 15+ 对已消失的 app 一律返回 `-10814`，而 `-gc` 全量重建会让运行中的 VPN 扩展被误读成「刚重装」。所以 `mo optimize` 也取消了这个功能。

**结论**：`lsregister dump` 解析成本高（多秒）、格式不稳定、且 Mole 实测证明它**不可靠**。Python 实现应完全跳过它。

---

## 4. pkg receipts（`pkg_receipts.sh`）

### 4.1 它解决的问题

不是「判断路径属于哪个 pkg」，而是反过来的：**找出被 pkg 安装到非标准位置的 .app**，这样卸载器能列出它们。函数名即意图：`pkg_receipt_nonstandard_app_paths()`（`pkg_receipts.sh:12`）。

`lib/uninstall/batch.sh:1153-1180` 的注释说明它还承担**「是否还有别的安装占着这些残留」**的举证责任——这才是「不误判为残留」的真正机制。

### 4.2 具体命令与解析逻辑

**① 列出所有 receipt**（`pkg_receipts.sh:32-47`）
```bash
pkgutil --pkgs            # 超时 MOLE_PKG_RECEIPT_LIST_TIMEOUT（默认 3s）
```
这一步很便宜。注释（`:28-31`）说真正贵的是下面的逐 receipt `--files`——在装了 Xcode 的机器上能耗尽整个预算（issue #1383）。

**② 过滤 Apple 包**（`pkg_receipts.sh:116`）
```bash
[[ "$pkg_id" =~ ^com\.apple\. ]] && continue
```

**③ 逐 receipt 取文件清单**（`pkg_receipts.sh:118-150`）
```bash
pkgutil --files "$pkg_id"
```
`--files` 输出的是**相对于安装根的路径**（首字符可能带 `/`，`:161` 用 `${rel_path#/}` 剥掉）。

**④ 从文件路径反推 .app 路径**（`pkg_receipts.sh:165-176`）—— 这是核心的字符串处理，**只认两个安装根**：

```bash
case "$candidate" in
    /usr/local/*.[aA][pP][pP]) app_path="$candidate" ;;
    /opt/*.[aA][pP][pP])       app_path="$candidate" ;;
    /usr/local/*.[aA][pP][pP]/* | /opt/*.[aA][pP][pP]/*)
        # 从 "....app/Contents/..." 里切出 "....app"
        app_prefix="${candidate%%.[aA][pP][pP]/*}"
        app_suffix_and_rest="${candidate#"$app_prefix"}"
        app_suffix="${app_suffix_and_rest%%/*}"
        app_path="${app_prefix}${app_suffix}"
        ;;
    *) continue ;;
esac
```

要点：
- 只覆盖 `/usr/local/` 和 `/opt/`（Homebrew 的两个前缀），其他位置（包括 `/Applications`）**不处理**——因为那些位置已经被常规的 app 目录扫描覆盖了。
- `.[aA][pP][pP]` 的字符类写法是为了在**大小写不敏感**的 case 语句里也能区分 `.app` / `.APP` / `.App`。
- 从子路径反推 app 根时用「第一个 `.app/` 之前的部分」+「第一个路径段」，即 `X.app/Contents/MacOS/foo` → `X.app`。

**⑤ 去重**（`pkg_receipts.sh:184-196`）。手写线性查找（bash 3.2 无关联数组），注释提到 `#1354`：bash 3.2 在 `set -u` 下对空数组展开会中止，所以用 `${seen_apps[@]+"${seen_apps[@]}"}`。

**⑥ 输出时校验存在性**（`pkg_receipts.sh:200-209`）
```bash
[[ -d "$seen_app" ]] && printf '%s\n' "$seen_app"
```
但**缓存里记录的是「路径存在与否都记」**（`:178-181` 注释）：app 可能正躺在废纸篓、或在自我更新的中间态，不能因为「暂时不存在」就从一份 complete 答案里消失——直到它的 receipt 变了为止。

### 4.3 缓存设计（这是「不误报」的机制核心）

缓存文件：`${MOLE_PKG_RECEIPT_CACHE_FILE:-$HOME/.cache/mole/pkg_receipt_apps_v1}`（`:19`）

**缓存键 = `pkgutil --pkgs` 全量输出 + `/var/db/receipts/*.plist` 的 `stat -f '%m %N'`（mtime + 名字）**，两者拼起来过 `cksum`（`:53-63`）：

```bash
receipt_stamps=$(LC_ALL=C "$STAT_BSD" -f '%m %N' "$receipts_dir"/*.plist 2>/dev/null)
receipts_fingerprint=$(printf '%s\n%s\n' "$pkgs_output" "$receipt_stamps" \
    | LC_ALL=C cksum | LC_ALL=C tr -cd '0-9 ' | LC_ALL=C tr ' ' '-')
```

为什么两样都要（`:49-52` 注释）：**只靠 `--pkgs` 列表会漏掉「同 id 的升级/重装」**——升级不改 package id，但可能在**新路径**放一个 app。installer 每次都会重写那个 receipt 的 plist，所以 plist 的 mtime 进键就能捕获。

缓存头格式：`#receipts:<fingerprint>`（`:83, 226`），读时逐行跳过第一行（`:85-89`）。

TTL 两档（`:20, 56-58`）：
- 默认 `3600` 秒
- 如果能拿到 receipt stamps（说明 `/var/db/receipts` 可读）且调用方没显式设 TTL → `604800`（7 天）

**`--require-complete` 语义**（`:17-18, 65-71, 98-113, 120-132, 200-232`）：调用方在要「证明」时传这个 flag，此时：

1. 缓存必须在 TTL 内**且** fingerprint 必须非空（`:70-71`）——「无法计算 fingerprint 的缓存，对这类调用方是不可用的」。
2. `MOLE_PKG_RECEIPT_SCAN_TIMEOUT` 无效时直接 `return 2`（`:104-106`）。
3. 扫描中任何一处超时 → `return 124`，**不返回部分结果**（`:110, 122, 156`）。
4. 任何 `pkgutil --files` 失败 → `return 2`（`:129-132`）。
5. **只有读完了所有 receipt 的扫描才允许写缓存**（`:214` 的 `$scan_complete -eq 1`）。原注释（`:96-97`）：「a complete caller reads a cache hit as proof that no other package owns an app」。

**返回码约定**（供 Python 参考）：

| rc | 含义 |
|---|---|
| 0 | 成功（可能为空） |
| 2 | 不完整/不可判定（`pkgutil` 不存在、无法 fingerprint、`--files` 失败） |
| 124 | 超时 |
| ≥128 | 信号 |

### 4.4 另一个「不误判残留」的机制：BOM 反向查询

`pkg_receipts.sh` 是「receipt → app 路径」。反方向的「这个路径属于哪个 pkg」由 `find_app_receipt_files()` 完成（`app_protection.sh:2248-2351`）：

1. 扫 `/private/var/db/receipts` 下 `*.bom`（`:2268-2282`）
2. 用 `mole_name_starts_with_bundle_id_boundary` 过滤出属于该 bundle id 的（`:2277-2280`）
3. `lsbom -f -s "$bom_file"` 列出 payload 路径（`:2298-2299`）
4. 每条路径经过 `receipt_payload_path_is_allowlisted`（`app_protection.sh:2353-2378`）**白名单**：

```bash
/Library/LaunchAgents/*.plist  | /Library/LaunchDaemons/*.plist
    → base 必须 == "$bundle_id.plist" 或 "$bundle_id."*.plist
/Library/PrivilegedHelperTools/*
    → mole_name_starts_with_bundle_id_boundary "$base" "$bundle_id"
/private/var/db/receipts/*
    → base == "$bundle_id.bom" | "$bundle_id.plist" | "$bundle_id."*
# 其他一律 return 1（不认）
```

5. 再加两道过滤：路径含 `..` 就拒（`:2319-2323`），`/Applications` 和 `/Library` 两个顶层目录跳过（`:2330-2332`），最后 `should_protect_path` 再筛一遍（`:2334-2338`）。

**这就是「某路径属于一个仍安装的 pkg 所以不能当残留」的完整链路**：bundle id → receipt 名（有边界的前缀匹配）→ `lsbom` payload → 白名单收敛到只可能属于该 app 的路径。

---

## 5. browser_clones（`browser_clones.sh`）

### 5.1 它清理的不是 profile，是「代码签名克隆」

文件名 `browser_clones.sh` 容易误读。它处理的是 **Chromium 签名校验时在 Darwin 用户目录里生成的临时 app 副本**，路径形如：

```
/private/var/folders/<2 字符>/<随机>/X/<bundle-id>.code_sign_clone/code_sign_clone.<6 位字母数字>/<App>.app
```

`_mole_browser_clone_root()`（`browser_clones.sh:4-11`）：
```bash
root=$(/usr/bin/getconf DARWIN_USER_DIR)          # 得到 /var/folders/xx/yyy/0/
case "$root" in /var/folders/*) root="/private$root" ;; esac
[[ "$root" =~ ^/private/var/folders/[A-Za-z0-9_]{2}/[A-Za-z0-9_]+/0/?$ ]] || return 1
root=${root%/}
printf '%s/X\n' "${root%/0}"                      # 换成 .../xx/yyy/X
```

**白名单 bundle id → App 名**（`browser_clones.sh:35-42`）：

| bundle id | App 名 |
|---|---|
| `com.google.Chrome.code_sign_clone` | `Google Chrome` |
| `com.google.Chrome.beta.code_sign_clone` | `Google Chrome Beta` |
| `com.google.Chrome.dev.code_sign_clone` | `Google Chrome Dev` |
| `com.google.Chrome.canary.code_sign_clone` | `Google Chrome Canary` |
| `org.chromium.Chromium.code_sign_clone` | `Chromium` |

### 5.2 十一道验证（`_mole_browser_clone_snapshot`，`browser_clones.sh:29-79`）

这是全仓库最保守的一段代码。任何一项不过就放弃：

| # | 验证 | 行号 |
|---|---|---|
| 1 | 路径必须在该 root 下 | 32 |
| 2 | 第一段必须是上表 5 个 id 之一 | 35-42 |
| 3 | 第二段必须匹配 `^code_sign_clone\.[A-Za-z0-9]{6}$` | 43 |
| 4 | **从路径到 `/` 逐级向上，任何一级是符号链接就拒** | 44-48 |
| 5 | 路径和父目录都必须是目录 | 50 |
| 6 | 路径和父目录的 uid 都必须等于当前 uid（`stat -f %u`） | 51 |
| 7 | 目录里**有且仅有 1 个条目**，且名字必须精确等于 `<App>.app` 或 `<App>.app.bundle` | 52-59 |
| 8 | `bundle`、`Contents`、`Contents/MacOS`、`Contents/Info.plist`、`Contents/MacOS/<App>` 五个路径都存在且**都不是符号链接** | 62-64 |
| 9 | plist 和 exe 都是普通文件，plist ≤ 65536 字节 | 65 |
| 10 | `PlistBuddy -c 'Print :CFBundleIdentifier'` == id 去掉 `.code_sign_clone` 后缀；`CFBundleExecutable` == App 名 | 66-72 |
| 11 | `lsof -nP -F pn -- "$exe"` 必须 **rc==1 且无输出**（即确认没人打开）；rc 0 或 2 或 124 都拒 | 73-76 |

返回的是 `stat -f '%d:%i:%m:%c'` 的五元组快照（`:77-78`），供后面做「删之前再确认一次没变」。

### 5.3 幂等/竞态保护

- `_mole_browser_clones_idle()`（`:13-27`）：先确认 `ps -axo comm=` 里能看到 `/sbin/launchd`（证明进程表读全了），再逐行匹配 Chrome/Chromium 的任何进程名或 `.app/` 路径或 `code_sign_clone/` 路径，**命中就返回 1（不清理）**。rc≥128 直接上抛。
- `_mole_browser_clone_final_guard()`（`:81-87`）：再查一次 idle + 重新取 snapshot 并和审查时的值**逐字相等** + 第三次查 idle。
- `clean_browser_code_sign_clones()`（`:89-117`）：把 guard 挂到 `_MOLE_SAFE_REMOVE_FINAL_GUARD`（`:95`），然后走 `safe_remove "$path" true unknown "$deadline" "$parent" "$parent_id" "$target_id"`（`:111`）——注意第 3 个参数是 `unknown`，即**尺寸未知也允许删**（APFS 共享 extent，`du` 数不准）。

### 5.4 浏览器 profile 目录：Mole 如何区分「纯缓存」与「用户数据」

**重要事实：`browser_clones.sh` 完全不涉及 profile 目录。** profile 的处理在 `lib/clean/user.sh` 的 `clean_browsers()`（`user.sh:1960-2184`）。它的模型是**白名单式列举可删叶子**，而不是「黑名单式排除用户数据」——这个方向选择本身就是最强的保护。

#### 5.4.1 可删（纯缓存）子目录清单

**Chromium 系**（Chrome/Edge/Brave/Vivaldi/Arc/Dia/Helium/Yandex/QQ Browser 共用一套叶子名）：

`user.sh:1971-1985`（Chrome，预检用）、`user.sh:754-783`（Chrome，实际删除）：

```
<profile>/Application Cache/      ← 已废弃，仅老版本
<profile>/Code Cache/
<profile>/GPUCache/
<profile>/DawnCache/
<profile>/GrShaderCache/
<profile>/GraphiteDawnCache/
<profile>/DawnGraphiteCache/      ← 见 user.sh:2465
<profile>/DawnWebGPUCache/        ← 见 user.sh:2467
```

**root 级（不属于任何 profile）**：

```
component_crx_cache/
extensions_crx_cache/     ← Arc/Dia/Helium 有，Chrome 主列表没有
ShaderCache/
GrShaderCache/
GraphiteDawnCache/
Crashpad/completed/
OptGuideOnDeviceModel/                 ← Chrome 端侧模型，常几 GB
OptGuideOnDeviceClassifierModel/
optimization_guide_model_store/
```

**`<profile>` 是什么**：`user.sh:2002` 的 glob 是 `"$HOME/Library/Application Support/Google/Chrome"/*/`，即**一层通配**——`Default`、`Profile 1`、`Profile 2`、`System Profile`、`Guest Profile` 都命中。Mole 从不用固定名字，只用 `*/`。

**各浏览器的 profile 根**（`user.sh`）：

| 浏览器 | profile 根 | 行号 |
|---|---|---|
| Chrome | `~/Library/Application Support/Google/Chrome/*/` | 2002 |
| Arc | `~/Library/Application Support/Arc/*/` 和 `Arc/User Data/*/` | 2038 / 2041 |
| Dia | `~/Library/Application Support/Dia/User Data/*/` | 2078 |
| Brave | `~/Library/Application Support/BraveSoftware/Brave-Browser/*/` | 2101 |
| Vivaldi | `~/Library/Application Support/Vivaldi/*/` | 2158 |
| Helium | `~/Library/Application Support/net.imput.helium/*/`（无独立 SW 循环） | 2106-2114 |
| Yandex | `~/Library/Application Support/Yandex/YandexBrowser/*/`（无 SW 循环） | 2118-2122 |
| QQ Browser 3 | `~/Library/Application Support/QQBrowser3/*/`（无 SW 循环） | 2170-2176 |
| **Firefox** | `~/Library/Application Support/Firefox/Profiles/*/cache2/` | 788-789 |

**Firefox 只有两条**（`user.sh:786-789`）：
```
~/Library/Caches/Firefox/*
~/Library/Application Support/Firefox/Profiles/*/cache2/*
```
`cache2` 是 Firefox 的 HTTP 缓存目录名；`startupCache`、`shader-cache` 等**未列入**。

**整目录删除（非 profile 内）**（`user.sh`）：
```
~/Library/Caches/com.apple.Safari/*              1963
~/Library/Caches/Google/Chrome/*                 1965
~/Library/Caches/Chromium/*                      2007
~/Library/Caches/com.microsoft.edgemac/*         2009
~/Library/Caches/company.thebrowser.Browser/*    2012 (Arc)
~/Library/Caches/company.thebrowser.dia/*        2051 (Dia)
~/Library/Caches/BraveSoftware/Brave-Browser/*   2084
~/Library/Caches/net.imput.helium/*              2107
~/Library/Caches/Yandex/YandexBrowser/*          2118
~/Library/Caches/com.operasoftware.Opera/*       2140
~/Library/Caches/com.vivaldi.Vivaldi/*           2143
~/Library/Caches/Comet/*                         2162
~/Library/Caches/com.kagi.kagimacOS/*            2163 (Orion)
~/Library/Caches/zen/*                           2164
~/Library/Caches/com.tencent.QQBrowser3/*        2171
~/Library/Application Support/Google/GoogleUpdater/crx_cache/*   2005
~/Library/Application Support/Google/GoogleUpdater/*.old         2006
~/.cache/puppeteer/*                             2008
```

#### 5.4.2 不可删（用户数据）——Mole 怎么表达「不删」

Mole **没有**一个「用户数据子目录黑名单」数组。它靠三层：

**① 只列白名单叶子**。`clean_browsers()` 里没有一处 `safe_clean <profile>/*`——永远是 `<profile>/<具体叶子>/*`。所以 `Cookies`、`Login Data`、`History`、`Bookmarks`、`Local Storage`、`IndexedDB`、`Session Storage`、`Web Data`、`Preferences`、`Extensions` 这些**从未出现在任何删除调用里**，是结构性排除。

**② 显式注释点名的非目标**（`lib/clean/dev.sh:4242-4244`，Codex 的同类清理）：
> `Local Storage, IndexedDB, cookies, sessions, preferences, and every Application Support sibling are deliberate non-targets because they can hold durable state rather than rebuildable cache data.`

**③ `Service Worker` 的精细切分**（`lib/clean/caches.sh:49-...`）——这是唯一一个「删一半」的目录：

```
Service Worker/CacheStorage/<origin-hash>/     ← 可删（按 origin 粒度）
Service Worker/ScriptCache/                    ← 绝不删
```

`ScriptCache` 被排除的理由写在 `user.sh:1966-1969`：
> `Do not clean Chromium Service Worker ScriptCache. Even when the browser is closed, removing MV3 extension bytecode can break extension service workers and trigger security warnings during dry-run scans. See #785, #964, and #968.`

`CacheStorage` 的删除约束（`caches.sh:59-140`）：
- `find "$cache_path" -mindepth 2 -maxdepth 2 -type d`（`:77-79`）——只取**两级**：origin hash 目录
- `cd -P` 后必须与字面路径相同，**拒绝符号链接根**（`:59-64`）
- 每个候选再 `_mole_snapshot_path_identity`，并校验其父目录在物理 cache root 内（`:117-128`）
- 域名从目录名 `grep -oE '[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,}'` 提取（`:100`），命中 `PROTECTED_SW_DOMAINS` 就跳过（`:102-108`）
- 还要过 `is_path_whitelisted`（`:109-116`）——注释说 SW 目录名是 origin **hash**，用户没法直接把 Chrome SW 路径加进白名单，所以这里要显式查一次

`PROTECTED_SW_DOMAINS`（`bin/clean.sh:54-77`）共 **18** 个：
```
capcut.com  photopea.com  pixlr.com
docs.google.com  sheets.google.com  slides.google.com  drive.google.com  mail.google.com
github.com  gitlab.com  codepen.io  codesandbox.io  replit.com  stackblitz.com
notion.so  figma.com  linear.app  excaladraw.com → excalidraw.com
```
（最后一条原文是 `excalidraw.com`，`:76`。）分组注释：Web 编辑器 / Google Workspace 离线模式 / 代码平台离线 PWA / 协作工具离线 PWA。

#### 5.4.3 进程门（运行时一律不动）

`clean_browsers()` 每个浏览器都先探进程，**三态**：

| 浏览器 | 探测 | 行号 |
|---|---|---|
| Chrome | `pgrep -x "Google Chrome"` / `-x "Google Chrome Helper"` / `-f "/Google Chrome.app/"` | 702-707 |
| Edge | `pgrep -x "Microsoft Edge"`（注释：不能匹配 Microsoft Teams） | 710-712 |
| Brave | `pgrep -x "Brave Browser"` | 714-716 |
| Firefox | `pgrep -x "Firefox"` | 718-720 |
| Arc | `pgrep -x "Arc"` | 738-740 |
| Vivaldi | `pgrep -x "Vivaldi"` | 742-744 |
| QQ Browser | `pgrep -x "QQBrowser3"` | 746-748 |
| Dia | `pgrep -x "Dia"` | 2054-2061（内联，非 `mole_pgrep_any`） |

三态语义在 `mole_pgrep_any`（`base.sh:72-94`）：任一命中 → 0；全部 rc=1 → 1；任何 rc≠1 → 2（未知）。`mole_clean_process_guard`（`base.sh:1710-1723`）：0 → 拒绝（`_MOLE_CLEAN_GUARD_REASON="<family> started"`）；1 → 允许；2 → 拒绝（reason = `process state unknown`）。

**rc==2 时的行为**：打印 `skipped (process state unknown)` 并且**不删**（`user.sh:1997-1999`）。rc==0（在跑）时调 `mole_defer_cleanup_family "Chrome"`——延后到进程退出后（`user.sh:1995-1996`、`base.sh:1678-1684`）。

#### 5.4.4 通用 Electron 缓存扫描：靠「标记文件」判断是不是 Chromium

`clean_application_support_logs()`（`user.sh:2476-...`，泛扫循环见 `user.sh:2538`）对 `~/Library/Application Support` 下每个目录做泛扫，只清 `start_candidates`（`user.sh:2538-2547`）：

```
Code Cache  GPUCache  DawnCache  GrShaderCache  GraphiteDawnCache
DawnGraphiteCache  DawnWebGPUCache  Crashpad/completed
```

并且**只有在 `app_support_dir_has_regenerable_cache_markers` 为真时**才额外加 `Cache` 和 `CachedData`（`user.sh:2548-2552`）。该函数（`user.sh:2456-2473`）检查的标记正是上面那 7 个叶子 + `Crashpad` 存在——也就是说：

> **「目录里有 `Code Cache`/`GPUCache`/… 就说明它是 Chromium 系，才允许动 `Cache`」。**

这是一个很值得抄的模式：用**布局指纹**而非名字/plist 来判断应用类型。

---

## 6. 对 Python 实现的建议

### 6.1 可以直接移植（低成本、高价值）

#### ① 保护判定：纯函数 + 有序短路

Mole 的 `should_protect_path` 是一个 270 行的巨型 `case`，但它本质是**一串无状态谓词**，用 Python 重写非常自然：

```python
def should_protect(path: str) -> bool:
    """返回 True = 不许删。无副作用、可重入。"""
    p = norm(path)                      # 去尾斜杠、折叠 //

    # 0. 本地数据库/恢复副本（仅 clean 模式，卸载模式例外）
    if not UNINSTALL_MODE and matches_any(p, LOCAL_STATE_PATTERNS):   # app_protection.sh:391-399
        return True

    # 1. 共享 home 状态根（.cache/.config/.local/... 本身）
    if is_shared_home_state_root(p):    # app_protection.sh:342-357
        return True

    # 2. 系统设置/控制中心/Finder/Dock 关键字
    if any(k in p.lower() for k in ("systemsettings", "systempreferences", "controlcenter")):
        return True

    # 3. 容器 cache/tmp 豁免（必须先于 com.apple.* 拦截）
    bundle = container_bundle_id(p)     # /Library/Containers/<id>/... 或 /Library/Group Containers/<id>/...
    if bundle and is_container_cache_or_tmp(p):
        container_cache = True          # 放行，但记下来
    elif bundle and should_protect_data(bundle):
        return True

    # 4. EDR 缓存（在 /private/var/folders 白名单之前！）
    if is_endpoint_security_cache_path(p):
        return True

    # 5. 硬编码高风险路径（keychain/mail/accounts/icloud/audio插件/…）
    if matches_any(p, HARDCODED_HIGH_RISK_PATTERNS):   # app_protection.sh:489-612
        return True

    # 6. 数组 glob —— 仅当上面没有命中「容器 cache 豁免」时
    #    （源码：if [[ "$_container_cache_path" != "true" && ... ]]）
    if not container_cache:
        if UNINSTALL_MODE:
            # 卸载模式：先看是否属于「可卸载的 Apple 应用」
            if matches_any(p, APPLE_UNINSTALLABLE_APPS):
                return False
            if matches_any(p, SYSTEM_CRITICAL_BUNDLES):
                return True
        else:
            # 清理模式：两个数组都查
            if matches_any(p, SYSTEM_CRITICAL_BUNDLES + DATA_PROTECTED_BUNDLES):
                return True

        # 7. 文件名兜底（仅清理模式；卸载模式跳过）
        if not UNINSTALL_MODE and should_protect_data(basename(p)):
            return True

    return False
```

三个**顺序不能错**的点：

1. **容器 cache 豁免在 `should_protect_data` 之前**（`app_protection.sh:479-485`）。否则 `~/Library/Containers/com.apple.Safari/Data/Library/Caches/*` 会被 `com.apple.*` 一刀切掉，缓存永远清不掉。豁免条件是三个（`:479-481`）：
   ```python
   p.endswith(...) or "/Data/Library/Caches/" in p or "/Data/tmp/" in p
   # 外加两个微信特例
   ".../com.tencent.xinWeChat/Data/Documents/app_data/log/" in p
   ".../com.tencent.xinWeChat/Data/.wxapplet/WMPF/" in p
   ```
2. **EDR 判定在 `/private/var/folders` 白名单之前**（`file_ops.sh:1310-1325`）。否则白名单会把 EDR 缓存一起放行。
3. **卸载模式先查 `APPLE_UNINSTALLABLE_APPS`**（`app_protection.sh:619-633`）。否则 Xcode 卸不掉。

#### ② glob → `fnmatch`

Mole 用 bash 的 `[[ "$x" == $pattern ]]`（`case`/glob，**不是**正则）。Python 对应 `fnmatch.fnmatchcase`——注意是 `fnmatchcase`，因为 Mole 的匹配**大小写敏感**（`should_protect_data` 里没开 `nocasematch`；只有 `is_critical_system_component` 和 `is_endpoint_security_cache_path` 开了）。

```python
from fnmatch import fnmatchcase

def matches_any(name: str, patterns) -> bool:
    return any(fnmatchcase(name, pat) for pat in patterns)
```

`build_regex_var()`（`app_protection.sh:92-112`）只是把 glob 转成锚定正则（`.`→`\.`、`*`→`.*`、加 `^$`），语义等价于 `fnmatchcase`。**不需要**移植成预编译正则——除非 profiling 证明 glob 是瓶颈（Mole 这么写是为了 bash 3.2 没有 nameref，不是性能）。

#### ③ 三态返回码

Mole 到处都是 `0/1/≥128` 和 `0/1/2`。Python 用 `enum` 表达，别用 bool：

```python
class Probe(IntEnum):
    CONFIRMED = 0     # 确认（命中/在跑/被占用）
    NEGATIVE  = 1     # 确认否
    UNKNOWN   = 2     # 探测失败 —— 一律按 CONFIRMED 处理
    CANCELLED = 130   # 信号中断，必须向上传播
```

这条纪律是 Mole 全部安全性的来源。Python 里最容易被写成 `except: return False`（= 当成安全），必须反过来。

#### ④ `bundle_has_installed_app` 的三段式

```python
APP_ROOTS = [
    "/Applications", "/Applications/Setapp", "/Applications/Utilities",
    "/System/Applications", "/System/Applications/Utilities",
    os.path.expanduser("~/Applications"),
    os.path.expanduser("~/Library/Application Support/Setapp/Applications"),
    "/opt/homebrew/Caskroom", "/usr/local/Caskroom",
    "/Library/Input Methods", os.path.expanduser("~/Library/Input Methods"),
]
DEEP_ROOTS = ("/Caskroom", "/Setapp/Applications")     # depth 3，其余 depth 1

REVERSE_DNS = re.compile(r'^[A-Za-z0-9][-A-Za-z0-9]*(\.[A-Za-z0-9][-A-Za-z0-9]*)+$')

def bundle_has_installed_app(bid: str, deadline: float) -> Probe:
    if not bid or bid == "unknown" or not REVERSE_DNS.match(bid):
        return Probe.NEGATIVE

    # 1. Spotlight 快路径：只当「命中」用，未命中不作为反证
    if which("mdfind"):
        rc, out = run_bounded(["mdfind", f"kMDItemCFBundleIdentifier == '{bid}'"], timeout=2)
        if rc >= 128: return Probe.CANCELLED
        if rc == 0 and out.strip():
            return Probe.CONFIRMED

    # 2. 文件系统慢路径
    bid_lower = bid.lower()
    parent_lower = strip_helper_suffix(bid_lower)   # .helper/.daemon/.agent/.xpc/.service
    for root in APP_ROOTS:
        if not os.path.isdir(root): continue
        if time.monotonic() >= deadline: return Probe.CONFIRMED   # 超时 → 保护
        depth = 3 if root.endswith(DEEP_ROOTS) else 1
        try:
            apps = find_apps(root, depth)           # find -maxdepth N -iname '*.app'
        except (Timeout, OSError):
            return Probe.CONFIRMED                  # 扫不动 → 保护
        for app in apps:
            if os.path.lexists(f"{app}/Contents/Library/LaunchServices/{bid}"):
                return Probe.CONFIRMED
            info = f"{app}/Contents/Info.plist"
            if not os.path.isfile(info): continue
            got = plutil_extract(info, "CFBundleIdentifier")
            if got is None:
                return Probe.CONFIRMED              # 读不出来 → 保护（Mole 原样）
            got_l = got.lower()
            if got_l == bid_lower or (parent_lower and got_l == parent_lower):
                return Probe.CONFIRMED
            if got in MICROSOFT_HELPER_MAP.get(bid, ()):
                return Probe.CONFIRMED
    return Probe.NEGATIVE
```

`strip_helper_suffix` 的后缀表（`bundle_resolver.sh:90`）：`.helper` `.daemon` `.agent` `.xpc` `.service`。

`MICROSOFT_HELPER_MAP`（`bundle_resolver.sh:98-108`）只有两个键：
```python
MICROSOFT_HELPER_MAP = {
    "com.microsoft.autoupdate.helper": {"com.microsoft.Word", "com.microsoft.Excel",
        "com.microsoft.Powerpoint", "com.microsoft.Outlook", "com.microsoft.OneNote"},
    "com.microsoft.office.licensingV2.helper": {... 同上 ...},
}
```

#### ⑤ `plutil` 而非 `defaults`

```python
def plist_get(path: str, key: str) -> str | None:
    rc, out = run_bounded(["plutil", "-extract", key, "raw", path], timeout=2)
    return out.strip() if rc == 0 else None
```
Mole 实测 `plutil -extract` 比 `defaults read` 快 ~1.8 倍，且不经过 `cfprefsd`（`app_protection.sh:1918-1921`）。Python 的 `plistlib` 更快（无 fork），推荐：

```python
def plist_get(path, key):
    try:
        with open(path, "rb") as f:
            return plistlib.load(f).get(key)
    except Exception:
        return None      # 调用方必须把 None 当成「不知道」
```
唯一注意：`plistlib` 不能读 binary plist 里的**损坏**内容时 Mole 的 `plutil` 也许能——但两者失败时都应归到 `UNKNOWN`。

#### ⑥ EDR 判定（20 行，照抄）

```python
ENDPOINT_PREFIXES = (
    "com.crowdstrike.", "com.sentinelone.", "com.sentinel-labs.", "com.eset.",
    "com.jamf.", "com.jamfsoftware.", "com.paloaltonetworks.",
    "com.cisco.anyconnect", "com.cisco.secureclient", "com.alibaba.security.",
)

def is_endpoint_security_cache_path(path: str) -> bool:
    if not (path.startswith("/private/var/folders/") or path.startswith("/var/folders/")):
        # realpath 兜底：防「别的目录里恰好有 var/folders」
        real = os.path.realpath(path)
        if not real.startswith("/private/var/folders/"):
            return False
    low = path.lower()
    return any(p in low for p in ENDPOINT_PREFIXES)
```

#### ⑦ 删除前的身份三元组（防 TOCTOU）

Mole 在 `safe_remove` 前绑 `(parent_realpath, parent_dev:ino, target_dev:ino)`，落地时重算（`file_ops.sh:3210-3246`，比较在 `_mole_path_matches_identity`）。Python：

```python
def snapshot_identity(path):
    parent = os.path.dirname(os.path.abspath(path)) or "/"
    parent_real = os.path.realpath(parent)
    ps, ts = os.stat(parent_real), os.stat(path, follow_symlinks=False)
    return parent_real, (ps.st_dev, ps.st_ino), (ts.st_dev, ts.st_ino)
```

配合「先 snapshot、后复算、不等就放弃」，能挡住「检查完到删除之间目标被换成符号链接/别的文件」这一类。

#### ⑧ 用户数据 vs 缓存的判断：抄「布局指纹」

```python
CHROMIUM_CACHE_LEAVES = {
    "Application Cache", "Code Cache", "GPUCache", "DawnCache",
    "GrShaderCache", "GraphiteDawnCache", "DawnGraphiteCache", "DawnWebGPUCache",
}
CHROMIUM_ROOT_LEAVES = {
    "ShaderCache", "GrShaderCache", "GraphiteDawnCache",
    "component_crx_cache", "extensions_crx_cache",
    "OptGuideOnDeviceModel", "OptGuideOnDeviceClassifierModel",
    "optimization_guide_model_store",
}
CHROMIUM_MARKERS = CHROMIUM_CACHE_LEAVES | {"Crashpad"}

def is_chromium_app_support_dir(d: str) -> bool:
    """app_support_dir_has_regenerable_cache_markers, user.sh:2456-2473"""
    return any(os.path.exists(os.path.join(d, m)) for m in CHROMIUM_MARKERS)

def cleanable_leaves(profile_root: str):
    for entry in os.listdir(profile_root):        # Default / Profile 1 / …
        prof = os.path.join(profile_root, entry)
        if not os.path.isdir(prof) or os.path.islink(prof): continue
        for leaf in CHROMIUM_CACHE_LEAVES:
            p = os.path.join(prof, leaf)
            if os.path.isdir(p) and not os.path.islink(p):
                yield p
```
**永远只产出具体叶子目录，绝不产出 `<profile>/*`。**

#### ⑨ `Service Worker` 的两级切分

```python
def sw_cache_origins(profile: str):
    root = os.path.join(profile, "Service Worker", "CacheStorage")
    if os.path.islink(root) or not os.path.isdir(root): return
    for name in os.listdir(root):                  # origin hash 目录
        p = os.path.join(root, name)
        if not os.path.isdir(p) or os.path.islink(p): continue
        domain = extract_domain(name)               # r'[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,}'
        if domain and any(d in domain for d in PROTECTED_SW_DOMAINS): continue
        yield p
# 注意：绝不产出 Service Worker/ScriptCache
```

### 6.2 值得移植但要改写

#### `pkgutil --files` 全量扫描 → **不要照抄**

Mole 的做法是「每个 receipt 跑一次 `pkgutil --files`」，在 Xcode 机器上能耗尽 8 秒预算（`pkg_receipts.sh:28-31` 的 issue #1383 就是为此）。

Python 侧的替代：

```python
# 方案 A：只查 receipt 名单，不查 payload（绝大多数场景够用）
def pkg_owned_nonstandard_apps(deadline) -> list[str]:
    rc, out = run_bounded(["pkgutil", "--pkgs"], timeout=3)
    if rc != 0: return []                     # 不可判定 → 空列表，调用方按「不知道」处理
    pkgs = [p for p in out.split() if p and not p.startswith("com.apple.")]
    ...
# 方案 B：需要 payload 时，直接读 BOM 而不是 pkgutil --files
#         /var/db/receipts/<pkg>.bom → lsbom -f -s（Mole 自己也是这么做的，见 app_protection.sh:2298）
```

**关键保留点**：
1. 只认 `/usr/local/*.app` 和 `/opt/*.app` 两个根（`pkg_receipts.sh:165-176`）。放宽到 `/Applications` 会做无用功（那边已有常规扫描）。
2. `.[aA][pP][pP]` 大小写处理在 Python 里简化为 `.lower().endswith(".app")`，但要**保留原路径大小写**用于返回。
3. **缓存键 = `--pkgs` 输出 + `/var/db/receipts/*.plist` 的 (mtime, name)**，用 `hashlib.sha256` 而不是 `cksum`。这条不能省：只靠 `--pkgs` 会漏掉「同 id 升级换路径」。
4. `--require-complete` 语义要保留：超时就返回「不完整」，绝不返回部分结果。

#### `mdfind` 快路径 → 保留，但降级

`mdfind` 在 Python 里同样是 subprocess，成本一样。建议保留，但：
- 超时给 2 秒（`MOLE_TIMEOUT_QUICK_DETECT_SEC`）
- **只把「有输出」当正面证据**，「无输出」永远继续走文件系统
- 结果做进程内 memo（`@lru_cache`），因为 `is_bundle_orphaned` 会对同一批 bundle id 反复问

Mole 的 `_MOLE_MDFIND_FOUND` / `_MOLE_MDFIND_NOTFOUND`（`apps.sh:434-461`）是同一思路，只是受 bash 3.2 限制写成了两个字符串。Python 直接 `dict` 即可，但**超时/错误的结果不要缓存**（`apps.sh:516-521` 的血泪注释）。

#### 缓存 TTL

Mole 的 `installed_apps_cache` TTL 是 300s（`apps.sh:157`），且必须带完整性尾行。Python 侧建议照抄这两条——**「不完整的 app 列表不能授权删除」**是核心不变量，比 TTL 本身重要得多。

### 6.3 代价太大 / 不值得移植

| 项 | 为什么不做 |
|---|---|
| **`lsregister dump` 解析** | Mole 自己实测否决：`lsregister -u` 在 macOS 15+ 对已消失的 app 返回 `-10814`，`-gc` 全量重建会让运行中的 VPN 扩展被误判成重装（`bin/clean.sh:1807-1813`）。dump 输出格式无契约、动辄几秒。**用 `find` + `plutil` 就够**。 |
| **bash 3.2 兼容写法** | `${arr[@]+"${arr[@]}"}`、`tr` 转小写、`eval` 间接赋值、手写线性去重——全部是 bash 3.2 的包袱（`app_protection.sh:160-161, 718-720`；`pkg_receipts.sh:186-193`）。Python 用 list/dict/`.lower()` 直接替代。 |
| **`build_regex_var` 预编译正则** | 见 6.1②。`fnmatchcase` 语义等价，可读性好得多。Mole 用正则是因为 bash 的 `=~` 比 `==` glob 在长列表上快，Python 里 `fnmatch` 有内部缓存。 |
| **`_mole_uninstall_name_variant_matches` 的名字变体矩阵** | 对每个 app 名生成 `nospace` / `hyphen` / `underscore` / `lowercase` 四套变体，再 ×5 种前缀形态（`app_protection.sh:751-768`）。这套启发式误伤率不低，Mole 自己也要靠 `LAUNCH_AGENT_NAME_COMMON_WORDS`（40 个词）和 `_mole_uninstall_is_common_app_name`（`app_protection.sh:722-735`）来兜。**建议 Python 侧只做精确 bundle id + 精确显示名，不做变体推导**。 |
| **`find_vendor_nested_app_paths` / `find_shared_app_paths`** | 依赖从 bundle id 反推 vendor/product token（`app_protection.sh:737-749`），再 `-mindepth 2 -maxdepth 2` 全盘扫 `~/Library/Application Support`。O(n) 目录遍历 × 每个 app，成本高、命中率低（为 Sibelius 这类少数产品设计）。 |
| **`_mole_uninstall_embedded_bundle_ids`** | `find Contents -maxdepth 12 -path "*/Contents/Info.plist"` 上限 128 个 plist（`app_protection.sh:1016-1027`）。对一个 `.app` 跑 128 次 `plutil`，为的是找 XPC/appex/LoginItems 的嵌套 id。只有做「深度卸载」才需要。 |
| **code_sign_clone 清理** | 收益极小（Chromium 签名副本，通常几十 MB），验证成本极大（11 道检查 + 3 次 idle 探测 + 五元组快照，`browser_clones.sh:29-87`）。**建议整体跳过**，或者只做「报告存在」不做删除。 |
| **PlayCover 别名解析** | `mole_playcover_alias_target`（`app_protection.sh:1097-1132`）要求别名目录里每个符号链接都指向同一 bundle 的同名条目，Info.plist 还可能是链接或副本。极窄场景，不值得。 |
| **`mole_clean_process_guard` 的 defer 机制** | `mole_defer_cleanup_family`（`base.sh:1678-1684`）是「先跳过，等进程退出后重试」的调度器，属于交互式 CLI 的体验优化。Python 侧直接「跳过并报告」即可。 |

### 6.4 现有 Python 实现的差距（对照 `app/skill-scripts/clean.py`）

当前仓库已有的 `PROTECTED_VENDOR_SEGMENTS`（`clean.py:85-91`）包含 `"Time Machine", "Arq", "Carbon Copy Cloner", "SuperDuper", "Resilio", "Sync.com", "pCloud", "Cylance", "Kandji", "colima", "lima"` 等——**这些在 Mole 里都不存在**（见 §2.4）。这不是错，是**比 Mole 更保守**；但需要明确标注为「本仓库自有规则，非 Mole 来源」，否则未来对照源码时会困惑。

反过来，Mole 有而 `clean.py` 目前**没有**的、建议补的：

1. **EDR 缓存前缀**（10 条，`app_protection_data.sh:195-206`）——目前 `PROTECTED_VENDOR_SEGMENTS` 只有 `"CrowdStrike", "SentinelOne", "Falcon"` 三个名字子串，覆盖不到 `com.jamf.` / `com.paloaltonetworks.` / `com.alibaba.security.`，也覆盖不到 `com.sentinel-labs.`。
2. **`/private/var/folders` 与 `/var/folders` 的等价**：`clean.py:184-186` 直接拒绝 home 之外的路径，恰好把 EDR 缓存一起挡了——但这是**偶然**而非设计。建议显式加一条 EDR 判断，让意图可读。
3. **`Service Worker/CacheStorage` 的 origin 粒度**：`clean.py` 的 `YELLOW_SAFE_SUFFIXES` 里有 `/Service Worker/CacheStorage` 和 `/Service Worker/ScriptCache`（`clean.py:97`）——**`ScriptCache` 在 Mole 里是明确禁删的**（`user.sh:1966-1969`，issue #785/#964/#968）。这条建议立即改掉。
4. **`PROTECTED_SW_DOMAINS`** 的 18 个域名（`bin/clean.sh:54-77`）——离线 PWA 的缓存删了会丢未同步内容。
5. **容器 cache 豁免**（`app_protection.sh:479-481`）：`clean.py` 的 `PROTECTED_SEGMENTS` 用「包含 `/Library/Keychains` 等」的子串匹配，会把容器内的 `Data/Library/Caches` 也一起挡掉（如果容器 id 恰好命中某条）。建议按 Mole 的顺序先判豁免。

### 6.5 一句话总结移植策略

> 把 §1 的四个数组（`SYSTEM_CRITICAL_BUNDLES` / `APPLE_UNINSTALLABLE_APPS` / `DATA_PROTECTED_BUNDLES` / `ENDPOINT_SECURITY_BUNDLE_PREFIXES`）原样搬成 Python 元组，把 `should_protect_path` 的判定顺序搬成 if 链，把 `bundle_has_installed_app` 搬成三段式（Spotlight 快路径 + 目录扫描 + 三态返回），把浏览器清理收缩成「只产出具体缓存叶子」。**其余全部不搬**——尤其是 `lsregister`、名字变体矩阵、code_sign_clone。

---

## 附录 A：Mole 保护相关的全部符号索引

| 符号 | 文件:行 |
|---|---|
| `SYSTEM_CRITICAL_BUNDLES` | `app_protection_data.sh:19` |
| `APPLE_UNINSTALLABLE_APPS` | `app_protection_data.sh:163` |
| `OFFICIAL_UNINSTALLER_RULES` | `app_protection_data.sh:179` |
| `ENDPOINT_SECURITY_BUNDLE_PREFIXES` | `app_protection_data.sh:195` |
| `DATA_PROTECTED_BUNDLES` | `app_protection_data.sh:209` |
| `LAUNCH_AGENT_NAME_COMMON_WORDS` | `app_protection_data.sh:629` |
| `is_critical_system_component` | `app_protection.sh:53` |
| `bundle_matches_pattern` | `app_protection.sh:75` |
| `build_regex_var` | `app_protection.sh:92` |
| `should_protect_from_uninstall` | `app_protection.sh:126` |
| `official_uninstaller_vendor` | `app_protection.sh:144` |
| `should_protect_data` | `app_protection.sh:182` |
| `is_endpoint_security_cache_path` | `app_protection.sh:266` |
| `is_orbstack_runtime_path` | `app_protection.sh:296` |
| `holds_compiled_model_cache` | `app_protection.sh:325` |
| `_mole_is_shared_home_state_root` | `app_protection.sh:342` |
| `_mole_is_steam_library_path` | `app_protection.sh:365` |
| `should_protect_path` | `app_protection.sh:384` |
| `is_path_whitelisted` | `app_protection.sh:660` |
| `_mole_uninstall_is_common_app_name` | `app_protection.sh:722` |
| `_mole_uninstall_vendor_product_tokens` | `app_protection.sh:737` |
| `_mole_uninstall_name_variant_matches` | `app_protection.sh:751` |
| `_mole_uninstall_materialize_find0` | `app_protection.sh:788` |
| `mole_uninstall_launch_agent_owned_by_app` | `app_protection.sh:817` |
| `find_vendor_nested_app_paths` | `app_protection.sh:867` |
| `find_shared_app_paths` | `app_protection.sh:922` |
| `_path_belongs_to_independent_cli` | `app_protection.sh:985` |
| `_mole_uninstall_embedded_bundle_ids` | `app_protection.sh:1013` |
| `mole_playcover_apps_dir` / `mole_is_playcover_app` / `mole_playcover_alias_target` | `app_protection.sh:1077 / 1083 / 1097` |
| `find_app_files` | `app_protection.sh:1134` |
| `get_diagnostic_report_paths_for_app` | `app_protection.sh:1905` |
| `find_app_system_files` | `app_protection.sh:1975` |
| `find_app_receipt_files` | `app_protection.sh:2248` |
| `receipt_payload_path_is_allowlisted` | `app_protection.sh:2353` |
| `force_kill_app` | `app_protection.sh:2421` |
| `bundle_has_installed_app` | `bundle_resolver.sh:41` |
| `_MOLE_BUNDLE_RESOLVER_APP_ROOTS` | `bundle_resolver.sh:21` |
| `pkg_receipt_nonstandard_app_paths` | `pkg_receipts.sh:12` |
| `_mole_browser_clone_root` / `_idle` / `_snapshot` / `_final_guard` | `browser_clones.sh:4 / 13 / 29 / 81` |
| `clean_browser_code_sign_clones` | `browser_clones.sh:89` |
| `validate_path_for_deletion` | `file_ops.sh:1138` |
| `_mole_is_critical_deletion_path` | `file_ops.sh:1022` |
| `safe_remove` / `safe_sudo_remove` | `file_ops.sh:1451 / 1982` |
| `_mole_snapshot_path_identity` | `file_ops.sh:3210` |
| `_mole_paths_have_open_handle` | `file_ops.sh:760` |
| `MOLE_ERR_SIP_PROTECTED` | `file_ops.sh:14` |
| `safe_clean` / `safe_clean_guarded` / `_safe_clean_impl` | `bin/clean.sh:1445 / 1453 / 819` |
| `PROTECTED_SW_DOMAINS` | `bin/clean.sh:54` |
| `clean_service_worker_cache` | `lib/clean/caches.sh:49` |
| `scan_installed_apps` | `lib/clean/apps.sh:150` |
| `ORPHAN_NEVER_DELETE_PATTERNS` | `lib/clean/apps.sh:422` |
| `is_bundle_orphaned` | `lib/clean/apps.sh:464` |
| `clean_orphaned_app_data` | `lib/clean/apps.sh:664` |
| `known_protect_patterns` | `lib/clean/apps.sh:1000` |
| `clean_browsers` | `lib/clean/user.sh:1960` |
| `app_support_dir_has_regenerable_cache_markers` | `lib/clean/user.sh:2456` |
| `process_container_cache` | `lib/clean/user.sh:1330` |
| `get_lsregister_path` | `lib/core/base.sh:124` |
| `mole_pgrep_any` / `mole_clean_process_guard` | `lib/core/base.sh:72 / 1710` |
| `mole_is_reverse_dns_bundle_id` | `lib/core/base.sh:1077` |
| `mole_name_starts_with_bundle_id_boundary` | `lib/core/base.sh:1092` |
| `mole_name_has_bundle_id_boundary` | `lib/core/base.sh:1101` |
| `mole_darwin_user_cache_root` | `lib/core/base.sh:243` |
| `load_mole_whitelist` | `lib/core/base.sh:452` |
| `DEFAULT_WHITELIST_PATTERNS` | `lib/core/base.sh:168` |
| `MOLE_TM_BACKUP_SAFE_HOURS` | `lib/core/base.sh:158` |
| `_mole_timeout_with_deadline` | `lib/core/timeouts.sh:87` |
| `MOLE_TIMEOUT_*_SEC` | `lib/core/timeouts.sh:61-67` |
| `unregister_app_bundle` / `refresh_launch_services_after_uninstall` | `lib/uninstall/batch.sh:581 / 603` |
| `endpointSecurityBundlePrefixes`（Go 镜像） | `cmd/analyze/delete.go:526` |

## 附录 B：`DEFAULT_WHITELIST_PATTERNS`（`base.sh:168-183`）

Mole 默认给所有用户的硬编码白名单（即使用户没配 whitelist 文件也生效）：

```
$HOME/Library/Caches/ms-playwright*
$HOME/.gradle/caches/*
$HOME/.gradle/daemon/*
$HOME/.ollama/models/*
$HOME/Library/Caches/com.nssurge.surge-mac/*
$HOME/Library/Application Support/com.nssurge.surge-mac/*
$HOME/Library/Caches/org.R-project.R/R/renv/*
$HOME/Library/Caches/JetBrains*
$HOME/Library/Caches/com.jetbrains.toolbox*
$HOME/Library/Caches/tealdeer/tldr-pages
$HOME/Library/Application Support/JetBrains*
$HOME/Library/Caches/com.apple.finder
$HOME/Library/Mobile Documents*
FINDER_METADATA          ← 哨兵值，不是路径（base.sh:167）
```

白名单文件位置：`$HOME/.config/mole/whitelist`（`base.sh:463`）。解析时的拒绝规则（`base.sh:468-505`）：含 `..` 拒、含控制字符拒、非绝对路径拒、含 `//` 拒、`/` `/System*` `/bin*` `/sbin*` `/usr/bin*` `/usr/sbin*` `/etc*` `/var/db*` 一律拒。

`is_path_whitelisted` 的三条匹配（`app_protection.sh:697-712`）：
1. 字面精确相等，或 glob 匹配
2. **target 是白名单条目的父目录**（保护被白名单覆盖的子项）
3. **target 是白名单目录的子项**（仅当条目不含 glob 时）

第 3 条依赖 `has_glob` 判断（`:688-693`）：`*` `?` `[` 任一出现即视为 glob。这个细节容易漏——带 glob 的白名单条目**只保护自己匹配到的路径，不保护其下所有子项**。

## 附录 C：本文档中的「源码中不存在」清单

为避免误传，明确记录以下**需求提到但 Mole 没有**的内容：

| 需求提及 | 源码事实 |
|---|---|
| Arq 备份保护 | `lib/` `bin/` 中无任何 `arq` 匹配 |
| Time Machine 数据目录保护 | `app_protection*.sh` 中无 TimeMachine 条目；只有行为式的 `tmutil` 状态检查 + 48h 安全窗 |
| CrashPlan / Resilio / Syncthing / pCloud / CCC / Duplicacy | 无 |
| SIP 路径清单 | 无；只有 `_mole_is_critical_deletion_path` 的字符串/inode 清单 + 错误码 10 |
| `csrutil` 参与保护判定 | 无；只在 `mo --version` 输出里显示 |
| `lsregister dump` 用于归属解析 | 无；只在卸载后做 `-u` / `-gc` |
| 生物识别的 bundle id 保护 | 只有 `*biometrickit*` 一个子串匹配（`app_protection.sh:64`） |
| 浏览器 profile 的「用户数据黑名单」数组 | 无；靠「只列白名单叶子」结构性排除 |
| `pkgutil --pkgs` 判断「某路径属于某 pkg」 | 反向做了（bundle id → `lsbom` payload → 白名单），见 §4.4 |
