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


def make_report() -> str:
    rc, out = run_script("report.py", [SCAN_JSON, REPORT_HTML], timeout=180)
    if rc != 0 or not os.path.exists(REPORT_HTML):
        raise RuntimeError("报告生成失败：%s" % (out.strip()[:500] or "无输出"))
    return "/report.html"


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
            if body.get("include_yellow"):
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
                               "free_before_gb": before, "free_after_gb": after})

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
