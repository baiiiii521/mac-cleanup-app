#!/usr/bin/env python3
"""
安全清理执行器 —— 借 Mole 的三件套：--dry-run 预览 / whitelist 保护 / 操作日志。

**默认只预览，什么都不删。** 必须显式加 --apply 才执行，且默认走「移到废纸篓」
（可逆）；只有再加 --rm 才会直接删除。

用法:
    python3 clean.py scan.json                    # 预览（默认，安全）
    python3 clean.py scan.json --apply            # 真删 → 移废纸篓（可逆）
    python3 clean.py scan.json --apply --rm       # 直接删除（不可逆，慎用）
    python3 clean.py scan.json --include-yellow   # 把黄灯项也纳入（仍需 --apply）

    python3 clean.py --whitelist list             # 查看保护名单
    python3 clean.py --whitelist add ~/path       # 加入保护（永不清理）
    python3 clean.py --whitelist remove ~/path
    python3 clean.py --history                    # 查看操作日志

安全模型（三层）:
  1. 白名单 —— 只处理报告里被判为可清理的路径，其余一律拒绝
  2. 硬保护 —— 系统目录、HOME 本身、HOME 的一级目录永不删除
  3. 用户保护 —— ~/.config/macos-deep-optimize/whitelist 里的路径跳过

每次执行都会写入
  ~/.local/state/macos-deep-optimize/operations.log
可用 --history 回看，或设 MO_NO_OPLOG=1 关闭。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

# 不生成 __pycache__（保持仓库干净）
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.realpath(os.path.expanduser("~"))
CONFIG_DIR = os.path.expanduser("~/.config/macos-deep-optimize")
WHITELIST_FILE = os.path.join(CONFIG_DIR, "whitelist")
STATE_DIR = os.path.expanduser("~/.local/state/macos-deep-optimize")
OPLOG = os.path.join(STATE_DIR, "operations.log")

# ── 硬保护：这些路径（及其本身）永远不允许删除 ──
PROTECTED = {
    "/", "/System", "/Library", "/Applications", "/usr", "/bin", "/sbin",
    "/private", "/opt", "/Volumes", "/Users",
    HOME,
    f"{HOME}/Library", f"{HOME}/Library/Application Support",
    f"{HOME}/Library/Containers", f"{HOME}/Library/Group Containers",
    f"{HOME}/Documents", f"{HOME}/Desktop", f"{HOME}/Downloads",
    f"{HOME}/Pictures", f"{HOME}/Movies", f"{HOME}/Music",
    f"{HOME}/.Trash", f"{HOME}/.config", f"{HOME}/.ssh",
}

# 这些目录是「容器」：只清内容、保留目录本身（清空后 App 会重建）
CONTAINER_DIRS = {
    f"{HOME}/Library/Caches",
    f"{HOME}/.cache",
    f"{HOME}/Library/Logs",
}

# 绝不删除的「身份/凭据/同步状态」类路径片段（mole 的 app_protection 思路）
# 这些一旦删掉，用户要重新登录、重新授权、重新同步，属于不可逆的数据损失。
PROTECTED_SEGMENTS = (
    "/.ssh", "/.gnupg", "/.password-store", "/.aws", "/.kube",
    "/Library/Keychains", "/Library/Mobile Documents",
    "/Library/Application Support/AddressBook",
    "/Library/Application Support/com.apple.sharedfilelist",
    "/Library/Application Support/FileProvider",
    "/Library/Application Support/CallHistoryDB",
    "/Library/Messages", "/Library/Mail", "/Library/Safari",
    "/Library/Calendars", "/Library/Reminders", "/Library/Accounts",
    "/Library/HomeKit", "/Library/IdentityServices",
    "/Library/Suggestions", "/Library/PersonalizationPortrait",
    "/Library/DoNotDisturb", "/Library/Shortcuts",
    "/Library/Containers/com.apple.mail",
    "/Library/Group Containers/group.com.apple.notes",
)

# 备份 / 安全软件 / 虚拟机的数据目录：删了等于毁掉用户资产或防护。
#
# 说明：这份清单是**本仓库自有规则**，不完全来自 mole。mole 的
# app_protection_data.sh 里只有 EDR 前缀（CrowdStrike/SentinelOne/…）
# 与 DATA_PROTECTED_BUNDLES 的 352 条 bundle id；它**没有** Arq / Time Machine
# 的路径级规则（Time Machine 靠 tmutil 行为式保护）。
# 我们比 mole 更保守：备份与虚拟机的数据目录即使规则表判成绿灯也一律拒绝，
# 因为「重新备份一次」和「重新装一个虚拟机」的代价远高于多留几百 MB。
PROTECTED_VENDOR_SEGMENTS = (
    "Time Machine", "Backblaze", "Arq", "Carbon Copy Cloner", "SuperDuper",
    "Resilio", "Sync.com", "pCloud", "Dropbox", "Google Drive", "OneDrive",
    "CrowdStrike", "SentinelOne", "Falcon", "Cylance", "Jamf", "Kandji",
    "VMware Fusion", "Parallels", "VirtualBox", "UTM", "OrbStack", "Docker",
    "com.docker.docker", "OrbStack", "colima", "lima",
)

# AI 模型 / 数据集缓存：体积巨大，看着像「垃圾」，其实是用户花几十分钟
# 重新下载的东西，有的还会因为中断而损坏（mole 明确把它们排除在自动删除外）。
# 只允许用户手动处理，绝不进自动清理。
PROTECTED_MODEL_CACHES = (
    "/.cache/huggingface", "/.cache/torch", "/.cache/tensorflow",
    "/.cache/wandb", "/.cache/modelscope", "/.lmstudio/models",
    "/.ollama/models", "/Library/Application Support/com.apple.CoreML",
    "/.cache/diffusers", "/.cache/clip", "/.cache/whisper",
    "/.cache/sentence_transformers", "/.cache/spacy", "/.cache/nltk_data",
    # mole 的 holds_compiled_model_cache：Apple Neural Engine 的**已编译**
    # 模型缓存。删了不会丢数据，但下次推理要重新编译，非常慢。
    "/com.apple.e5rt.e5bundlecache",
)

# 共享的 XDG / 用户级根目录：属于许多互不相关的工具，删掉影响面极大。
# （mole 的 _mole_is_shared_home_state_root）
# 注意：只保护**根本身**，不保护 ~/.config/zed 这类 App 专属子目录。
SHARED_STATE_ROOTS = (
    f"{HOME}/.config", f"{HOME}/.local", f"{HOME}/.local/share",
    f"{HOME}/.local/state", f"{HOME}/.cache", f"{HOME}/Library/Caches",
    f"{HOME}/Library/Application Support", f"{HOME}/Library/Logs",
)

# 用户黄灯项里的这些安全子路径，允许清理（纯缓存、着色器、崩溃日志，不含数据库与用户凭据）
#
# ⚠️ 明确**不含** `/Service Worker/ScriptCache`：Mole 在 lib/clean/user.sh 里
# 专门注释过这件事（issue #785/#964/#968）—— 即使浏览器已关闭，删掉 MV3
# 扩展的字节码会破坏 extension service worker，并在扫描时触发安全警告。
# 之前这里列了 ScriptCache，属于会造成真实损坏的规则，已移除。
YELLOW_SAFE_SUFFIXES = (
    "/Partitions", "/cache", "/Cache", "/tmp", "/temp",
    "/GPUCache", "/Code Cache", "/logs", "/Logs",
    "/Service Worker/CacheStorage",
    "/DawnCache", "/GrShaderCache", "/GraphiteDawnCache", "/ShaderCache",
    "/Crashpad", "/blob_storage",
    "/CachedData", "/CachedExtensions",
    "/component_crx_cache", "/Application Cache",
)


# ─────────────────────────────────────────── 白名单 / 日志

def load_whitelist():
    if not os.path.exists(WHITELIST_FILE):
        return []
    out = []
    for line in open(WHITELIST_FILE, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def save_whitelist(entries):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
        f.write("# macos-deep-optimize 保护名单 —— 这里的路径永不被清理\n")
        f.write("# 每行一个路径，支持 ~ 开头\n")
        for e in sorted(set(entries)):
            f.write(e + "\n")


def is_whitelisted(path, wl):
    rp = os.path.realpath(path)
    for w in wl:
        wr = os.path.realpath(os.path.expanduser(w))
        if rp == wr or rp.startswith(wr + os.sep):
            return True
    return False


def oplog(entries):
    if os.environ.get("MO_NO_OPLOG"):
        return
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(OPLOG, "a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    except OSError:
        pass


def show_history(limit=40):
    if not os.path.exists(OPLOG):
        print("还没有操作记录。")
        return
    lines = open(OPLOG, encoding="utf-8").read().strip().splitlines()
    print(f"操作日志: {OPLOG}（最近 {min(limit, len(lines))} 条 / 共 {len(lines)} 条）\n")
    for line in lines[-limit:]:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        mark = {"trash": "→废纸篓", "rm": "×删除", "skip": "跳过",
                "refuse": "拒绝", "empty": "清空内容"}.get(e.get("action"), e.get("action"))
        print(f"  {e.get('ts','')}  {mark:<8} {e.get('size',''):>9}  {e.get('path','')}")
        if e.get("reason"):
            print(f"              └ {e['reason']}")


# ─────────────────────────────────────────── 安全检查

def check(path):
    """返回 (ok, reason)。ok=False 时 reason 说明为什么拒绝。"""
    rp = os.path.realpath(os.path.expanduser(path))
    if not os.path.exists(rp):
        return False, "路径不存在"
    if rp in PROTECTED:
        return False, "这是受保护的关键路径"
    # 必须在 home 内（/private/var/folders 之类交给系统自己管）
    if not rp.startswith(HOME + os.sep):
        return False, "不在用户目录内，交给系统自己回收"

    # 容器目录（Caches / .cache / Logs）允许处理 —— 走「清空内容」模式
    if rp in CONTAINER_DIRS:
        return True, ""

    # 身份/凭据/同步状态：即使落在 Caches 里也不许整体删（可能是 App 的登录态）
    for seg in PROTECTED_SEGMENTS:
        if seg in rp:
            return False, f"属于登录凭据或同步状态（{seg}），删了要重新授权/同步"

    # 备份、安全软件、虚拟机数据：用户资产或防护，绝不作为「缓存」清理
    for vendor in PROTECTED_VENDOR_SEGMENTS:
        if vendor.lower() in rp.lower():
            return False, f"属于备份/安全/虚拟机数据（{vendor}），不作为缓存清理"

    # AI 模型与数据集缓存：重新下载代价高、中断可能损坏，只允许手动处理
    for mc in PROTECTED_MODEL_CACHES:
        if mc in rp:
            return False, "属于 AI 模型/数据集缓存，重新下载代价高，请手动确认后再处理"

    # 不允许是 HOME 的一级目录（如 ~/Documents）
    rel = rp[len(HOME) + 1:]
    if os.sep not in rel:
        return False, "这是主目录的一级目录，不允许整体删除"

    # 关键防护：非 Caches 目录严禁删除包含敏感数据库、会话凭证、密钥的路径
    if not (rp.startswith(os.path.join(HOME, "Library", "Caches")) or rp.startswith(os.path.join(HOME, ".cache"))):
        sensitive_parts = ("/Cookies", "/IndexedDB", "/Local Storage", "/Session Storage", "/Preferences", "/Keychains", "/Accounts")
        for sp in sensitive_parts:
            if sp in rp or rp.endswith(sp):
                return False, f"包含敏感用户会话或配置（{sp}），严禁删除"
        if rp.endswith((".db", ".sqlite", ".sqlite3", ".keychain", ".keychain-db")):
            return False, "属于数据库或密钥库文件，严禁直接删除"

    return True, ""


def size_of(path):
    try:
        out = subprocess.run(["du", "-sk", path], capture_output=True, text=True, timeout=120).stdout
        kb = int(out.split()[0])
    except Exception:
        return "?"
    for unit in ("KB", "MB", "GB"):
        if kb < 1024 or unit == "GB":
            return f"{kb:.0f} {unit}" if unit == "KB" else f"{kb/1024:.1f} {unit}"
        kb /= 1024
    return "?"


def to_trash(path):
    """把路径移入废纸篓（可逆）。

    两条路径：
      1. 优先走 Finder（`osascript`）—— 走系统废纸篓语义，用户能在
         废纸篓里看到、能「放回原处」；
      2. Finder 失败（没有自动化权限 / 无 GUI 会话 / 超时）时退回
         shutil.move 到 ~/.Trash —— 仍然是可逆的，只是少了「放回原处」元数据。

    关键：无论走哪条，**必须确认源路径真的没了**，否则抛异常让上层记失败。
    旧实现不看结果，Finder 静默失败时会把「没删掉」当成「已移入废纸篓」，
    正是回执超额报告的另一半原因。
    """
    script = 'tell application "Finder" to delete (POSIX file %s as alias)' % json.dumps(path)
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True,
                           text=True, timeout=120)
        ok = r.returncode == 0
    except Exception:
        ok = False
    if ok and not os.path.exists(path):
        return
    # 兜底：直接搬到 ~/.Trash（同名冲突时加时间戳）
    dest = os.path.join(HOME, ".Trash",
                        os.path.basename(path.rstrip("/")) + "." + time.strftime("%H%M%S"))
    shutil.move(path, dest)
    if os.path.exists(path):
        raise OSError("移动后源路径仍然存在")


def empty_dir(path):
    """清空目录内容，保留目录本身"""
    n = 0
    for entry in os.listdir(path):
        p = os.path.join(path, entry)
        try:
            to_trash(p)
            n += 1
        except Exception as e:
            print(f"      ⚠️ {entry}: {e}")
    return n


# 按「保留天数」清理的目录（借鉴 mole 的 retention 语义）：
# 日志/崩溃报告不是垃圾，而是**排障证据**。mole 保留 7 天，
# 我们也保留 7 天 —— 只删更旧的，既腾出空间又不至于出问题时没日志可查。
RETENTION_DAYS = 7
RETENTION_DIRS = {
    f"{HOME}/Library/Logs": RETENTION_DAYS,
    f"{HOME}/Library/Logs/DiagnosticReports": RETENTION_DAYS,
    f"{HOME}/Library/Application Support/CrashReporter": RETENTION_DAYS,
}


def empty_dir_older_than(path, days):
    """只清掉 days 天以前的条目（按 mtime），保留最近的活动。

    这是比「整个清空」更科学的做法：既释放历史占用，又不破坏
    正在写入的日志文件与刚产生的崩溃报告（那些往往正是你要看的）。
    """
    cutoff = time.time() - days * 86400
    n = 0
    for entry in os.listdir(path):
        p = os.path.join(path, entry)
        try:
            st = os.lstat(p)
            # 目录本身旧不代表里面没有新文件：目录统一按自身 mtime 判断，
            # 太新的跳过；旧目录整体移走（里面可能有大量历史小文件）。
            if st.st_mtime > cutoff:
                continue
            to_trash(p)
            n += 1
        except Exception as e:
            print(f"      ⚠️ {entry}: {e}")
    return n


# ─────────────────────────────────────────── 收集目标

def collect(scan, include_yellow=False):
    """从 scan 结果里汇总本次要处理的项。"""
    sys.path.insert(0, HERE)
    from report import build_model  # 复用分级逻辑，保证报告与执行一致

    targets = []
    tiers = build_model(scan)

    # ① 规则表判定的绿灯项（已包含纯缓存、已核实安全的构建产物、本地安装包）
    for it in tiers["green"]:
        p = it["path"]
        real = os.path.realpath(p)
        # 优先级：按天数保留 > 清空内容 > 整个删除。
        # 日志既是容器又是排障证据 —— 必须走「只删 7 天前」，
        # 不能因为它是 CONTAINER_DIRS 就被整个清空。
        if real in RETENTION_DIRS:
            mode = "retain"
        elif real in CONTAINER_DIRS:
            mode = "empty"
        else:
            mode = "remove"
        targets.append({"path": p, "name": it["name"], "size": it["size"],
                        "size_kb": it.get("size_kb", 0),
                        "tier": "green", "mode": mode,
                        "retain_days": RETENTION_DIRS.get(real, 0),
                        "why": it.get("desc", "纯缓存")})

    # ② 黄灯——只纳用户明确要求，且只碰核实过的安全子路径
    if include_yellow:
        for it in tiers["yellow"]:
            if any(it["path"].endswith(s) for s in YELLOW_SAFE_SUFFIXES):
                targets.append({"path": it["path"], "name": it["name"], "size": it["size"],
                                "size_kb": it.get("size_kb", 0),
                                "tier": "yellow", "mode": "remove",
                                "why": "黄灯项中已核实的纯缓存子目录"})

    # ③ 去重 —— 同一条路径可能同时出现在多个分类中
    uniq, seen_paths = [], set()
    for t in targets:
        key = os.path.realpath(os.path.expanduser(t["path"]))
        if key in seen_paths:
            continue
        seen_paths.add(key)
        uniq.append(t)
    return uniq


# ─────────────────────────────────────────── main

def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("scan_json", nargs="?")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认为预览）")
    ap.add_argument("--rm", action="store_true", help="直接删除而非移废纸篓（不可逆）")
    ap.add_argument("--include-yellow", action="store_true", help="把黄灯安全子路径也纳入")
    ap.add_argument("--select-file", help="只处理该 JSON 数组里列出的路径（逐项勾选用；只做交集，不会新增）")
    ap.add_argument("--whitelist", nargs="+", help="list / add <path> / remove <path>")
    ap.add_argument("--history", action="store_true", help="查看操作日志")
    ap.add_argument("-h", "--help", action="store_true")
    args = ap.parse_args()

    if args.help or (not args.scan_json and not args.whitelist and not args.history):
        print(__doc__)
        return

    # ── 白名单管理
    if args.whitelist:
        op = args.whitelist[0]
        wl = load_whitelist()
        if op == "list":
            if not wl:
                print("保护名单为空。用法: --whitelist add <路径>")
            else:
                print(f"保护名单（{WHITELIST_FILE}）：")
                for w in wl:
                    print(f"  {w}")
            return
        if op in ("add", "remove") and len(args.whitelist) >= 2:
            p = args.whitelist[1]
            if op == "add":
                if p not in wl:
                    wl.append(p)
                save_whitelist(wl)
                print(f"✓ 已加入保护: {p}")
            else:
                wl = [w for w in wl if w != p]
                save_whitelist(wl)
                print(f"✓ 已移出保护: {p}")
            return
        print("用法: --whitelist list | add <path> | remove <path>")
        return

    if args.history:
        show_history()
        return

    # ── 清理
    scan = json.load(open(args.scan_json, encoding="utf-8"))
    targets = collect(scan, args.include_yellow)

    # 逐项勾选：只做「交集」——选择文件只能把范围收窄，绝不能扩大（安全模型不被绕过）
    if args.select_file:
        try:
            with open(args.select_file, encoding="utf-8") as f:
                selected = {os.path.realpath(os.path.expanduser(str(p))) for p in json.load(f)}
            before = len(targets)
            targets = [t for t in targets
                       if os.path.realpath(os.path.expanduser(t["path"])) in selected]
            print(f"逐项选择：{before} 项中选中 {len(targets)} 项（按 --select-file）\n")
        except (OSError, json.JSONDecodeError, TypeError) as e:
            raise SystemExit(f"读取 --select-file 失败: {e}")
    wl = load_whitelist()

    todo, skipped = [], []
    for t in targets:
        ok, reason = check(t["path"])
        if not ok:
            skipped.append({**t, "reason": reason})
            continue
        if is_whitelisted(t["path"], wl):
            skipped.append({**t, "reason": "在保护名单里（whitelist）"})
            continue
        todo.append(t)

    mode_label = "直接删除（不可逆）" if args.rm else "移到废纸篓（可逆）"
    print(f"{'【预览】' if not args.apply else '【执行】'}模式: {mode_label}")
    print(f"待处理 {len(todo)} 项 | 跳过 {len(skipped)} 项\n")

    if todo:
        print("将要处理：")
        for t in todo:
            m = {"empty": "清空内容",
                 "retain": f"只删 {t.get('retain_days') or RETENTION_DAYS} 天前",
                 "remove": "整个删除"}.get(t["mode"], "整个删除")
            print(f"  {t['size']:>9}  [{m}] {t['name'][:44]}")
            print(f"              {t['path'].replace(HOME,'~')}")
            print(f"              └ {t['why']}")
        print()

    if skipped:
        print("跳过（附原因）：")
        for s in skipped[:15]:
            print(f"  {s['size']:>9}  {s['name'][:40]:<42} {s['reason']}")
        if len(skipped) > 15:
            print(f"  … 共 {len(skipped)} 项跳过")
        print()

    if not args.apply:
        # 预览也要给机器可读回执 —— 否则调用方只能靠退出码猜，
        # 会把「预览成功」误读成「已经删了 N 项」（历史上就是这样误导 UI 的）。
        # 预览的 done 恒为 0：**什么都没删**，这是安全模型的核心承诺。
        preview = {
            "ok": len(todo) > 0,
            "mode": "preview",
            "requested": len(targets),
            "would_do": len(todo),
            "done": 0,
            "failed": 0,
            "skipped": len(skipped),
            "done_paths": [],
            "failed_items": [],
            "skipped_items": [{"path": s["path"], "reason": s["reason"]} for s in skipped],
            "would_paths": [t["path"] for t in todo],
            "freed_kb": 0,
        }
        print("\n__RECEIPT__ " + json.dumps(preview, ensure_ascii=False))
        print("这只是预览。确认无误后加 --apply 执行：")
        print(f"  python3 {os.path.basename(__file__)} {args.scan_json} --apply")
        return

    # ── 执行
    entries, done, failed, freed_kb = [], 0, 0, 0.0
    done_paths, failed_items = [], []
    for t in todo:
        p = os.path.realpath(os.path.expanduser(t["path"]))
        before = size_of(p)
        try:
            if t["mode"] == "empty":
                n = empty_dir(p)
                action = "empty"
                print(f"  ✓ 清空 {p.replace(HOME,'~')}（{n} 个子项）")
            elif t["mode"] == "retain":
                days = t.get("retain_days") or RETENTION_DAYS
                n = empty_dir_older_than(p, days)
                action = "retain"
                print(f"  ✓ 清理 {p.replace(HOME,'~')} 中 {days} 天以前的条目（{n} 个）")
            elif args.rm:
                shutil.rmtree(p, ignore_errors=False)
                action = "rm"
                print(f"  ✓ 删除 {p.replace(HOME,'~')}")
            else:
                to_trash(p)
                action = "trash"
                print(f"  ✓ 移入废纸篓 {p.replace(HOME,'~')}")
            done += 1
            done_paths.append(p)
            freed_kb += t.get("size_kb") or 0
            entries.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action,
                            "path": p, "size": before, "why": t["why"]})
        except Exception as e:
            failed += 1
            failed_items.append({"path": p, "reason": str(e)})
            print(f"  ✗ 失败 {p.replace(HOME,'~')}: {e}")
            entries.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": "refuse",
                            "path": p, "size": before, "reason": str(e)})
    for s in skipped:
        entries.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": "skip",
                        "path": s["path"], "size": s.get("size", ""), "reason": s["reason"]})
    oplog(entries)

    # 机器可读回执：界面据此判断「到底动了几项」，不能只看退出码
    # （历史 bug：全部跳过也 exit 0，UI 于是把请求项数当成完成数，回执超额报告）
    receipt = {
        "ok": done > 0 and failed == 0,
        "requested": len(targets),
        "done": done,
        "failed": failed,
        "skipped": len(skipped),
        "done_paths": done_paths,
        "failed_items": failed_items,
        "skipped_items": [{"path": s["path"], "reason": s["reason"]} for s in skipped],
        "freed_kb": round(freed_kb, 1),
    }
    print("\n__RECEIPT__ " + json.dumps(receipt, ensure_ascii=False))

    print(f"\n完成 {done} 项" + (f"、失败 {failed} 项" if failed else "")
          + (f"、跳过 {len(skipped)} 项" if skipped else "") + f"。日志: {OPLOG}")
    if not args.rm and done:
        print("⚠️ 移入废纸篓还没真正释放空间 —— 核对无误后再清空废纸篓。")
    if done == 0:
        print("⚠️ 本次没有处理任何一项（全部被跳过或失败），磁盘没有发生变化。")
        # 全跳过 = 什么都没做，用非零退出码让调用方别把它当成成功
        raise SystemExit(2)


if __name__ == "__main__":
    main()
