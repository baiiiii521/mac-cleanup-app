#!/usr/bin/env python3
"""
用 TypeSafe / Jev 判断目录该不该清理。

TypeSafe 的 Jev 是一个「可编程判断」模型：给它状态，它返回带概率的**类型化
判断**（不是生成文本）。这里用它补上规则表的盲区 —— 规则表只认得写死的模式，
遇到未知 App 的数据目录、UUID 命名的容器就无能为力，而 Jev 可以看着目录名、
大小、子目录结构做出判断。

用法:
    python3 judge.py scan.json > judged.json
    python3 judge.py scan.json --top 20 --batch 5
    python3 judge.py scan.json --only-unknown     # 只判断规则表识别不了的

前提:
    API key 放在 ~/.config/typesafe/key，或设置环境变量 TYPESAFE_API_KEY

成本控制:
    每个目录问 3 个问题（类别 / 能否删 / 可恢复性），5 个目录一批，
    一次请求 = 15 个问题。--top N 限制判断范围。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
HOME = os.path.expanduser("~")

# 与 report.py 保持一致的「规则表能识别」的前缀 —— --only-unknown 时跳过这些
KNOWN_FRAGMENTS = [
    "/Library/Caches", "/.cache", "/.npm", "/.cargo", "/.gradle", "/.m2", "/.bun",
    "/Library/Developer", "/Library/Logs", "/Library/Metadata", "/.Trash",
    "com.tencent.xinWeChat", "com.bytedance.macos.feishu", "ZaloData",
    "wpsoffice", "com.apple.wallpaper", "Photos Library", "Mobile Documents",
    "/Library/Containers", "/Library/Application Support", "Group Containers",
]

CATEGORIES = {
    "regenerable_cache": "纯缓存或临时文件，App 会自动重建，删了不丢东西",
    "app_user_content": "App 数据，内含用户真实内容（聊天记录、媒体、文档）",
    "app_runtime": "App 运行必需的核心数据（数据库、索引、虚拟机镜像）",
    "user_documents": "用户自己的文件（照片、文稿、下载），不专属某个 App",
    "system_managed": "由 macOS 自己管理，会自行重建",
    "unknown": "信息不足，无法判断",
}

RECOVER_LEVELS = [
    "删了就没了 —— 这是唯一副本",
    "只有云端或其他设备上还有时才救得回来",
    "可以重新下载或重新同步（慢但可行）",
    "App 或系统会自动重建",
]


def load_key():
    k = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if k:
        return k
    p = os.path.expanduser("~/.config/typesafe/key")
    if os.path.exists(p):
        return open(p).read().strip()
    return None


def sub_names(path, limit=12):
    try:
        items = []
        for n in sorted(os.listdir(path)):
            if n.startswith("."):
                continue
            full = os.path.join(path, n)
            items.append(n + ("/" if os.path.isdir(full) else ""))
            if len(items) >= limit:
                break
        return items
    except OSError:
        return []


def dir_mtime(path):
    try:
        return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
    except OSError:
        return "unknown"


def app_installed(app_name):
    if not app_name:
        return None
    for root in ("/Applications", os.path.expanduser("~/Applications")):
        if os.path.exists(os.path.join(root, app_name + ".app")):
            return True
    return False


def is_running(pattern):
    try:
        out = subprocess.run(["pgrep", "-ifl", pattern], capture_output=True,
                             text=True, timeout=10).stdout
        return bool(out.strip())
    except Exception:
        return None


def build_request(dirs):
    """把 N 个目录塞进一个 state，每个目录问 3 个问题（fan-out）。"""
    state = {"directories": []}
    questions = {}
    for i, d in enumerate(dirs):
        state["directories"].append({
            "path": d["path"].replace(HOME, "~"),
            "size": d["size_h"],
            "last_modified": dir_mtime(d["path"]),
            "subdirectories": sub_names(d["path"]),
        })
        ref = f"`directories[{i}]`"
        questions[f"d{i}_category"] = {
            "type": "choice",
            "instructions": f"What kind of data is stored in {ref}? Judge from the path, size, and the subdirectory names.",
            "criteria": CATEGORIES,
        }
        questions[f"d{i}_safe"] = {
            "type": "noul",
            "instructions": f"Can {ref} be deleted right now without losing user data and without breaking an installed application?",
            "criteria": {
                "true": "Nothing irreplaceable is lost — either it is regenerable, or the app is not needed",
                "false": "Real user content would be lost, or an installed app would break or need a full re-sync",
            },
        }
        questions[f"d{i}_recover"] = {
            "type": "score",
            "instructions": f"If {ref} were deleted, how recoverable is its content?",
            "criteria": RECOVER_LEVELS,
        }
    return {"state": state, "model": MODEL, "questions": questions}


def call_api(key, payload, retries=3):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(retries):
        req = urllib.request.Request(
            API_URL, data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 529) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise SystemExit(f"API 错误 {e.code}: {detail}")
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise SystemExit(f"网络错误: {e}")
    return {}


def parse_answers(dirs, resp):
    answers = resp.get("answers", {})
    out = []
    for i, d in enumerate(dirs):
        cat = answers.get(f"d{i}_category", {})
        safe = answers.get(f"d{i}_safe", {})
        rec = answers.get(f"d{i}_recover", {})
        choice = cat.get("choice", "unknown")
        conf = cat.get("confidence", 0)
        p_safe = safe.get("noul", None)
        out.append({
            "path": d["path"],
            "size": d["size_h"],
            "category": choice,
            "category_label": CATEGORIES.get(choice, choice),
            "category_confidence": round(conf, 3) if isinstance(conf, (int, float)) else None,
            "safe_to_delete": round(p_safe, 3) if isinstance(p_safe, (int, float)) else None,
            "recoverability": round(rec.get("score", -1), 2) if "score" in rec else None,
            "probabilities": cat.get("probabilities", {}),
        })
    return out


def verdict(row):
    """把 Jev 的输出翻译成一句可执行建议。"""
    cat, safe = row["category"], row["safe_to_delete"]
    if safe is None:
        return "gray", "Jev 未返回该问题的答案"
    if cat == "regenerable_cache" and safe >= 0.7:
        return "green", "纯缓存，可清（删了会自动重建）"
    if cat == "system_managed":
        return "gray", "系统托管，交给系统自己回收"
    if safe <= 0.15:
        return "red", "明确不该删（会丢数据或弄坏应用）"
    if safe <= 0.45:
        return "red", "倾向保留，风险大于收益"
    if safe >= 0.75:
        return "yellow", "可能可删，但建议先去对应 App 里确认"
    return "yellow", "拿不准 —— 交给用户判断"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scan_json")
    ap.add_argument("--top", type=int, default=15, help="判断前 N 大的目录（默认 15）")
    ap.add_argument("--batch", type=int, default=5, help="每批几个目录（默认 5）")
    ap.add_argument("--only-unknown", action="store_true",
                    help="只判断规则表识别不了的目录")
    ap.add_argument("--out", default="-", help="输出文件，默认 stdout")
    args = ap.parse_args()

    key = load_key()
    if not key:
        raise SystemExit("找不到 API key —— 放到 ~/.config/typesafe/key 或设 TYPESAFE_API_KEY")

    scan = json.load(open(args.scan_json, encoding="utf-8"))

    # 收集候选目录：下钻结果 + 顶层目录，按大小降序
    cands, seen = [], set()
    for it in scan.get("children", []):
        if it["path"] not in seen:
            seen.add(it["path"]); cands.append(it)
    for grp, lst in scan.get("dir_groups", {}).items():
        for it in lst:
            if it["path"] not in seen and it["size_kb"] > 0:
                seen.add(it["path"]); cands.append(it)
    cands.sort(key=lambda x: -x["size_kb"])

    if args.only_unknown:
        cands = [c for c in cands
                 if not any(f in c["path"] for f in KNOWN_FRAGMENTS)]
    cands = cands[:args.top]

    if not cands:
        print(json.dumps({"judged": [], "note": "没有需要判断的目录"},
                         ensure_ascii=False, indent=1))
        return

    results = []
    for i in range(0, len(cands), args.batch):
        batch = cands[i:i + args.batch]
        sys.stderr.write(f"  判断第 {i//args.batch + 1} 批（{len(batch)} 个目录）…\n")
        resp = call_api(key, build_request(batch))
        for row in parse_answers(batch, resp):
            tier, advice = verdict(row)
            row["tier"] = tier
            row["advice"] = advice
            results.append(row)

    payload = {
        "model": MODEL,
        "judged_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(results),
        "judged": results,
    }
    out = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.out == "-":
        print(out)
    else:
        open(args.out, "w", encoding="utf-8").write(out)
        sys.stderr.write(f"已写入 {args.out}（{len(results)} 项）\n")


if __name__ == "__main__":
    main()
