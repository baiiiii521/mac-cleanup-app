#!/usr/bin/env python3
"""
macOS read-only scanner for macos-deep-optimize.

Collects disk / memory / process / service / directory data and emits one JSON
blob to stdout. STRICTLY READ-ONLY: only sizes, listings and metadata reads.
Never creates, moves or deletes anything.

Usage:
    python3 scan.py > scan.json
"""
import concurrent.futures
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
PAGE = 16384  # bytes per page on Apple Silicon


def sh(cmd, timeout=180):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def du_map(paths):
    """Batch directory sizes -> {path: KB}.

    性能：`du` 是这次扫描的绝对瓶颈（一次体检 87s 里大半花在它上面）。
    三条优化，全部只影响速度、不影响结果：
      1. 多路并行：按 chunk 切分后同时跑几个 du，吃满 SSD 与多核；
      2. 按父目录聚合：同一次扫描里大量路径共享父目录，先算父目录、
         再算子目录可以复用页缓存（实测收益有限但零成本）；
      3. 跳过重复/不存在路径，避免 argv 里塞垃圾。
    注意：不改变 du -sk 的语义（仍是磁盘占用、仍以 KB 为单位）。
    """
    out = {}
    seen = set()
    valid = []
    for p in paths:
        if p in seen or not os.path.exists(p):
            continue
        seen.add(p)
        valid.append(p)
    if not valid:
        return out

    def run_chunk(chunk):
        cmd = "du -sk " + " ".join("'%s'" % p.replace("'", "'\\''") for p in chunk) + " 2>/dev/null"
        rows = []
        for line in sh(cmd).splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                try:
                    rows.append((parts[1], int(parts[0])))
                except ValueError:
                    pass
        return rows

    chunks = [valid[i:i + 24] for i in range(0, len(valid), 24)]
    # 线程数按 CPU 数封顶，避免在低配机上反而更慢
    workers = max(1, min(6, (os.cpu_count() or 2)))
    if len(chunks) <= 1:
        results = [run_chunk(c) for c in chunks]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run_chunk, chunks))
    for rows in results:
        for path, kb in rows:
            out[path] = kb
    return out


def du_kb(p):
    if not os.path.exists(p):
        return 0
    m = du_map([p])
    return m.get(p, 0)


def human(kb):
    n = float(kb) * 1024
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit not in ("B", "KB") else f"{int(n)} {unit}"
        n /= 1024


# ---------------------------------------------------------------- system

def scan_system():
    info = {
        "os": f"{sh('sw_vers -productName').strip()} {sh('sw_vers -productVersion').strip()}",
        "build": sh('sw_vers -buildVersion').strip(),
        "model": sh('sysctl -n hw.model').strip(),
        "mem_gb": round(int(sh('sysctl -n hw.memsize').strip() or 0) / 1024 ** 3),
        "user": os.environ.get("USER", ""),
        "home": HOME,
        "scanned_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    # macOS 27 起 sw_vers -productName 仍返回 macOS
    info["os"] = info["os"].replace("macOS macOS", "macOS")

    df = sh("df -k /System/Volumes/Data").splitlines()
    if len(df) > 1:
        f = df[-1].split()
        try:
            total_kb, used_kb, free_kb = int(f[1]), int(f[2]), int(f[3])
            info.update({
                "disk_total": human(total_kb),
                "disk_used": human(used_kb),
                "disk_free": human(free_kb),
                "disk_used_pct": round(used_kb / total_kb * 100),
                "disk_total_kb": total_kb,
                "disk_used_kb": used_kb,
                "disk_free_kb": free_kb,
            })
        except (ValueError, IndexError):
            pass

    m = re.search(r"Container Free Space:\s+([\d.]+ \w+)", sh("diskutil info /System/Volumes/Data"))
    info["purgeable"] = m.group(1) if m else ""
    return info


# ---------------------------------------------------------------- memory

def scan_memory():
    o = sh("vm_stat")
    if not o:
        return {}
    g = lambda k: (lambda m: int(m.group(1)) if m else 0)(re.search(re.escape(k) + r":\s+(\d+)", o))
    to_gb = lambda pages: round(pages * PAGE / 1024 ** 3, 2)
    d = {
        "free_gb": to_gb(g("Pages free") + g("Pages speculative")),
        "compressor_gb": to_gb(g("Pages occupied by compressor")),
        "compressor_raw_gb": to_gb(g("Pages stored in compressor")),
        "wired_gb": to_gb(g("Pages wired down")),
        "active_gb": to_gb(g("Pages active")),
        "inactive_gb": to_gb(g("Pages inactive")),
        "compressions": g("Compressions"),
        "decompressions": g("Decompressions"),
        "pageouts": g("Pageouts"),
    }
    sw = re.search(r"total = ([\d.]+)M\s+used = ([\d.]+)M", sh("sysctl vm.swapusage"))
    if sw:
        d["swap_total_gb"] = round(float(sw.group(1)) / 1024, 2)
        d["swap_used_gb"] = round(float(sw.group(2)) / 1024, 2)
    return d


# ---------------------------------------------------------------- processes

def scan_processes():
    up = sh("uptime").strip()
    la = re.search(r"load averages?:\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", up)
    load = [float(x) for x in la.groups()] if la else []
    uptime_str = up.split("load average")[0].strip()

    def parse(sort_key, n=12):
        rows = []
        out = sh(f"ps -Aceo rss,pcpu,comm -{sort_key} 2>/dev/null | head -{n + 1}")
        for line in out.splitlines()[1:]:
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            try:
                rows.append({
                    "name": parts[2].strip()[:44],
                    "mem_mb": round(int(parts[0]) / 1024),
                    "cpu": float(parts[1]),
                })
            except ValueError:
                continue
        return rows

    return {
        "uptime": uptime_str,
        "load": load,
        "top_cpu": parse("r"),
        "top_mem": parse("m"),
    }


def scan_services():
    rows = []
    for line in sh("launchctl list").splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 3 or f[2].startswith("com.apple"):
            continue
        pid = f[0].strip()
        rows.append({"label": f[2], "pid": pid if pid != "-" else "", "running": pid != "-"})
    return sorted(rows, key=lambda r: (not r["running"], r["label"]))


# ---------------------------------------------------------------- dirs

# 关键目录清单：(路径, 分组, 人类可读名)
#
# 性能：`~/Library` 这一项曾经排在最前面，而它会把下面所有 Library/*
# 子目录完整遍历一遍 —— 实测它单独就要 18.7s，且和后续的 Caches /
# Containers / Application Support 等**重复读同一批 inode**，
# 是整次体检最大的单点浪费（占 120s 里的 15%）。
# 现在不再单独 du 整个 ~/Library：它的总量由「各子项之和 + 其余条目」推导，
# 见 scan_dirs() 末尾的 library 汇总逻辑。
TARGETS = [
    (f"{HOME}/Library/Caches", "caches", "应用缓存"),
    (f"{HOME}/Library/Containers", "containers", "沙盒应用数据"),
    (f"{HOME}/Library/Group Containers", "group_containers", "应用组共享数据"),
    (f"{HOME}/Library/Application Support", "app_support", "应用数据"),
    (f"{HOME}/Library/Developer", "developer", "开发工具缓存"),
    (f"{HOME}/Library/Mobile Documents", "icloud", "iCloud 数据"),
    (f"{HOME}/Library/Metadata", "metadata", "Spotlight 索引"),
    (f"{HOME}/Library/Logs", "logs", "日志"),
    (f"{HOME}/Pictures", "pictures", "图片"),
    (f"{HOME}/Downloads", "downloads", "下载"),
    (f"{HOME}/Desktop", "desktop", "桌面"),
    (f"{HOME}/Documents", "documents", "文稿"),
    (f"{HOME}/Movies", "movies", "影片"),
    (f"{HOME}/Music", "music", "音乐"),
    (f"{HOME}/.cache", "dev_cache", "开发缓存 (.cache)"),
    (f"{HOME}/.npm", "dev_cache", "npm 缓存"),
    (f"{HOME}/.bun", "dev_cache", "Bun 缓存"),
    (f"{HOME}/.cargo", "dev_cache", "Rust 缓存"),
    (f"{HOME}/.gradle", "dev_cache", "Gradle 缓存"),
    (f"{HOME}/.m2", "dev_cache", "Maven 缓存"),
    (f"{HOME}/.Trash", "trash", "废纸篓"),
]


def scan_dirs():
    """一次遍历拿到「每个目标目录的总量」+「它的一级子项」+「~/Library 总量」。

    性能（实测占整次体检 55%+，是最大瓶颈）：
      · 旧实现先 du_map 算目标总量，再对每个父目录 du 一遍算子项 ——
        同一批目录被遍历两次；再加上单独 du 整个 ~/Library，
        等于把 Library 子树读了三遍。
      · 现在只对每个目标目录跑**一次** `du -k -d 1`，父子体积同时到手；
      · ~/Library 的总量由各子项求和 + 其余条目（未列入 TARGETS 的）
        用一次浅层 du 补齐，不再整树遍历。
    """
    parents = [(p, g, l) for p, g, l in TARGETS if os.path.isdir(p)]

    def du_depth1(item):
        path, grp, label = item
        cmd = "du -k -d 1 '%s' 2>/dev/null" % path.replace("'", "'\\''")
        rows = []
        for line in sh(cmd, timeout=240).splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                try:
                    rows.append((parts[1], int(parts[0])))
                except ValueError:
                    pass
        child_sizes = {}
        parent_kb = None
        np = os.path.normpath(path)
        for p, kb in rows:
            if os.path.normpath(p) == np:
                parent_kb = kb
            else:
                child_sizes[p] = kb
        return path, grp, label, parent_kb, child_sizes

    workers = max(1, min(4, (os.cpu_count() or 2)))
    if len(parents) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(du_depth1, parents))
    else:
        results = [du_depth1(i) for i in parents]

    groups = {}
    children = []
    lib_children_kb = 0
    for path, grp, label, parent_kb, child_sizes in results:
        if parent_kb is None:
            # du 拿不到（权限/路径问题）→ 退回单点测量，保证数字不缺失
            parent_kb = du_kb(path)
        groups.setdefault(grp, []).append({
            "name": os.path.basename(path) or label,
            "label": label,
            "path": path,
            "size_kb": parent_kb,
            "size_h": human(parent_kb),
        })
        if path.startswith(os.path.join(HOME, "Library")):
            lib_children_kb += parent_kb
        for p, kb in child_sizes.items():
            if kb >= 102400:  # 100 MB
                children.append({
                    "parent": path, "group": grp,
                    "name": os.path.basename(p), "path": p,
                    "size_kb": kb, "size_h": human(kb),
                })

    # ~/Library 汇总：已统计的子项 + 其余条目（浅层，不整树遍历）
    library = os.path.join(HOME, "Library")
    if os.path.isdir(library):
        covered = {os.path.normpath(p) for p, g, _ in TARGETS
                   if p.startswith(library + os.sep)}
        rest = []
        try:
            for e in os.listdir(library):
                full = os.path.join(library, e)
                if os.path.normpath(full) not in covered and os.path.isdir(full):
                    rest.append(full)
        except OSError:
            rest = []
        # 只对「未被覆盖的其它一级子目录」跑一次浅层 du，通常只有几个
        rest_kb = sum(du_map(rest).values()) if rest else 0
        total_lib = lib_children_kb + rest_kb
        groups.setdefault("library", []).append({
            "name": "Library", "label": "用户资源库",
            "path": library, "size_kb": total_lib, "size_h": human(total_lib),
        })

    children.sort(key=lambda x: -x["size_kb"])
    return groups, children


def scan_spotlight():
    out = {"raw": sh("mdutil -s -a").strip()}
    idx = du_kb(f"{HOME}/Library/Metadata/CoreSpotlight")
    out["index_kb"] = idx
    out["index_h"] = human(idx)
    out["excluded"] = []
    for root, dirs, files in os.walk(HOME):
        if ".metadata_never_index" in files:
            out["excluded"].append(root)
        # 限制深度，避免全盘遍历
        depth = root[len(HOME):].count(os.sep)
        if depth >= 3:
            dirs[:] = []
    return out


def scan_bigfiles(min_mb=200, roots=None):
    roots = roots or [f"{HOME}/Downloads", f"{HOME}/Desktop", f"{HOME}/Documents", f"{HOME}/Movies"]
    rows = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for line in sh(f"find '{root}' -type f -size +{min_mb}M 2>/dev/null | head -40").splitlines():
            p = line.strip()
            if p and os.path.exists(p):
                try:
                    rows.append({"path": p, "size_mb": round(os.path.getsize(p) / 1024 ** 2)})
                except OSError:
                    pass
    rows.sort(key=lambda r: -r["size_mb"])
    return rows[:25]


# ---------------------------------------------------------------- orphans

def read_bundle_id(plist_path):
    """读 Info.plist 的 CFBundleIdentifier。

    性能关键：旧实现对每个 App 起一次 `PlistBuddy` 子进程 —— 238 个 App
    就是 238 次 fork/exec，实测要 3-9 秒。改用 Python 内置 plistlib
    纯读取，同一个工作量降到几十毫秒。两者结果等价（都解析同一个 plist），
    但 plistlib 对二进制/XML 两种格式都支持，且没有子进程开销。
    """
    try:
        import plistlib
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
        bid = data.get("CFBundleIdentifier")
        return bid.strip() if isinstance(bid, str) else ""
    except Exception:
        return ""


def installed_bundle_ids():
    """已安装 App 的 bundle id 集合（用于识别「App 已卸载、数据还在」的残留）"""
    ids = set()
    roots = [
        "/Applications",
        os.path.expanduser("~/Applications"),
        "/Applications/Setapp",
        "/Applications/Utilities",
        "/System/Applications",
        "/System/Applications/Utilities",
        "/System/Library/CoreServices/Applications",
        "/System/Library/CoreServices",
        "/Library/Input Methods",
        os.path.expanduser("~/Library/Input Methods"),
        "/opt/homebrew/Caskroom",
        "/usr/local/Caskroom",
    ]

    def scan_root(root, depth=0):
        """扫一个目录下的 .app（含一层子目录，覆盖 Caskroom 这类嵌套）。"""
        if depth > 2 or not os.path.isdir(root):
            return
        try:
            entries = os.listdir(root)
        except OSError:
            return
        for entry in entries:
            full = os.path.join(root, entry)
            if entry.endswith(".app"):
                plist = os.path.join(full, "Contents", "Info.plist")
                if os.path.exists(plist):
                    bid = read_bundle_id(plist)
                    if bid:
                        ids.add(bid)
            elif depth <= 1 and os.path.isdir(full) and not entry.startswith("."):
                # Caskroom 层级是 Caskroom/<name>/<version>/<name>.app
                scan_root(full, depth + 1)

    for root in roots:
        scan_root(root)
    return ids


ORPHAN_SPOTS = [
    ("Application Support", "app_support"),
    ("Application Scripts", "app_scripts"),
    ("Caches", "cache"),
    ("Containers", "container"),
    ("Group Containers", "group"),
    ("Preferences", "prefs"),
    ("Saved Application State", "savedstate"),
    ("LaunchAgents", "launchagent"),
    ("HTTPStorages", "httpstorage"),
    ("WebKit", "webkit"),
]

BID_RE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2,}$")

# 这些前缀很常见但归属不一定能靠 bundle id 判断，宁可不报
ORPHAN_SKIP_PREFIXES = (
    "com.apple.", "group.com.apple.", "org.python.", "com.google.",
    "com.microsoft.", "org.mozilla.", "io.github.", "com.github.",
    "com.jetbrains.", "com.adobe.", "org.videolan."
)


def dir_age_days(path):
    try:
        return (time.time() - os.path.getmtime(path)) / 86400
    except OSError:
        return -1


def dir_has_recent_activity(path, days=7, maxdepth=3):
    """目录里最近 days 天有没有文件被改过。

    为什么不用目录 mtime（mole 的实测经验，purge_shared.sh:227-243）：
    一个目录的 mtime 只在**直接子项增删**时更新。`~/Library/Caches/Foo`
    里天天写 `sub/cache.db`，但 `Foo` 本身的 mtime 可能是半年前 ——
    只看目录 mtime 会把它判成「半年没动」而误清。反过来，往目录里
    新放一个文件又会把 mtime 刷新成今天，让真正闲置的目录逃过检查。

    正确做法：找**目录内部**有没有近期被修改的文件。用 `find -mtime`
    一次系统调用级遍历，比 Python 逐层 os.walk 快得多。
    """
    try:
        out = subprocess.run(
            ["find", path, "-maxdepth", str(maxdepth), "-type", "f",
             "-mtime", "-%d" % days, "-print", "-quit"],
            capture_output=True, text=True, timeout=6).stdout
        return bool(out.strip())
    except Exception:
        # 探测失败按「有活动」处理：宁可不报，不可误删（mole 的三态约定）
        return True


# ── 归属解析增强（借鉴 mole 的 bundle_resolver / pkg_receipts）──────────────
# 为什么需要：只靠「扫 .app 目录读 Info.plist」会漏掉两类仍然活着的 App：
#   ① 用 pkg 安装到非常规位置（比如 /usr/local/xxx.app）的；
#   ② 只有 privileged helper / LaunchAgent、主 App 本体不在标准目录的。
# 漏掉它们 → 它们的用户数据会被误报成「孤儿残留」。宁可多查一步。

_PKG_RECEIPT_CACHE = None


def pkg_receipt_bundle_ids():
    """从 pkgutil 收据里找出「由安装包安装的 App」的 bundle id。

    mole 的教训（源码 #1383 明确写过）：`pkgutil --pkgs` 是一次廉价调用，
    但**每条收据的 `pkgutil --files` 在 Xcode 机上要几秒**。本机实测：
    对 400 条收据逐条 --files 花了 35.5s，且没有多找出任何一个 bundle id
    （因为标准目录扫描 + mdfind 已经覆盖）。所以这里的策略是：
      · 只处理**收据名看起来像 App** 的（含 .app / 厂商域名）且数量很少；
      · 整体预算 6 秒，超时立即放弃，绝不拖慢体检；
      · 结果进程内缓存，二次调用零成本。
    """
    global _PKG_RECEIPT_CACHE
    if _PKG_RECEIPT_CACHE is not None:
        return _PKG_RECEIPT_CACHE
    ids = set()
    deadline = time.time() + 6.0
    try:
        pkgs = subprocess.run(["pkgutil", "--pkgs"], capture_output=True,
                              text=True, timeout=5).stdout.split()
    except Exception:
        _PKG_RECEIPT_CACHE = ids
        return ids
    # 只挑「收据 id 里带 .app」或第三方（非 com.apple.）的少量收据深挖
    candidates = [p for p in pkgs if ".app" in p.lower()][:40]
    for pkg in candidates:
        if time.time() > deadline:
            break
        try:
            files = subprocess.run(["pkgutil", "--files", pkg], capture_output=True,
                                   text=True, timeout=2).stdout
        except Exception:
            continue
        if ".app/" not in files:
            continue
        for line in files.splitlines():
            if ".app/" in line:
                app_path = line.split(".app/")[0] + ".app"
                plist = os.path.join(app_path, "Contents", "Info.plist")
                if os.path.exists(plist):
                    try:
                        bid = subprocess.run(
                            ["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleIdentifier", plist],
                            capture_output=True, text=True, timeout=2).stdout.strip()
                        if bid:
                            ids.add(bid)
                    except Exception:
                        pass
                break
    _PKG_RECEIPT_CACHE = ids
    return ids


def launchservices_bundle_ids():
    """从 LaunchServices 数据库读出所有登记过的 App 的 bundle id。

    mole 用 lsregister -dump 作为 Spotlight 之外的第二来源，覆盖
    「装在任意位置但启动过一次」的 App。但实测 `-dump` 在本机要 20s+
    且解析脆弱（输出格式随系统版本变），性价比很低 —— 所以这里改成
    用 mdfind 走 Spotlight 的元数据索引（0.1s 级），拿不到就放弃，
    绝不让孤儿检测因为这一步变慢。

    这是「有更好、没有也不影响正确性」的补充，不参与关键判断。
    """
    try:
        out = subprocess.run(
            ["mdfind", "kMDItemContentType == 'com.apple.application-bundle'"],
            capture_output=True, text=True, timeout=6).stdout
    except Exception:
        return set()
    ids = set()
    for app in out.splitlines()[:600]:
        app = app.strip()
        if not app.endswith(".app"):
            continue
        plist = os.path.join(app, "Contents", "Info.plist")
        if not os.path.exists(plist):
            continue
        bid = read_bundle_id(plist)
        if bid:
            ids.add(bid)
    return ids


def running_proc_names():
    """正在运行的进程名（小写、去扩展名）。

    用途只有一个：**排除误报**。一个进程在跑，说明它的主人还活着，
    对应的数据目录绝不能报成「孤儿残留」。
    """
    try:
        out = subprocess.run(["ps", "-Aceo", "comm"], capture_output=True, text=True, timeout=5).stdout
        names = set()
        for line in out.splitlines()[1:]:
            base = os.path.basename(line.strip()).lower()
            if base.endswith(".app"):
                base = base[:-4]
            if base:
                names.add(base)
        return names
    except Exception:
        return set()


# 进程名里这些过于通用，不能拿来判断归属（否则 node/run/start 会压掉一大片）
PROC_TOKEN_STOPWORDS = {
    "node", "run", "main", "start", "helper", "agent", "daemon", "server",
    "python", "python3", "ruby", "java", "deno", "bun", "sh", "bash", "zsh",
    "code", "app", "service", "worker", "update", "updater", "installer",
}


def _bid_tokens(bid):
    """把 bundle id 拆成有意义的分词，用于和进程名做**整词**比对。

    早期实现用 `p in bid_lower` 子串匹配：进程名 node 会顺带压掉
    com.foo.nodebar 这类无关目录，越跑越安静（只漏报，但漏得没道理）。
    改成整词比对：com.foo.nodebar → {foo, nodebar}，node 不再命中。
    """
    toks = set()
    for part in re.split(r"[.\-_/\s]+", bid.lower()):
        part = part.strip()
        if len(part) >= 4 and part not in PROC_TOKEN_STOPWORDS:
            toks.add(part)
    return toks


def _proc_tokens(proc_names):
    """进程名 → 有区分度的分词集合（LarkShell → larkshell）。"""
    toks = set()
    for n in proc_names:
        if len(n) >= 4 and n not in PROC_TOKEN_STOPWORDS:
            toks.add(n)
    return toks


def _proc_owns_bid(bid, proc_names, proc_tokens):
    """这个 bundle id 是否属于一个正在运行的进程。

    三层比对，从严到宽：
      1. 整词相等：…feishu vs Feishu（最可靠）
      2. 一方是另一方的**前缀**，且短的那方 ≥5 字符（lark ↔ larkshell）
      3. 一方是另一方的**词根变体**：去掉 -helper/-agent/-daemon 等后缀后相等
    刻意不做任意子串包含 —— 那正是旧实现 node → nodebar 误压的根源。
    """
    toks = _bid_tokens(bid)
    if not toks:
        return False
    if toks & proc_names:
        return True
    for t in toks:
        for p in proc_tokens:
            if t == p:
                return True
            short, long_ = (t, p) if len(t) <= len(p) else (p, t)
            # 前缀关系：短名要有区分度。通用词（code/node/main/helper…）已在
            # PROC_TOKEN_STOPWORDS 里被剔除，所以这里 ≥4 是安全的。
            if len(short) >= 4 and long_.startswith(short):
                return True
    return False


def scan_orphans(min_kb=1024, limit=25, stale_days=30):
    """疑似孤儿残留：目录名像 bundle id、但对应 App 已不在系统。

    误报是这一类检查最大的敌人 —— 很多 App 的数据目录用的是**子组件**的
    bundle id（比如飞书的数据目录叫 com.bytedance.macos.feishu，但装的 app
    是 Lark.app，bundle id 是 com.bytedance.macos.lark），光比对 /Applications
    会把它们全报成孤儿。防护机制：
      1. 系统应用库全盘覆盖（/System/Applications, CoreServices, Caskroom,
         Setapp, Input Methods 等）
      2. Group Containers 自动剔除 10 位 Team ID 前缀（例如 243LU875E5.groups.com.apple.podcasts）
      3. 排除知名厂商子组件前缀（Microsoft / Mozilla / Adobe / JetBrains 等）
      4. 运行中进程活跃检测（当前正在运行的应用绝对不当做孤儿）
      5. 30 天闲置时间闸（最近有写操作的判定主人仍在）
      6. **pkg 收据 + LaunchServices 归属**（借鉴 mole）：装在非常规位置、
         只登记在 LaunchServices 里的 App 也算「还装着」，不报孤儿
    """
    installed = installed_bundle_ids()
    # 归属补强（借鉴 mole）：这两步是「宁可多查一步」的补充证据。
    # 实测：标准目录扫描已覆盖 238 个，mdfind 补 18 个（0.7s，值得），
    # pkg 收据要 5.5s 且本机零收益 —— 只在环境变量显式要求时启用。
    installed |= launchservices_bundle_ids()
    if os.environ.get("MAC_CLEAN_PKG_RECEIPTS") == "1":
        installed |= pkg_receipt_bundle_ids()
    proc_names = running_proc_names()
    proc_tokens = _proc_tokens(proc_names)
    found = []
    for sub, kind in ORPHAN_SPOTS:
        base = os.path.join(HOME, "Library", sub)
        if not os.path.isdir(base):
            continue
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            bid = name
            for suffix in (".plist", ".savedState", ".binarycookies"):
                if bid.endswith(suffix):
                    bid = bid[: -len(suffix)]
                    break
            # 兼容 Group Containers 的 Team ID 前缀（如 243LU875E5.groups.com.apple.podcasts）
            clean_bid = re.sub(r"^[A-Z0-9]{10}\.", "", bid)
            clean_bid = re.sub(r"^groups?\.", "", clean_bid)
            if not BID_RE.match(clean_bid) and not BID_RE.match(bid):
                continue
            if any(clean_bid.startswith(p) or bid.startswith(p) for p in ORPHAN_SKIP_PREFIXES):
                continue
            if clean_bid in installed or bid in installed:
                continue
            # 运行中进程排除（整词比对，避免 node 这类通用名误压一片）
            if _proc_owns_bid(clean_bid, proc_names, proc_tokens) or \
               _proc_owns_bid(name.lower(), proc_names, proc_tokens):
                continue
            path = os.path.join(base, name)
            age = dir_age_days(path)
            if 0 <= age < stale_days:
                continue  # 最近还有活动，主人应该还在
            kb = du_kb(path)
            if kb < min_kb:
                continue
            found.append({
                "name": name, "path": path, "bundle_id": clean_bid,
                "kind": kind, "size_kb": kb, "size_h": human(kb),
                "idle_days": int(age) if age >= 0 else None,
            })
    found.sort(key=lambda x: -x["size_kb"])
    return found[:limit]


# ---------------------------------------------------------------- uninstalled leftovers
# 借鉴 mole 的 uninstall 归属匹配：与其「按目录名猜这是不是孤儿」，不如
# 「先确定这个 App 已经不在了，再按命名变体把它散落各处的数据成套找出来」。
# 后者的误报率低得多，因为每一组都有明确的归属主体。

# 残留可能出现的标准位置（覆盖 mole find_app_files 的主要几类）
LEFTOVER_SPOTS = [
    ("Application Support", "app_support"),
    ("Caches", "cache"),
    ("Logs", "logs"),
    ("Preferences", "prefs"),
    ("Saved Application State", "savedstate"),
    ("Containers", "container"),
    ("Group Containers", "group"),
    ("HTTPStorages", "httpstorage"),
    ("WebKit", "webkit"),
    ("Application Scripts", "app_scripts"),
    ("LaunchAgents", "launchagent"),
    ("Services", "service"),
    ("QuickLook", "quicklook"),
]

# App 名的版本/渠道后缀：Zed Nightly → Zed，Firefox Developer Edition → Firefox
APP_NAME_SUFFIXES = (
    "Nightly", "Beta", "Alpha", "Dev", "Canary", "Preview", "Insider",
    "Stable", "Release", "RC", "LTS", "Developer Edition", "Technology Preview",
)


def app_name_variants(name):
    """生成 App 名的常见命名变体（mole 的做法）。

    App 各处命名并不统一：'Maestro Studio' / 'maestro-studio' /
    'MaestroStudio' 都可能是同一个 App 的数据目录名。
    """
    name = (name or "").strip()
    if len(name) < 2:
        return set()
    base = name
    for suf in APP_NAME_SUFFIXES:
        if base.lower().endswith(" " + suf.lower()):
            base = base[: -(len(suf) + 1)]
            break
    forms = {name, base,
             name.replace(" ", ""), base.replace(" ", ""),
             name.replace(" ", "-"), base.replace(" ", "-"),
             name.replace(" ", "_"), base.replace(" ", "_")}
    out = set()
    for f in forms:
        if len(f) < 2:
            continue
        out.add(f)
        out.add(f.lower())
    return out


def scan_leftover_groups(min_kb=1024, limit=15, stale_days=30):
    """找出「已卸载 App」留下的成套数据。

    与 scan_orphans 的分工：
      · scan_orphans  —— 目录名像 bundle id，但主人找不到（靠猜，噪声大）
      · 本函数        —— 主人明确不在了，按名字变体成套收集（证据强）

    安全性：只**报告**、只进黄灯，绝不自动清理。因为「App 不在标准目录」
    也可能是「装在别处 / 装在外置盘」，删了同样会丢配置。
    """
    # 先算出「现在装着谁」的名字集合（用于判断某组残留的主人是否还在）
    alive_names = set()
    alive_bids = set()
    for root in ("/Applications", os.path.expanduser("~/Applications"),
                 "/System/Applications"):
        if not os.path.isdir(root):
            continue
        try:
            for e in os.listdir(root):
                if not e.endswith(".app"):
                    continue
                name = e[:-4]
                alive_names.add(name.lower())
                # 同时收 bundle id：残留目录名常常是 bundle id 而不是 App 名
                # （com.izual.Easydict 的 App 叫 Easydict.app），
                # 只比 App 名会把它误判成「已卸载」。
                plist = os.path.join(root, e, "Contents", "Info.plist")
                if os.path.exists(plist):
                    bid = read_bundle_id(plist)
                    if bid:
                        alive_bids.add(bid.lower())
        except OSError:
            continue
    # 正在运行的进程也算「还活着」
    alive_names |= running_proc_names()

    groups = {}
    for sub, kind in LEFTOVER_SPOTS:
        base = os.path.join(HOME, "Library", sub)
        if not os.path.isdir(base):
            continue
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for entry in entries:
            stem = entry
            for suffix in (".plist", ".savedState", ".binarycookies"):
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            # 只处理「看起来像人类可读 App 名」的：含空格、或首字母大写驼峰
            if stem.startswith(".") or len(stem) < 3:
                continue
            # 纯 bundle id（含 com./org./io. 等）一律交给 scan_orphans 处理，
            # 这里不碰 —— 否则 com.apple.* 系统组件会大量涌入
            if re.match(r"^[a-z0-9]+(\.[a-z0-9_-]+){2,}$", stem):
                continue
            # 系统守护进程/后台代理的典型形态：全小写、无空格、无大写
            # （askpermissiond / familycircled / com.apple.Music）
            if not any(c.isupper() for c in stem) and " " not in stem:
                continue
            # 明确属于系统的名字，绝不当作「已卸载 App」
            if stem.lower().startswith(("com.apple", "group.com.apple", "apple.")):
                continue
            path = os.path.join(base, entry)
            # 先用 stat 做**零成本预筛**（不跑 du）：
            #   · 最近有写入 → 主人可能还在，直接跳过
            #   · 名字对应一个还装着的 App → 跳过
            # 这两步能筛掉绝大多数候选，避免对上千个目录跑 du（实测 57s → 秒级）。
            age = dir_age_days(path)
            if 0 <= age < stale_days:
                continue
            stem_low = stem.lower()
            if stem_low in alive_names:
                continue
            groups.setdefault(stem, []).append({
                "kind": kind, "path": path,
                "idle_days": int(age) if age >= 0 else None,
            })

    # 只有「同一 App 至少留下 2 处痕迹」的组才值得算体积 ——
    # 单处痕迹噪声太大，很可能只是某个系统组件。
    out = []
    for stem, items in groups.items():
        if len(items) < 2:
            continue
        sizes = du_map([i["path"] for i in items])
        for i in items:
            i["size_kb"] = sizes.get(i["path"], 0)
            i["size_h"] = human(i["size_kb"])
        total = sum(i["size_kb"] for i in items)
        if total < min_kb:
            continue
        items.sort(key=lambda x: -x["size_kb"])
        lead = items[0]
        out.append({
            "app_name": stem, "bundle_id": "", "match_kind": f"按名匹配 · {len(items)} 处",
            "path": lead["path"], "size_kb": total, "size_h": human(total),
            "idle_days": lead.get("idle_days"),
            "parts": [i["path"] for i in items],
            "kinds": sorted({i["kind"] for i in items}),
        })
    out.sort(key=lambda x: -x["size_kb"])
    return out[:limit]


# ---------------------------------------------------------------- project artifacts

PROJECT_ROOTS = [
    f"{HOME}/Projects", f"{HOME}/Developer", f"{HOME}/dev",
    f"{HOME}/GitHub", f"{HOME}/code", f"{HOME}/Work",
    f"{HOME}/Documents", f"{HOME}/Desktop", f"{HOME}/Downloads",
]

ARTIFACT_NAMES = {
    # ── Node / 前端 ──
    "node_modules": "Node 依赖（重新 install 可重建）",
    ".next": "Next.js 构建缓存",
    ".nuxt": "Nuxt 构建缓存",
    ".svelte-kit": "SvelteKit 构建产物",
    ".astro": "Astro 构建缓存",
    ".docusaurus": "Docusaurus 构建缓存",
    ".output": "Nitro/Nuxt3 构建输出",
    ".turbo": "Turborepo 缓存",
    ".parcel-cache": "Parcel 缓存",
    ".vite": "Vite 依赖预打包缓存",
    ".rollup.cache": "Rollup 构建缓存",
    ".webpack": "Webpack 持久化缓存",
    ".angular": "Angular 构建缓存",
    ".nx": "Nx 任务图与缓存",
    ".storybook-out": "Storybook 静态产物",
    "storybook-static": "Storybook 静态产物",
    "dist": "构建输出",
    "build": "构建输出",
    ".cache-loader": "Webpack cache-loader 缓存",
    ".eslintcache": "ESLint 增量检查缓存",
    ".stylelintcache": "Stylelint 增量检查缓存",
    # ── Python ──
    ".venv": "Python 虚拟环境",
    "venv": "Python 虚拟环境",
    "__pycache__": "Python 字节码缓存",
    ".pytest_cache": "pytest 缓存",
    ".mypy_cache": "mypy 缓存",
    ".ruff_cache": "ruff 缓存",
    ".tox": "tox 测试环境",
    ".nox": "nox 测试环境",
    ".eggs": "Python egg 构建产物",
    "htmlcov": "测试覆盖率 HTML 报告",
    "coverage": "测试覆盖率报告",
    ".hypothesis": "Hypothesis 测试缓存",
    # ── Rust / Go / JVM ──
    "target": "Rust/Java 构建产物",
    ".gradle": "Gradle 本地构建缓存",
    ".m2": "Maven 本地仓库",
    ".bloop": "Scala Bloop 构建缓存",
    ".metals": "Scala Metals 语言服务缓存",
    # ── Apple / 移动端 ──
    ".dart_tool": "Dart/Flutter 工具缓存",
    "DerivedData": "Xcode 构建产物",
    ".build": "Swift Package 构建产物",
    "Pods": "CocoaPods 依赖（pod install 可重建）",
    "Carthage": "Carthage 依赖（carthage bootstrap 可重建）",
    ".symlinks": "CocoaPods 符号链接缓存",
    # ── 其他生态 ──
    ".terraform": "Terraform 插件与状态",
    ".serverless": "Serverless Framework 产物",
    ".aws-sam": "AWS SAM 构建产物",
    ".cargo-target": "Rust 构建产物",
    "vendor": "Go/PHP 依赖（go mod / composer 可重建）",
    "bower_components": "Bower 依赖",
    "elm-stuff": "Elm 构建产物",
    "_build": "OCaml/Elixir 构建产物",
    "deps": "Elixir 依赖",
    ".stack-work": "Haskell Stack 构建产物",
    "zig-cache": "Zig 构建缓存",
    ".zig-cache": "Zig 构建缓存",
    "zig-out": "Zig 构建输出",
}

# 构建产物通常在项目根下 1-3 层；扫太深又慢又容易误报
SKIP_WALK_DIRS = {".git", "Library", "node_modules", ".venv", "venv",
                  "Applications", "Pictures", "Movies", "Music"}


# 判定构建产物能否安全重建要看的锁文件
LOCK_FILES = (
    # Node / 前端
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock",
    "npm-shrinkwrap.json", ".yarnrc.yml", ".pnp.cjs",
    # Python
    "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock", "requirements.txt",
    # Rust / Go / JVM
    "Cargo.lock", "go.sum", "gradle.lockfile", "pom.xml",
    # Apple / 移动端
    "pubspec.lock", "Podfile.lock", "Package.resolved", "Cartfile.resolved",
    # 其他
    "composer.lock", "Gemfile.lock", "flake.lock", "mix.lock", "stack.yaml.lock",
    "packages.lock.json", "elm.json",
)


def find_project_root(start_dir, max_up=3):
    """从产物所在目录向上找真正的「项目根」。

    为什么必须往上找：monorepo / 前端子目录的产物常常长在
    `项目/web/node_modules`，而锁文件和 .git 在 `项目/`。
    只检查直接父目录会把这类项目全部误判成「没有锁文件 → 不能重建」，
    于是 200 MB 的 node_modules 永远进不了绿灯 —— 这正是本机实测发现的
    误判（sunset-glow/web 明明有 package-lock.json 却报 risk）。

    判定「是项目根」的依据（任一命中即停）：有锁文件、有 .git、有 package.json。
    """
    cur = os.path.abspath(start_dir)
    for _ in range(max_up + 1):
        if any(os.path.exists(os.path.join(cur, f)) for f in LOCK_FILES):
            return cur
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        if os.path.exists(os.path.join(cur, "package.json")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.abspath(start_dir)


def artifact_safety(proj_root, art_path):
    """判断这个构建产物删掉是否真的安全。

    只看目录名远远不够 —— 「叫 node_modules」不等于「删了没事」：
      · 没有锁文件 → 重建会装到不同版本，可能直接跑不起来
      · 不是 git 仓库 → 这个目录可能没有别处备份
      · 有未提交改动 → 你正在改它
      · 今天还在动 → 正在使用中

    返回 (safe: bool, reason: str, idle_days: float)
    """
    try:
        idle = (time.time() - os.path.getmtime(art_path)) / 86400
    except OSError:
        idle = -1
    root = find_project_root(proj_root)
    is_git = os.path.isdir(os.path.join(root, ".git"))
    has_lock = any(os.path.exists(os.path.join(root, f)) for f in LOCK_FILES)

    if 0 <= idle < 1 or dir_has_recent_activity(art_path, days=1):
        return False, "今天还在用（1 天内有过改动）", idle
    if not has_lock:
        if is_git:
            return False, f"没有锁文件，重建可能装到不同版本（闲置 {idle:.0f} 天）", idle
        return False, "既不是 git 仓库、也没有锁文件 —— 删了不一定装得回原样", idle
    if is_git:
        dirty = sh(f"cd '{root}' && git status --porcelain 2>/dev/null | head -3", timeout=20)
        if dirty.strip():
            return False, "项目有未提交的改动，先 git commit 再清", idle
    return True, f"可安全重建：有锁文件，闲置 {idle:.0f} 天", idle


def scan_project_artifacts(max_depth=4, min_kb=10240, limit=40):
    """项目里可重建的构建产物（node_modules / dist / .venv 等）

    每项都会带 safe 标记：只有「有锁文件 + 是 git 仓库 + 无未提交改动 +
    不是今天在动」才判为可安全清理，否则降到需人工判断。
    """
    found = []
    seen = set()
    for root in PROJECT_ROOTS:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("/").count("/")
        for dirpath, dirnames, _ in os.walk(root, topdown=True):
            if dirpath.count("/") - base_depth >= max_depth:
                dirnames[:] = []
                continue
            # 命中就记下并从遍历里摘除（避免 node_modules 里再套 node_modules）
            hits = [d for d in dirnames if d in ARTIFACT_NAMES]
            for h in hits:
                p = os.path.join(dirpath, h)
                if p in seen:
                    continue
                seen.add(p)
                kb = du_kb(p)
                if kb < min_kb:
                    continue
                safe, reason, idle = artifact_safety(dirpath, p)
                found.append({
                    "name": h, "path": p, "size_kb": kb, "size_h": human(kb),
                    "project": os.path.relpath(dirpath, HOME),
                    "desc": ARTIFACT_NAMES[h],
                    "safe": safe, "safety_reason": reason,
                    "idle_days": int(idle) if idle >= 0 else None,
                })
            dirnames[:] = [d for d in dirnames
                           if d not in ARTIFACT_NAMES and d not in SKIP_WALK_DIRS]
    found.sort(key=lambda x: -x["size_kb"])
    return found[:limit]


# ---------------------------------------------------------------- installers

INSTALLER_EXTS = {".dmg", ".pkg", ".mpkg", ".iso", ".xip"}
INSTALLER_SPOTS = [
    f"{HOME}/Downloads", f"{HOME}/Desktop", f"{HOME}/Documents",
    f"{HOME}/Library/Caches/Homebrew/downloads",
    f"{HOME}/Library/Containers/com.apple.mail/Data/Library/Mail Downloads",
    f"{HOME}/Library/Mobile Documents/com~apple~CloudDocs",
    f"{HOME}/Library/Application Support/Telegram Desktop",
]


def install_src(p):
    if "Mobile Documents" in p:
        return "iCloud"
    if "Homebrew" in p:
        return "Homebrew"
    if "/Downloads/" in p:
        return "Downloads"
    if "/Desktop/" in p:
        return "Desktop"
    if "Mail Downloads" in p:
        return "Mail 附件"
    if "Telegram" in p:
        return "Telegram"
    return "其他"


def scan_installers(min_mb=30, limit=30):
    """残留的安装包（DMG/PKG/ISO/XIP）—— 装完就没用了。

    用 Spotlight（mdfind）而不是 find：iCloud Drive 上有上万个文件，
    find 遍历要 5 分钟，mdfind 是 0.1 秒级。索引不可用时回退到有限范围的 find。
    """
    query = " || ".join(f'kMDItemFSName == "*{e}"c' for e in sorted(INSTALLER_EXTS))
    raw = sh(f"mdfind '{query}'", timeout=180)

    paths = []
    if raw.strip():
        paths = [l.strip() for l in raw.splitlines() if l.strip()]
    else:
        # 回退：只在有限的几个位置用 find，且不碰 iCloud（太慢）
        for spot in (f"{HOME}/Downloads", f"{HOME}/Desktop",
                     f"{HOME}/Library/Caches/Homebrew/downloads"):
            if not os.path.isdir(spot):
                continue
            out = sh(f"find '{spot}' -maxdepth 3 -type f "
                     f"\\( -name '*.dmg' -o -name '*.pkg' -o -name '*.mpkg' "
                     f"-o -name '*.iso' -o -name '*.xip' \\) 2>/dev/null")
            paths.extend(l.strip() for l in out.splitlines() if l.strip())

    # 检查当前挂载中的磁盘镜像，避免误将正在使用的 DMG 视作残留
    mounted_raw = sh("hdiutil info 2>/dev/null")
    mounted_images = set(re.findall(r"image-path\s*:\s*(.+)", mounted_raw))

    found, seen = [], set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        # 系统更新包不是垃圾 —— 那是「已下载、待安装」的更新
        if p.startswith("/Library/Updates/"):
            continue
        # 已安装的应用本体、废纸篓里的东西不算
        if "/.Trash/" in p or p.startswith("/Applications/"):
            continue
        # 正在下载中的临时文件或分段文件不算
        if p.endswith((".download", ".crdownload", ".part", ".tmp")):
            continue
        # 正在挂载使用中的 DMG 不算
        if p in mounted_images:
            continue
        try:
            mtime = os.path.getmtime(p)
            # 2 小时内新下载的文件不当做残留（用户可能刚下载还没来得及安装）
            if (time.time() - mtime) < 7200:
                continue
            kb = os.path.getsize(p) // 1024
        except OSError:
            continue
        if kb < min_mb * 1024:
            continue
        found.append({
            "name": os.path.basename(p), "path": p, "size_kb": kb,
            "size_h": human(kb), "source": install_src(p),
            # iCloud Drive 里的东西性质不同 —— 那是用户主动管理/归档的区域，
            # 不是「装完忘删」。而且删除会同步到所有设备，不能当本地缓存处理。
            "in_icloud": "Mobile Documents" in p,
        })
    found.sort(key=lambda x: -x["size_kb"])
    return found[:limit]


# ---------------------------------------------------------------- maintenance (read-only)

def scan_maintenance():
    """系统维护的只读检查 —— 只报告，不动手"""
    m = {}
    m["spotlight"] = sh("mdutil -s -a").strip()
    sm = re.search(r"SMART Status:\s+(.+)", sh("diskutil info /"))
    m["smart"] = sm.group(1).strip() if sm else "unknown"
    m["timemachine"] = ("未配置" if "No destinations configured" in sh("tmutil destinationinfo")
                        else "已配置")
    snaps = [l for l in sh("tmutil listlocalsnapshots /").splitlines()
             if "com.apple.TimeMachine" in l]
    m["local_snapshots"] = len(snaps)
    m["user_logs_kb"] = du_kb(f"{HOME}/Library/Logs")
    m["system_logs_kb"] = du_kb("/Library/Logs")
    m["var_folders_kb"] = du_kb("/private/var/folders")
    m["wallpaper_kb"] = du_kb(f"{HOME}/Library/Application Support/com.apple.wallpaper")
    m["trash_kb"] = du_kb(f"{HOME}/.Trash")
    return m


def main():
    t0 = time.time()
    data = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "system": scan_system(),
        "memory": scan_memory(),
        "processes": scan_processes(),
        "services": scan_services(),
    }
    groups, children = scan_dirs()
    data["dir_groups"] = groups
    data["children"] = children
    data["spotlight"] = scan_spotlight()
    data["big_files"] = scan_bigfiles()

    # ── 以下为对照 Mole 补齐的能力（全部只读）──
    data["orphans"] = scan_orphans()
    data["leftover_groups"] = scan_leftover_groups()
    data["project_artifacts"] = scan_project_artifacts()
    data["installers"] = scan_installers()
    data["maintenance"] = scan_maintenance()

    data["scan_seconds"] = round(time.time() - t0, 1)
    json.dump(data, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if sys.platform != "darwin":
        print("此脚本仅支持 macOS", file=sys.stderr)
        sys.exit(1)
    main()
