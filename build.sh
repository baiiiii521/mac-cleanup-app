#!/bin/bash
# 组装并安装「Mac 清理助手.app」到 ~/Applications
#
#   bash build.sh                 # 构建 + 安装
#   bash build.sh --no-sign       # 跳过 ad-hoc 签名
#
# 结构：
#   Mac 清理助手.app/Contents/
#     MacOS/launch               启动器（挑 python、起服务、开浏览器）
#     Resources/server.py        本地服务（唯一的自研代码，做 HTTP 外壳）
#     Resources/index.html       UI
#     Resources/skill-scripts/   技能脚本副本（技能目录不存在时的兜底）
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Mac 清理助手（网页版）.app"     # 注意：原生 GUI 版由 native/build_native.sh 装成「Mac 清理助手.app」
DEST_DIR="$HOME/Applications"
DEST="$DEST_DIR/$APP_NAME"
SKILL_SCRIPTS="${SKILL_SCRIPTS:-$HOME/.hermes/skills/devops/macos-deep-optimize/scripts}"

echo "▸ 组装 $DEST"
rm -rf "$DEST"
mkdir -p "$DEST/Contents/MacOS" "$DEST/Contents/Resources/skill-scripts"

cp "$SRC/app/Info.plist" "$DEST/Contents/Info.plist"
cp "$SRC/app/launch"     "$DEST/Contents/MacOS/launch"
cp "$SRC/app/server.py"  "$DEST/Contents/Resources/server.py"
cp "$SRC/app/index.html" "$DEST/Contents/Resources/index.html"
chmod +x "$DEST/Contents/MacOS/launch"

# 技能脚本副本（兜底用；运行时优先用实时技能目录）
if [ -d "$SKILL_SCRIPTS" ]; then
  cp "$SKILL_SCRIPTS"/*.py "$DEST/Contents/Resources/skill-scripts/" 2>/dev/null || true
  echo "  ✓ 内置技能脚本副本：$(ls "$DEST/Contents/Resources/skill-scripts" | tr '\n' ' ')"
else
  echo "  ⚠️ 没找到技能脚本目录（$SKILL_SCRIPTS），app 将依赖实时技能目录"
fi

# 图标（有 app/AppIcon.png 就生成 .icns）
if [ -f "$SRC/app/AppIcon.png" ] && command -v iconutil >/dev/null 2>&1; then
  ICONSET="$(mktemp -d)/AppIcon.iconset"; mkdir -p "$ICONSET"
  for s in 16 32 64 128 256 512; do
    sips -z $s $s        "$SRC/app/AppIcon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$SRC/app/AppIcon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$DEST/Contents/Resources/AppIcon.icns" >/dev/null 2>&1 && echo "  ✓ 生成图标"
  rm -rf "$(dirname "$ICONSET")"
fi

# ad-hoc 签名：避免 Gatekeeper 直接拦下
if [ "${1:-}" != "--no-sign" ] && command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$DEST" >/dev/null 2>&1 && echo "  ✓ ad-hoc 签名完成" || echo "  ⚠️ 签名失败（不影响本机使用）"
fi

# 去掉可能带上的隔离属性（否则 Finder 双击会被 Gatekeeper 拦）
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

# 向 LaunchServices 重新注册：每次重建后必须刷新，否则 Finder/启动台点的是过期记录（表现为"点了没反应"）
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [ -x "$LSREG" ]; then
  "$LSREG" -f -R "$DEST" >/dev/null 2>&1 && echo "  ✓ 已向 LaunchServices 重新注册"
fi

# 刷新 Launchpad/Spotlight 索引
touch "$DEST"
echo "✅ 安装完成：$DEST"
echo
echo "打开方式：Finder → 应用程序（或按 F4 打开启动台）→「Mac 清理助手」；"
echo "也可以：open \"$DEST\""
