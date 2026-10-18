#!/bin/bash
# 构建原生 GUI 版「Mac 清理助手.app」（AppKit，真窗口，不是网页）
#
#   bash native/build_native.sh              # 编译 + 安装到 ~/Applications
#   bash native/build_native.sh --keep-web   # 同时保留旧的网页版（改名为 ...（网页版）.app）
#
# 结构：
#   Mac 清理助手.app/Contents/
#     MacOS/MacCleanup           Swift 原生界面（唯一的 UI）
#     Resources/server.py        清理引擎的本地服务（复用技能脚本）
#     Resources/index.html       浏览器版界面（菜单里可打开，备用）
#     Resources/skill-scripts/   技能脚本副本（技能目录不存在时兜底）
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Mac 清理助手.app"
DEST_DIR="$HOME/Applications"
DEST="$DEST_DIR/$APP_NAME"
SKILL_SCRIPTS="${SKILL_SCRIPTS:-$HOME/.hermes/skills/devops/macos-deep-optimize/scripts}"
BUILD_DIR="$SRC/native/.build"

echo "▸ 编译 Swift 原生界面"
rm -rf "$BUILD_DIR"; mkdir -p "$BUILD_DIR"
# ── SDK 选择 ──
# 默认 SDK（xcrun --show-sdk-path）可能与本机 swiftc 版本不匹配，报
#   error: could not build Objective-C module 'SwiftShims'
#   error: failed to build module 'Swift'; this SDK is not supported by the compiler
# 这是 CommandLineTools 的常见状态（SDK 比编译器新一个小版本）。
# 策略：先试默认 SDK；失败就按「从新到旧」试同目录下其它已安装 SDK。
SDK_DIR="/Library/Developer/CommandLineTools/SDKs"
DEFAULT_SDK="$(xcrun --show-sdk-path 2>/dev/null || echo "$SDK_DIR/MacOSX.sdk")"

build_with() {
  local sdk="$1"
  rm -f "$BUILD_DIR/MacCleanup"
  swiftc -O -sdk "$sdk" -target arm64-apple-macosx13.0 \
    -o "$BUILD_DIR/MacCleanup" "$SRC/native/main.swift" -framework Cocoa 2>&1 | tail -25
  [ -x "$BUILD_DIR/MacCleanup" ]
}

echo "  · 默认 SDK: $DEFAULT_SDK"
if ! build_with "$DEFAULT_SDK"; then
  echo "  ⚠️ 默认 SDK 与编译器不匹配，尝试其它已安装 SDK…"
  # 按版本号从新到旧排（MacOSX27.0 > MacOSX26.5 > …），跳过软链
  for cand in $(ls -d "$SDK_DIR"/MacOSX*.sdk 2>/dev/null | grep -v 'MacOSX\.sdk$' | sort -Vr); do
    [ "$cand" = "$DEFAULT_SDK" ] && continue
    echo "  · 尝试 $cand"
    if build_with "$cand"; then
      echo "  ✓ 使用 SDK: $cand"
      echo "  💡 想让它成为默认，可设环境变量：export SDK=$cand"
      break
    fi
  done
fi
[ -x "$BUILD_DIR/MacCleanup" ] || { echo "❌ 编译失败：所有已安装 SDK 都不匹配，请 xcode-select --install 更新 CommandLineTools"; exit 1; }
echo "  ✓ 编译完成（$(du -h "$BUILD_DIR/MacCleanup" | cut -f1)）"

echo "▸ 组装 $DEST"
# 老的网页版先让位（内容还在，改个名字留作备份）
if [ -d "$DEST" ]; then
  if [ "${1:-}" = "--keep-web" ]; then
    rm -rf "$DEST_DIR/Mac 清理助手（网页版）.app"
    mv "$DEST" "$DEST_DIR/Mac 清理助手（网页版）.app"
    echo "  · 旧网页版已保留为：Mac 清理助手（网页版）.app"
  else
    rm -rf "$DEST"
  fi
fi
mkdir -p "$DEST/Contents/MacOS" "$DEST/Contents/Resources/skill-scripts"
cp "$SRC/native/Info.plist" "$DEST/Contents/Info.plist"
cp "$BUILD_DIR/MacCleanup"  "$DEST/Contents/MacOS/MacCleanup"
cp "$SRC/app/server.py"     "$DEST/Contents/Resources/server.py"
cp "$SRC/app/index.html"    "$DEST/Contents/Resources/index.html"
chmod +x "$DEST/Contents/MacOS/MacCleanup"

if [ -d "$SKILL_SCRIPTS" ]; then
  cp "$SKILL_SCRIPTS"/*.py "$DEST/Contents/Resources/skill-scripts/" 2>/dev/null || true
  echo "  ✓ 内置技能脚本副本：$(ls "$DEST/Contents/Resources/skill-scripts" | tr '\n' ' ')"
fi

if [ -f "$SRC/app/AppIcon.png" ] && command -v iconutil >/dev/null 2>&1; then
  ICONSET="$(mktemp -d)/AppIcon.iconset"; mkdir -p "$ICONSET"
  for s in 16 32 64 128 256 512; do
    sips -z $s $s               "$SRC/app/AppIcon.png" --out "$ICONSET/icon_${s}x${s}.png"    >/dev/null
    sips -z $((s*2)) $((s*2))   "$SRC/app/AppIcon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$DEST/Contents/Resources/AppIcon.icns" >/dev/null 2>&1 && echo "  ✓ 生成图标"
  rm -rf "$(dirname "$ICONSET")"
fi

if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$DEST" >/dev/null 2>&1 && echo "  ✓ ad-hoc 签名完成"
fi
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
[ -x "$LSREG" ] && "$LSREG" -f -R "$DEST" >/dev/null 2>&1 && echo "  ✓ 已向 LaunchServices 重新注册"

echo "✅ 安装完成：$DEST"
echo "打开：Finder → 应用程序 →「Mac 清理助手」（或启动台搜索）"
