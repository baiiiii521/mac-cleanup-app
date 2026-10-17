#!/bin/bash
# 截「Mac 清理助手」的窗口（被别的窗口挡住也能截到内容）
#
#   bash tools/capture-app-window.sh [输出路径] [窗口名关键词]
#
# 两个必须知道的坑（都实测过）：
#   1) WKWebView 会额外造几个小的辅助窗口（也挂同一个 owner 名），所以要取「面积最大的那个主窗口」；
#   2) App 不在前台时，WindowServer 可能只保留一份缩小快照，窗口列表会报出很小的尺寸（例如 158x155）。
#      所以先把 App 唤到前台、等它报出正常尺寸，然后才截。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/.impeccable/review/appwindow.png}"
KEY="${2:-Mac 清理助手}"

pick() {   # 输出：面积 窗口id 宽 高
  (cd "$ROOT" && swift tools/winlist.swift "$KEY" 2>/dev/null) | grep -v '^——' \
    | sed -n 's/^id=\([0-9]*\).* \([0-9]*\)x\([0-9]*\)$/\1 \2 \3/p' \
    | awk '{print $2*$3, $1, $2, $3}' | sort -rn | head -1
}

# 1) 尽量把 App 唤到前台（App 名带空格时 open -a 最稳）
[ -d "$HOME/Applications/$KEY.app" ] && { open -a "$HOME/Applications/$KEY.app" 2>/dev/null || true; }
osascript -e 'tell application "System Events" to set frontmost of every process whose name contains "MacCleanup" to true' >/dev/null 2>&1 || true

# 2) 等它报出正常尺寸（最多 6 次 × 0.6s）
BEST=""
for _ in 1 2 3 4 5 6; do
  BEST="$(pick || true)"
  W="$(echo "$BEST" | awk '{print $3}')"
  [ -n "${W:-}" ] && [ "${W:-0}" -ge 800 ] && break
  sleep 0.6
done

read -r _a WID W H <<<"${BEST:-}"
if [ -z "${WID:-}" ]; then
  echo "❌ 没找到含「${KEY}」的窗口。先跑：swift tools/winlist.swift（换一个更短的关键词试试）"
  exit 1
fi

mkdir -p "$(dirname "$OUT")"
screencapture -l "$WID" -x -o "$OUT"
echo "✅ 窗口 id=${WID}（报出尺寸 ${W}x${H}）已截图：${OUT}"
[ "${W:-0}" -lt 800 ] && if [ "${W:-0}" -lt 800 ]; then echo "⚠️ 尺寸偏小：App 可能仍不在前台（WindowServer 只留了缩小快照），把它点到最前再跑一次。"; fi
echo "   图太大就压一下：sips -Z 1400 \"${OUT}\" --out \"${OUT%.png}-small.png\""
