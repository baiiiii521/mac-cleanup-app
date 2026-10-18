#!/usr/bin/env python3
"""
macOS 体检报告生成器 —— 把 scan.py 的 JSON 渲染成一个自包含的 HTML 页面。

用法:
    python3 scan.py > scan.json
    python3 report.py scan.json [out.html]

页面特性:
  · 磁盘总览（容量 + 三色分段进度条）
  · 内存 / 交换 / 负载诊断（判断卡顿根因）
  · 🟢🟡🔴 三色分级清单，每条带可一键复制的处置命令
  · 后台服务、常驻进程、大文件清单
  · 全程离线可看，不含任何远程资源
"""
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


# ══════════════════════════════════════════════════════════
# 分级规则
# ══════════════════════════════════════════════════════════

# 🟢 纯缓存：(路径片段, 说明, 处置命令)
# 顺序很重要：**具体的规则必须排在通用的 /Library/Caches 之前**，
# 否则会被泛化规则先命中，拿到错的说明与错的处置命令（P6 修过一轮）。
GREEN_RULES = [
    # ── 包管理器 / 语言缓存 ──
    ("/.bun/install/cache", "Bun 包下载缓存（全局）",
     'rm -rf ~/.bun/install/cache/*'),
    ("/.yarn/berry/cache", "Yarn Berry (v2+) 依赖缓存",
     'yarn cache clean 2>/dev/null || rm -rf ~/.yarn/berry/cache/*'),
    ("/Library/Caches/CocoaPods", "CocoaPods 依赖下载缓存",
     'pod cache clean --all 2>/dev/null || rm -rf ~/Library/Caches/CocoaPods/*'),
    ("/Library/Caches/go-build", "Go 语言编译构建缓存",
     'go clean -cache 2>/dev/null || rm -rf ~/Library/Caches/go-build/*'),
    ("/Library/Caches/ms-playwright", "Playwright 浏览器测试缓存",
     'rm -rf ~/Library/Caches/ms-playwright/*'),
    ("/Library/Caches/pypoetry/artifacts", "Poetry 构建产物缓存",
     'rm -rf ~/Library/Caches/pypoetry/artifacts/*'),
    ("/Library/Caches/pypoetry/cache", "Poetry 包下载缓存",
     'rm -rf ~/Library/Caches/pypoetry/cache/*'),
    ("/.pyenv/cache", "pyenv 下载的 Python 源码包",
     'rm -rf ~/.pyenv/cache/*'),
    ("/.cache/poetry", "Poetry 缓存",
     'rm -rf ~/.cache/poetry/*'),
    ("/.cache/ruff", "Ruff 检查缓存",
     'rm -rf ~/.cache/ruff/*'),
    ("/.cache/mypy", "mypy 类型检查缓存",
     'rm -rf ~/.cache/mypy/*'),
    ("/.conda/pkgs", "Conda 包缓存",
     'conda clean --packages --tarballs -y'),
    ("/.jupyter/runtime", "Jupyter 运行时缓存",
     'rm -rf ~/.jupyter/runtime/*'),
    ("/Library/Caches/ms-playwright-go", "Playwright Go 浏览器缓存",
     'rm -rf ~/Library/Caches/ms-playwright-go/*'),
    ("/Library/Caches/go-build", "Go 构建缓存",
     'go clean -cache'),
    # ── 系统 / Xcode ──
    ("/Library/Caches/com.apple.dt.XCTestCache", "Xcode 单元测试产物缓存",
     'rm -rf ~/Library/Caches/com.apple.dt.XCTestCache/*'),
    ("/Library/Caches/com.apple.helpd", "macOS 系统帮助文档索引缓存",
     'rm -rf ~/Library/Caches/com.apple.helpd/*'),
    ("/Library/Caches/CloudKit", "CloudKit 临时同步缓存",
     'rm -rf ~/Library/Caches/CloudKit/*'),
    ("/Library/Caches/Homebrew", "Homebrew 下载缓存",
     'brew cleanup --prune=all'),
    ("/Library/Developer/Xcode/DerivedData", "Xcode 构建产物",
     'rm -rf ~/Library/Developer/Xcode/DerivedData/*'),
    ("/Library/Developer/CoreSimulator/Caches", "iOS 模拟器运行时缓存",
     'rm -rf ~/Library/Developer/CoreSimulator/Caches/*'),
    ("/Library/Developer/CoreSimulator/Devices", "不再可用的 iOS 模拟器镜像",
     'xcrun simctl delete unavailable'),
    ("/Library/Developer/Xcode/iOS DeviceSupport", "旧 iOS 真机调试符号（可自动重下）",
     'rm -rf ~/Library/Developer/Xcode/iOS\\ DeviceSupport/*'),
    ("/Library/Developer/Xcode/Archives", "Xcode 归档（含发布产物，确认后再删）",
     'open ~/Library/Developer/Xcode/Archives'),
    # ── 通用（必须排在最后）──
    ("/Library/Caches", "应用缓存。清内容保留目录，会自动重建",
     'find ~/Library/Caches -mindepth 1 -maxdepth 1 -type d -exec rm -rf {}/* \\;'),
    ("/.cache", "开发工具缓存（uv / pip / 各类 CLI）",
     'rm -rf ~/.cache/*'),
    ("/.npm/_cacache", "npm 包缓存",
     'npm cache clean --force'),
    ("/.cargo/registry", "Rust crate 缓存",
     'rm -rf ~/.cargo/registry/cache'),
    ("/.gradle/caches", "Gradle 构建缓存",
     'rm -rf ~/.gradle/caches'),
    ("/.m2/repository", "Maven 本地仓库（可重新下载）",
     'rm -rf ~/.m2/repository'),
    ("/.nuget/packages", ".NET NuGet 包缓存",
     'dotnet nuget locals all --clear'),
    ("/.ivy2/cache", "Ivy 依赖缓存",
     'rm -rf ~/.ivy2/cache'),
    # 日志类：保留 7 天（与 clean.py 的 RETENTION_DAYS 一致），不整清
    ("/Library/Logs/DiagnosticReports", "崩溃报告（保留最近 7 天）",
     'find ~/Library/Logs/DiagnosticReports -type f -mtime +7 -delete'),
    ("/Library/Logs", "应用日志（保留最近 7 天）",
     'find ~/Library/Logs -type f -mtime +7 -delete'),
    ("/Library/Application Support/CrashReporter", "崩溃报告（保留最近 7 天）",
     'find ~/Library/Application\\ Support/CrashReporter -type f -mtime +7 -delete'),
]

# 🟡 需人工判断：(路径片段, 内容画像, 处置路径, 风险)
YELLOW_RULES = [
    ("com.tencent.xinWeChat", "微信：聊天数据库 + 图片视频文件",
     "微信 > 设置 > 通用 > 存储空间 > 管理，按聊天/类型清理",
     "手删文件会让聊天里的图片视频全部变「已过期」且无法恢复"),
    ("com.bytedance.macos.feishu", "飞书：聊天附件与 SDK 数据",
     "飞书 > 设置 > 通用 > 存储空间 > 清理缓存",
     "直接删可能导致重新登录并全量同步"),
    ("ZaloData", "Zalo：聊天记录 + 媒体文件",
     "Zalo > 设置 > 数据与存储 > 清理媒体缓存",
     "删 Database 会导致 Zalo 无法启动"),
    ("wpsoffice", "WPS：文档缓存与云同步副本",
     "WPS > 设置 > 清理缓存",
     "若文档只存了本地副本，清缓存可能丢失"),
    ("com.apple.wallpaper", "动态视频壁纸素材",
     "先换成静态壁纸，再删除 aerials 目录",
     "壁纸还在用时会自动重新下载"),
    ("Photos Library", "照片图库：缩略图 + 内容索引 + 数据库（通常不含原图）",
     "系统设置 > Apple ID > iCloud > 照片 > 优化 Mac 存储空间；或整体移走图库",
     "手删 derivatives 会触发从 iCloud 全量重下；删 database 直接毁库"),
    ("Mobile Documents", "iCloud Drive：本地缓存 + 各 App 的 iCloud 容器",
     "系统设置 > Apple ID > iCloud > 管理账户储存空间，逐个 App 清理",
     "删了 iPhone/iPad 上也会同步消失"),
    ("/Library/Containers", "沙盒应用数据（各 App 的私有目录）",
     "在对应 App 内部清理，或整体评估是否还需要该 App",
     "手删可能导致 App 配置重置"),
    ("/Library/Application Support", "应用数据（Profile、离线内容、缓存）",
     "进对应 App 内部清理",
     "含登录状态与配置，误删需重新登录"),
    ("Group Containers", "应用组共享数据",
     "多为系统数据（备忘录、录音等），在对应 App 内清理",
     "可能含你的个人数据"),
    ("/Downloads", "下载目录：安装包、素材、临时文件",
     "手动审查，把项目素材归档到外置盘，删除已安装的 .dmg/.pkg",
     "可能是某些文件的唯一副本"),
    ("/Desktop", "桌面文件",
     "归档到 iCloud 或项目目录",
     "注意是否开启了 iCloud 桌面同步"),
    ("/Documents", "文稿",
     "归档到 iCloud 或项目目录",
     "—"),
    ("/Movies", "影片",
     "导出到外置盘",
     "—"),
    ("/Pictures", "图片",
     "归档或开启 iCloud 优化存储",
     "—"),
]

# 🔴 不建议手删
RED_RULES = [
    ("/Library/Metadata", "Spotlight 索引数据库",
     "删除会重建，重建期间系统明显变卡，得不偿失"),
    ("com.apple.Photos", "照片图库核心数据库",
     "手删会毁掉整个图库，只能用「照片」App 或整体移走"),
    ("vm_bundles", "虚拟机沙箱镜像（Claude 等）",
     "应用核心组件，删了功能失效；请在 App 内清理或正常卸载"),
    ("/Library/Containers/B64B6CEC", "UUID 命名的容器（需查 bundle id 才知道是谁）",
     "先用 defaults read <容器>/.com.apple.containermanagerd.metadata.plist 查归属"),
]


def classify(item_path, size_kb):
    """返回 (tier, rule) —— tier ∈ green/yellow/red/info"""
    for frag, desc, cmd in GREEN_RULES:
        if frag in item_path:
            return "green", {"desc": desc, "cmd": cmd}
    # UUID 命名的容器：归属未知，不要贸然动手
    if re.search(r"/Containers/[0-9A-F]{8}-[0-9A-F]{4}-", item_path):
        return "yellow", {
            "desc": "UUID 命名的沙盒容器 —— 归属未知，先查清是哪个 App",
            "disposal": "查归属：plutil -p <容器>/.com.apple.containermanagerd.metadata.plist | grep MCMMetadataIdentifier",
            "risk": "不知道属于谁就动手，可能删掉某个 App 的全部数据",
        }
    for frag, profile, disposal in RED_RULES:
        if frag in item_path:
            return "red", {"desc": profile, "disposal": disposal}
    for frag, profile, disposal, risk in YELLOW_RULES:
        if frag in item_path:
            return "yellow", {"desc": profile, "disposal": disposal, "risk": risk}
    return "info", {}


def build_model(scan):
    """把扫描结果转成分级清单。

    规则：下钻出的具体项优先；父目录若已有子项被列出，就不再单列父目录
    （否则 Containers 34.8GB 会跟它内部的微信 29.4GB 重复计算）。
    """
    tiers = {"green": [], "yellow": [], "red": []}
    seen = set()

    children = scan.get("children", [])
    covered_parents = {c["parent"] for c in children}

    items = list(children)
    for grp, lst in scan.get("dir_groups", {}).items():
        for it in lst:
            if it["path"] not in covered_parents and it["size_kb"] > 0:
                items.append({"parent": it["path"], "group": grp, "name": it["name"],
                              "path": it["path"], "size_kb": it["size_kb"],
                              "size_h": it["size_h"]})

    for it in sorted(items, key=lambda x: -x["size_kb"]):
        p = it["path"]
        if p in seen:
            continue
        # 跳过已被更具体子项覆盖的路径
        if any(p != q and q.startswith(p + os.sep) for q in
               (c["path"] for c in children)):
            continue
        seen.add(p)
        tier, rule = classify(p, it["size_kb"])
        if tier == "info":
            continue
        tiers[tier].append({"name": it["name"], "path": p, "size": it["size_h"],
                            "size_kb": it["size_kb"], **rule})

    # ── 对照 Mole 补齐的三类（扫描结果直接进对应灯）──
    # 注意：这三类必须查 seen，否则会和上面 children 里已列出的同一路径重复计数
    # （典型：项目构建产物 node_modules 同时来自 children 与 project_artifacts）
    for it in scan.get("project_artifacts", []):
        if it["path"] in seen:
            continue
        seen.add(it["path"])
        proj = it.get("project", "?")
        base = {"name": f"构建产物 · {it['name']}", "path": it["path"],
                "size": it["size_h"], "size_kb": it["size_kb"]}
        if it.get("safe"):
            tiers["green"].append({
                **base,
                "desc": f"{it.get('desc','可重建')}　项目：{proj}　"
                        f"（{it.get('safety_reason','')}）",
                "cmd": f"rm -rf '{it['path']}'",
            })
        else:
            tiers["yellow"].append({
                **base,
                "desc": f"{it.get('desc','构建产物')}　项目：{proj}",
                "disposal": f"⚠️ {it.get('safety_reason','')}。"
                            f"确认能重建后再删（先跑一遍 cd ~/{proj} && npm install 试试）",
                "risk": "删了不一定装得回原样 —— 可能直接影响这个项目还能不能跑起来",
            })
    for it in scan.get("installers", []):
        if it["path"] in seen:
            continue
        seen.add(it["path"])
        base = {"name": f"残留安装包 · {it['name']}", "path": it["path"],
                "size": it["size_h"], "size_kb": it["size_kb"]}
        if it.get("in_icloud"):
            # iCloud Drive 是用户主动管理的区域，不能当本地缓存处理
            tiers["yellow"].append({
                **base,
                "desc": f"位于 **iCloud Drive**（{it['path'].split('Mobile Documents/')[-1].rsplit('/', 1)[0]}）"
                        f"—— iCloud 里的文件通常是你刻意保存或归档的，不是「装完忘删」",
                "disposal": "先判断：这是你主动归档的备份吗？是的话保留。"
                            "确实不需要再在「访达 > iCloud 云盘」里手动删除",
                "risk": "⚠️ 删了会同步到 iCloud 云端和所有设备（iPhone / iPad 上也同时消失），"
                        "30 天后彻底删除。这不是本地清理。",
            })
        else:
            tiers["green"].append({
                **base,
                "desc": f"安装包（来源：{it.get('source','?')}）—— 装完就没用了",
                "cmd": f"rm -f '{it['path']}'",
            })
    for it in scan.get("orphans", []):
        if it["path"] in seen:
            continue
        seen.add(it["path"])
        idle = it.get("idle_days")
        idle_s = f"，已闲置 {idle} 天" if isinstance(idle, int) else ""
        tiers["yellow"].append({
            "name": f"疑似孤儿残留 · {it['name']}",
            "path": it["path"], "size": it["size_h"], "size_kb": it["size_kb"],
            "desc": f"对应 App 已不在 /Applications{idle_s}。这类检查**会漏报也会误报**"
                    f"——有些 App 的数据目录用的是子组件的 bundle id，主人其实还在。",
            "disposal": "先打开目录看一眼内容；确认确实无用再删，或先移废纸篓放几天观察",
            "risk": "可能是仍在使用的 App 的子组件数据（典型误报：飞书 / WPS 的辅助进程目录）",
        })

    # ── 已卸载 App 的成套残留（借鉴 mole 的 uninstall 归属匹配）──
    # 孤儿检测是「按目录名猜」，这里反过来：先确定「这个 App 已经不在了」，
    # 再把它散落在各处的数据按**命名变体**成套找出来。这是最安全的残留识别
    # 方式 —— 有明确的归属主体，不是猜。
    for grp in scan.get("leftover_groups", []):
        p = grp.get("path", "")
        if not p or p in seen:
            continue
        seen.add(p)
        tiers["yellow"].append({
            "name": f"已卸载残留 · {grp.get('app_name') or grp.get('bundle_id')}",
            "path": p, "size": grp.get("size_h", ""), "size_kb": grp.get("size_kb", 0),
            "desc": f"属于已卸载的 {grp.get('app_name') or grp.get('bundle_id')}"
                    f"（{grp.get('match_kind', '按名匹配')}）",
            "disposal": "确认该 App 确实不再使用后，可整套删除；拿不准就先移废纸篓观察几天",
            "risk": "如果只是换了安装位置（比如从 /Applications 移到外置盘），删了会丢配置",
        })

    for tier in tiers:
        tiers[tier].sort(key=lambda x: -x["size_kb"])
    return tiers


def diag(mem, procs, sysinfo):
    """生成诊断结论。"""
    notes = []
    m = mem or {}
    free = m.get("free_gb", 9)
    comp = m.get("compressor_gb", 0)
    swap = m.get("swap_used_gb", 0)
    mem_gb = sysinfo.get("mem_gb", 0)
    load = (procs or {}).get("load", [0])
    la = load[0] if load else 0

    if free < 0.3:
        notes.append(("bad", "内存余量归零",
                      f"空闲仅 {free*1024:.0f} MB，系统已在靠压缩硬撑"))
    elif free < 1:
        notes.append(("warn", "内存偏紧", f"空闲 {free*1024:.0f} MB"))
    else:
        notes.append(("ok", "内存余量正常", f"空闲 {free:.1f} GB"))

    if comp > 2:
        notes.append(("bad", "内存压缩器负荷重",
                      f"压缩了 {m.get('compressor_raw_gb', 0)} GB → 占 {comp} GB，说明物理内存不够用"))
    elif comp > 1:
        notes.append(("warn", "内存压缩器工作中", f"占用 {comp} GB"))

    if swap > 0.5:
        notes.append(("warn", "已在使用交换空间", f"swap 已用 {swap} GB，说明内存压力持续存在"))

    if la > 5:
        notes.append(("warn", "系统负载偏高", f"load average = {la}（健康值 < 5）"))
    else:
        notes.append(("ok", "系统负载正常", f"load average = {la}"))

    if mem_gb and mem_gb <= 8:
        notes.append(("info", "硬件提示",
                      f"这台机器只有 {mem_gb} GB 内存。卡顿主因通常是内存不足，"
                      "清理磁盘只能缓解、不能根治。建议：大应用用完 Cmd+Q 退出、"
                      "同时最多开 2 个重型应用。"))
    return notes


def render(scan, tiers, notes):
    data = {"scan": scan, "tiers": tiers, "notes": notes}
    blob = json.dumps(data, ensure_ascii=False)
    tpl_path = os.path.join(HERE, "..", "assets", "report_template.html")
    with open(tpl_path, encoding="utf-8") as f:
        tpl = f.read()
    return tpl.replace("__REPORT_DATA__", blob)


def main():
    argv = [a for a in sys.argv[1:]]
    judge_path, positional = None, []
    for a in argv:
        if a.startswith("--judge="):
            judge_path = a.split("=", 1)[1]
        elif a == "--judge":
            judge_path = True  # 由下一个位置参数补上
        else:
            positional.append(a)
    if judge_path is True:
        judge_path = None

    if not positional:
        print(__doc__)
        sys.exit(1)
    with open(positional[0], encoding="utf-8") as f:
        scan = json.load(f)

    # 可选：合并 Jev 的判断结果
    judge = {}
    if judge_path and os.path.exists(judge_path):
        try:
            jd = json.load(open(judge_path, encoding="utf-8"))
            for row in jd.get("judged", []):
                judge[row["path"]] = row
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ 读取 judge 文件失败，忽略: {e}", file=sys.stderr)

    tiers = build_model(scan)
    for tier in tiers.values():
        for it in tier:
            j = judge.get(it["path"])
            if j:
                it["judge"] = {
                    "category": j.get("category_label", j.get("category")),
                    "confidence": j.get("category_confidence"),
                    "safe": j.get("safe_to_delete"),
                    "recover": j.get("recoverability"),
                    "advice": j.get("advice", ""),
                    "tier": j.get("tier", ""),
                }
    notes = diag(scan.get("memory"), scan.get("processes"), scan.get("system"))

    out = positional[1] if len(positional) > 1 else os.path.join(
        os.path.expanduser("~"), "Desktop",
        "macos-report-%s.html" % __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M"))
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(scan, tiers, notes))

    total = lambda tier: sum(i["size_kb"] for i in tiers[tier]) / 1024 ** 2
    print(f"报告已生成: {out}")
    print(f"  🟢 可清理   {len(tiers['green']):2d} 项  约 {total('green'):.1f} GB")
    print(f"  🟡 需判断   {len(tiers['yellow']):2d} 项  约 {total('yellow'):.1f} GB")
    print(f"  🔴 不建议   {len(tiers['red']):2d} 项  约 {total('red'):.1f} GB")
    print(f"\n打开: open '{out}'")


if __name__ == "__main__":
    main()
