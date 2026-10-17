#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mac 清理助手 · 本地服务

双击 .app 后由 launch 启动本服务，然后自动打开浏览器操作。
真正的扫描/分级/清理逻辑全部来自 macos-deep-optimize 技能（scan.py / report.py / clean.py），
本文件只做「HTTP 外壳 + 状态管理」，不重复实现任何清理规则。

设计原则：
  · 只读扫描 → 用户确认 → 才清理（清理默认是预览，必须显式 apply）
  · 数据不出本机：只监听 127.0.0.1，无任何外网上传
  · 依赖技能脚本，不复制逻辑；找不到技能目录时回退到自带的副本 skill-scripts/
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HOME, ".local", "state", "mac-cleanup-app")
LOG_FILE = os.path.join(STATE_DIR, "server.log")
SCAN_JSON = os.path.join(STATE_DIR, "scan.json")
REPORT_HTML = os.path.join(STATE_DIR, "report.html")
JUDGED_JSON = os.path.join(STATE_DIR, "judged.json")
SELECT_JSON = os.path.join(STATE_DIR, "select.json")
TYPESAFE_KEY = os.path.join(HOME, ".config", "typesafe", "key")
OPS_LOG = os.path.join(HOME, ".local", "state", "macos-deep-optimize", "operations.log")

SKILL_CANDIDATES = [
    os.environ.get("MAC_CLEAN_SKILL", ""),
    os.path.join(HOME, ".hermes", "skills", "devops", "macos-deep-optimize", "scripts"),
    os.path.join(HERE, "skill-scripts"),
]


def log(msg: str) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def find_skill_dir() -> str:
    for cand in SKILL_CANDIDATES:
        if cand and os.path.isfile(os.path.join(cand, "scan.py")):
            return cand
    raise RuntimeError("找不到 macos-deep-optimize 技能脚本目录")


SKILL_DIR = find_skill_dir()


def run_script(name: str, args: list, timeout: int = 900) -> tuple:
    """跑技能脚本，返回 (returncode, stdout+stderr)。"""
    cmd = [sys.executable, os.path.join(SKILL_DIR, name)] + args
    log("run: " + " ".join(cmd))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "超时（%ss）：%s" % (timeout, name)


def disk_stats() -> dict:
    """磁盘数字：优先 /System/Volumes/Data（用户数据卷），失败退回 /。"""
    for target in ("/System/Volumes/Data", "/"):
        try:
            p = subprocess.run(["df", "-k", target], capture_output=True, text=True, timeout=15)
            lines = p.stdout.strip().splitlines()
            if len(lines) < 2:
                continue
            f = lines[-1].split()
            total, used, avail = int(f[1]) / 1048576.0, int(f[2]) / 1048576.0, int(f[3]) / 1048576.0
            return {"total_gb": round(total, 1), "used_gb": round(used, 1), "free_gb": round(avail, 1),
                    "pct": round(used / total * 100, 1) if total else 0.0, "mount": target}
        except Exception as exc:                                     # noqa: BLE001
            log("disk_stats failed on %s: %s" % (target, exc))
    return {}


def human(kb: float) -> str:
    for unit, div in (("TB", 1024.0 ** 3), ("GB", 1024.0 ** 2), ("MB", 1024.0), ("KB", 1.0)):
        if kb >= div or unit == "KB":
            return "%.1f %s" % (kb / div, unit) if unit != "KB" else "%d KB" % kb
    return "%d KB" % kb


def tiers_summary() -> dict:
    """用技能自带的 report.build_model() 算三色汇总（不重新实现规则）。"""
    try:
        if SKILL_DIR not in sys.path:
            sys.path.insert(0, SKILL_DIR)
        import importlib
        report = importlib.import_module("report")
        with open(SCAN_JSON, encoding="utf-8") as f:
            scan = json.load(f)
        tiers = report.build_model(scan)
        out = {"green": {"kb": 0.0, "items": []}, "yellow": {"kb": 0.0, "items": []},
               "red": {"kb": 0.0, "items": []}}
        for name, rows in tiers.items():
            if name not in out:
                continue
            out[name]["kb"] = round(sum(r.get("size_kb", 0) for r in rows) / 1024.0, 1)  # MB
            for r in sorted(rows, key=lambda x: -x.get("size_kb", 0))[:8]:
                out[name]["items"].append({"name": r.get("name", ""), "path": r.get("path", ""),
                                           "size": r.get("size", ""),
                                           "note": r.get("why") or r.get("risk") or r.get("desc") or ""})
        for name in out:
            out[name]["mb"] = out[name].pop("kb")
        return out
    except Exception as exc:                                          # noqa: BLE001
        log("tiers_summary failed: %s" % exc)
        return {}


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.phase = "idle"           # idle | scanning | scanning_done | cleaning
        self.started_at = 0.0
        self.scan_at = ""
        self.scan_seconds = None
        self.error = ""
        self.notice = ""
        self.scan_gb_before = None
        self.scan_gb_after = None

    def snapshot(self) -> dict:
        with self.lock:
            d = {"phase": self.phase, "elapsed": round(time.time() - self.started_at, 1) if self.started_at else 0,
                 "scan_at": self.scan_at, "scan_seconds": self.scan_seconds, "error": self.error,
                 "notice": self.notice, "has_scan": os.path.exists(SCAN_JSON),
                 "has_report": os.path.exists(REPORT_HTML), "skill_dir": SKILL_DIR}
        d["disk"] = disk_stats()
        if d["has_scan"]:
            d["tiers"] = tiers_summary()
            d["items"] = build_items()
            d["judged"] = judged_summary()
        return d


ST = State()


def do_scan() -> None:
    with ST.lock:
        if ST.phase in ("scanning", "cleaning"):
            return
        ST.phase = "scanning"
        ST.started_at = time.time()
        ST.error = ""
        ST.notice = "正在只读扫描（约 2-3 分钟）…"
    rc, out = run_script("scan.py", [], timeout=1200)
    with ST.lock:
        if rc != 0 or not out.strip().startswith("{"):
            ST.phase = "idle"
            ST.error = "扫描失败：%s" % (out.strip()[:800] or "无输出")
            ST.notice = ""
            log("scan failed rc=%s" % rc)
            return
        try:
            with open(SCAN_JSON, "w", encoding="utf-8") as f:
                f.write(out)
            data = json.loads(out)
            ST.scan_seconds = data.get("scan_seconds")
            ST.scan_at = data.get("generated_at", "")
        except Exception as exc:                                      # noqa: BLE001
            ST.phase = "idle"
            ST.error = "扫描结果解析失败：%s" % exc
            ST.notice = ""
            return
        ST.phase = "scanning_done"
        ST.notice = "扫描完成，可查看报告或预览清理"
        ST.started_at = 0.0
    log("scan ok")


def make_report(use_judge: bool = False) -> str:
    args = [SCAN_JSON, REPORT_HTML]
    if use_judge and os.path.exists(JUDGED_JSON):
        args.append("--judge=" + JUDGED_JSON)
    rc, out = run_script("report.py", args, timeout=180)
    if rc != 0 or not os.path.exists(REPORT_HTML):
        raise RuntimeError("报告生成失败：%s" % (out.strip()[:500] or "无输出"))
    return "/report.html"


def safe_suffixes() -> tuple:
    """clean.py 里认的「黄灯中已核实的纯缓存子路径」后缀——勾选范围以它为准。"""
    try:
        if SKILL_DIR not in sys.path:
            sys.path.insert(0, SKILL_DIR)
        import importlib
        clean_mod = importlib.import_module("clean")
        return tuple(getattr(clean_mod, "YELLOW_SAFE_SUFFIXES", ()))
    except Exception as exc:                                          # noqa: BLE001
        log("safe_suffixes failed: %s" % exc)
        return ()


def build_items() -> list:
    """体检清单：绿灯 + 黄灯，带「能不能勾选」和 Jev 的 AI 判断（如果有）。"""
    if not os.path.exists(SCAN_JSON):
        return []
    try:
        if SKILL_DIR not in sys.path:
            sys.path.insert(0, SKILL_DIR)
        import importlib
        report = importlib.import_module("report")
        with open(SCAN_JSON, encoding="utf-8") as f:
            scan = json.load(f)
        tiers = report.build_model(scan)
    except Exception as exc:                                          # noqa: BLE001
        log("build_items failed: %s" % exc)
        return []

    judged = {}
    if os.path.exists(JUDGED_JSON):
        try:
            with open(JUDGED_JSON, encoding="utf-8") as f:
                for row in json.load(f).get("judged", []):
                    judged[row.get("path", "")] = row
        except (OSError, json.JSONDecodeError) as exc:
            log("read judged failed: %s" % exc)

    suffixes = safe_suffixes()
    out = []
    for tier in ("green", "yellow"):
        for r in tiers.get(tier, []):
            p = r.get("path", "")
            selectable = tier == "green" or (suffixes and p.endswith(suffixes))
            note_parts = [r.get("desc", "")]
            if r.get("risk"):
                note_parts.append("风险：" + str(r["risk"]))
            if r.get("disposal"):
                note_parts.append("处置：" + str(r["disposal"]))
            out.append({
                "path": p, "name": r.get("name", ""), "size": r.get("size", ""),
                "size_kb": r.get("size_kb", 0), "tier": tier,
                "selectable": bool(selectable),
                "note": " ｜ ".join([x for x in note_parts if x]).strip(),
                "cmd": r.get("cmd", ""), "ai": judged.get(p),
            })
    out.sort(key=lambda x: (0 if x["tier"] == "green" else 1, -x.get("size_kb", 0)))

    # 规则表认不出的目录（info）——不参与清理，但把 AI 判断摆出来（这正是 Jev 的价值）
    try:
        kids = list(scan.get("children", []))
        covered = {c.get("parent") for c in kids}
        for _grp, lst in (scan.get("dir_groups") or {}).items():
            for it in lst:
                if it.get("path") not in covered and it.get("size_kb", 0) > 0:
                    kids.append(it)
    except Exception:                                                 # noqa: BLE001
        kids = []
    known = {i["path"] for i in out}
    unknown = []
    for it in sorted(kids, key=lambda x: -x.get("size_kb", 0)):
        p = it.get("path", "")
        if not p or p in known:
            continue
        try:
            tier, _rule = report.classify(p, it.get("size_kb", 0))
        except Exception:                                             # noqa: BLE001
            continue
        if tier != "info":
            continue
        ai = judged.get(p)
        unknown.append({
            "path": p, "name": it.get("name", p.split("/")[-1]), "size": it.get("size_h", ""),
            "size_kb": it.get("size_kb", 0), "tier": "unknown", "selectable": False,
            "note": ("AI 判断：" + str(ai.get("advice", ""))) if ai
                    else "规则表认不出这个目录 —— 点下面的「运行 AI 判断」让 Jev 看一眼",
            "cmd": "", "ai": ai,
        })
    listed_unknown = [u for u in unknown if u["ai"]] + [u for u in unknown if not u["ai"]][:8]
    return out + listed_unknown


def judged_summary() -> dict:
    if not os.path.exists(JUDGED_JSON):
        return {}
    try:
        with open(JUDGED_JSON, encoding="utf-8") as f:
            d = json.load(f)
        rows = d.get("judged", [])
        by = {}
        for r in rows:
            by[r.get("tier", "?")] = by.get(r.get("tier", "?"), 0) + 1
        return {"judged_at": d.get("judged_at", ""), "model": d.get("model", ""),
                "count": d.get("count", len(rows)), "by_tier": by}
    except (OSError, json.JSONDecodeError):
        return {}


def clean_output() -> str:
    if not os.path.exists(OPS_LOG):
        return "还没有操作记录。"
    try:
        with open(OPS_LOG, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "日志: %s\n\n%s" % (OPS_LOG, "".join(lines[-60:]))
    except OSError as exc:
        return "读取日志失败: %s" % exc


class Handler(BaseHTTPRequestHandler):
    server_version = "MacCleanupApp/1.0"

    def log_message(self, fmt, *args):                                # 静默，只写自己的日志
        pass

    # ---------- helpers ----------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _text(self, text: str, code: int = 200) -> None:
        self._send(code, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:                                             # noqa: BLE001
            return {}

    # ---------- routes ----------
    def do_GET(self):                                                 # noqa: N802
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as exc:
                return self._text("UI missing: %s" % exc, 500)
        if path == "/report.html":
            if not os.path.exists(REPORT_HTML):
                return self._text("还没有报告，先跑一次扫描。", 404)
            with open(REPORT_HTML, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._json(ST.snapshot())
        if path == "/api/history":
            return self._json({"output": clean_output()})
        if path == "/api/whitelist":
            rc, out = run_script("clean.py", ["--whitelist", "list"], timeout=60)
            return self._json({"output": out})
        if path == "/api/items":
            return self._json({"items": build_items(), "judged": judged_summary()})
        if path == "/api/typesafe":
            configured = bool(os.environ.get("TYPESAFE_API_KEY") or
                              (os.path.exists(TYPESAFE_KEY) and open(TYPESAFE_KEY, encoding="utf-8").read().strip()))
            return self._json({"configured": configured,
                               "source": "env" if os.environ.get("TYPESAFE_API_KEY") else ("file" if configured else "none"),
                               "path": TYPESAFE_KEY})
        if path == "/api/quit":
            self._json({"ok": True})
            self.wfile.flush()
            threading.Timer(0.8, lambda: os._exit(0)).start()
            return
        return self._text("not found", 404)

    def do_POST(self):                                                # noqa: N802
        path = urlparse(self.path).path
        body = self._body()

        if path == "/api/scan":
            threading.Thread(target=do_scan, daemon=True).start()
            return self._json({"ok": True, "phase": "scanning"})

        if path == "/api/report":
            if not os.path.exists(SCAN_JSON):
                return self._json({"ok": False, "error": "先跑扫描"}, 400)
            try:
                url = make_report()
                return self._json({"ok": True, "url": url})
            except Exception as exc:                                  # noqa: BLE001
                return self._json({"ok": False, "error": str(exc)}, 500)

        if path == "/api/clean":
            mode = body.get("mode", "preview")
            if mode not in ("preview", "apply"):
                return self._json({"ok": False, "error": "mode 只能是 preview/apply"}, 400)
            if not os.path.exists(SCAN_JSON):
                return self._json({"ok": False, "error": "先跑扫描"}, 400)
            args = [SCAN_JSON]
            if mode == "apply":
                if body.get("confirm") != "确认清理":
                    return self._json({"ok": False, "error": "需要确认文本"}, 400)
                args.append("--apply")
                if body.get("rm"):
                    args.append("--rm")
            # 逐项勾选：把选中的路径写成选择文件，clean.py 只做交集（不会扩大范围）
            paths = body.get("paths")
            if paths is not None:
                if not isinstance(paths, list) or not paths:
                    return self._json({"ok": False, "error": "没有勾选任何项"}, 400)
                try:
                    with open(SELECT_JSON, "w", encoding="utf-8") as f:
                        json.dump(paths, f, ensure_ascii=False)
                except OSError as exc:
                    return self._json({"ok": False, "error": "写入选择文件失败：%s" % exc}, 500)
                args += ["--select-file", SELECT_JSON, "--include-yellow"]
            elif body.get("include_yellow"):
                args.append("--include-yellow")
            before = disk_stats().get("free_gb")
            with ST.lock:
                ST.phase = "cleaning"
                ST.started_at = time.time()
            rc, out = run_script("clean.py", args, timeout=900)
            with ST.lock:
                ST.phase = "scanning_done"
                ST.started_at = 0.0
            after = disk_stats().get("free_gb")
            return self._json({"ok": rc == 0, "mode": mode, "output": out,
                               "selected": len(paths) if isinstance(paths, list) else None,
                               "free_before_gb": before, "free_after_gb": after})

        if path == "/api/judge":
            if not os.path.exists(SCAN_JSON):
                return self._json({"ok": False, "error": "先跑扫描"}, 400)
            if not (os.environ.get("TYPESAFE_API_KEY") or os.path.exists(TYPESAFE_KEY)):
                return self._json({"ok": False, "error": "未配置 TypeSafe/Jev 的 key"}, 400)
            args = [SCAN_JSON, "--out", JUDGED_JSON]
            if body.get("top"):
                args += ["--top", str(int(body["top"]))]
            if body.get("batch"):
                args += ["--batch", str(int(body["batch"]))]
            if body.get("only_unknown", True):
                args.append("--only-unknown")
            with ST.lock:
                ST.phase = "judging"
                ST.started_at = time.time()
            rc, out = run_script("judge.py", args, timeout=900)
            with ST.lock:
                ST.phase = "scanning_done"
                ST.started_at = 0.0
            if rc != 0:
                return self._json({"ok": False, "error": "Jev 判断失败", "output": out[-1500:]}, 500)
            try:
                make_report(use_judge=True)          # 重生成报告，把 AI 判断并进去
            except Exception as exc:                                  # noqa: BLE001
                log("report with judge failed: %s" % exc)
            return self._json({"ok": True, "output": out[-1500:], "judged": judged_summary()})

        if path == "/api/typesafe":
            key = (body.get("key") or "").strip()
            if not key:
                return self._json({"ok": False, "error": "key 不能为空"}, 400)
            if "\n" in key or len(key) > 300:
                return self._json({"ok": False, "error": "key 格式不对"}, 400)
            try:
                os.makedirs(os.path.dirname(TYPESAFE_KEY), exist_ok=True)
                with open(TYPESAFE_KEY, "w", encoding="utf-8") as f:
                    f.write(key + "\n")
                os.chmod(TYPESAFE_KEY, 0o600)
                log("typesafe key saved to %s" % TYPESAFE_KEY)   # 只记路径，不记内容
                return self._json({"ok": True, "configured": True, "path": TYPESAFE_KEY})
            except OSError as exc:
                return self._json({"ok": False, "error": "写入失败：%s" % exc}, 500)

        if path == "/api/whitelist":
            action = body.get("action")
            if action == "list":
                rc, out = run_script("clean.py", ["--whitelist", "list"], timeout=60)
                return self._json({"ok": True, "output": out})
            if action in ("add", "remove") and body.get("path"):
                rc, out = run_script("clean.py", ["--whitelist", action, body["path"]], timeout=60)
                return self._json({"ok": rc == 0, "output": out})
            return self._json({"ok": False, "error": "action 需要 list/add/remove 且带 path"}, 400)

        if path == "/api/trash":
            if body.get("confirm") != "清空废纸篓":
                return self._json({"ok": False, "error": "需要确认文本"}, 400)
            try:
                p = subprocess.run(["osascript", "-e", 'tell application "Finder" to empty trash'],
                                   capture_output=True, text=True, timeout=300)
                return self._json({"ok": p.returncode == 0, "output": (p.stdout or "") + (p.stderr or ""),
                                   "free_gb": disk_stats().get("free_gb")})
            except Exception as exc:                                  # noqa: BLE001
                return self._json({"ok": False, "error": str(exc)}, 500)

        return self._text("not found", 404)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    port = int(os.environ.get("MAC_CLEAN_PORT") or 0) or free_port()
    if os.path.exists(SCAN_JSON):
        try:
            with open(SCAN_JSON, encoding="utf-8") as f:
                ST.scan_at = json.load(f).get("generated_at", "")
                ST.phase = "scanning_done"
        except Exception:                                             # noqa: BLE001
            pass
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    with open(os.path.join(STATE_DIR, "port"), "w", encoding="utf-8") as f:
        f.write(str(port))
    log("serving on %s (skill=%s python=%s)" % (url, SKILL_DIR, sys.executable))
    print(url, flush=True)
    if "--open" in sys.argv or os.environ.get("MAC_CLEAN_OPEN", "1") == "1":
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
