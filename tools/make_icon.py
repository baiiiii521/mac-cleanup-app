#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 app 图标 AppIcon.png（1024×1024，纯标准库，无第三方依赖）。

画的是「晚霞天空」：从深靛蓝过渡到橙红的渐变 + 几条被夕阳照亮的云 + 一个小光点，
外加 macOS 风格的圆角（squircle）透明遮罩。

用法：python3 tools/make_icon.py [输出路径]
"""

from __future__ import annotations

import math
import struct
import sys
import zlib

W = H = 1024
RADIUS = int(W * 0.235)          # 圆角半径（近 macOS 图标比例）

STOPS = [                        # (位置 0-1, R, G, B)
    (0.00, 26, 27, 66),
    (0.35, 74, 47, 110),
    (0.62, 158, 63, 104),
    (0.82, 233, 116, 66),
    (1.00, 250, 176, 92),
]

CLOUDS = [                       # (cx, cy, rx, ry, 亮度, 暖度)
    (0.30, 0.62, 0.42, 0.045, 0.55, 0.75),
    (0.72, 0.545, 0.34, 0.032, 0.45, 0.65),
    (0.50, 0.70, 0.52, 0.028, 0.70, 0.85),
    (0.20, 0.78, 0.30, 0.022, 0.60, 0.90),
    (0.82, 0.735, 0.26, 0.020, 0.50, 0.85),
]


def gradient(y: float):
    t = y / (H - 1)
    for i in range(len(STOPS) - 1):
        p0, r0, g0, b0 = STOPS[i]
        p1, r1, g1, b1 = STOPS[i + 1]
        if p0 <= t <= p1:
            k = (t - p0) / (p1 - p0)
            return (r0 + (r1 - r0) * k, g0 + (g1 - g0) * k, b0 + (b1 - b0) * k)
    r, g, b = STOPS[-1][1:]
    return (r, g, b)


def cloud_at(x: float, y: float) -> float:
    """云的不透明度（0-1）：几个椭圆叠加 + 边缘软化。"""
    v = 0.0
    for cx, cy, rx, ry, bright, warm in CLOUDS:
        dx = (x / W - cx) / rx
        dy = (y / H - cy) / ry
        d = math.sqrt(dx * dx + dy * dy)
        if d < 1.0:
            v += bright * (1.0 - d) ** 1.6
    return min(1.0, v)


def sun_at(x: float, y: float) -> float:
    """夕阳余晖（0-1）：在下方中间的一个柔和光斑。"""
    dx = (x / W - 0.52) / 0.34
    dy = (y / H - 0.86) / 0.20
    d = math.sqrt(dx * dx + dy * dy)
    return 0.0 if d > 1.0 else (1.0 - d) ** 2.2


def corner_alpha(x: int, y: int) -> int:
    """圆角遮罩，边缘做 1px 抗锯齿。"""
    cx = min(max(x, RADIUS), W - 1 - RADIUS)
    cy = min(max(y, RADIUS), H - 1 - RADIUS)
    d = math.hypot(x - cx, y - cy)
    if d <= RADIUS - 1:
        return 255
    if d >= RADIUS:
        return 0
    return int(255 * (RADIUS - d))


def build_pixels() -> bytearray:
    buf = bytearray(W * H * 4)
    i = 0
    for y in range(H):
        base_r, base_g, base_b = gradient(y)
        for x in range(W):
            r, g, b = base_r, base_g, base_b
            s = sun_at(x, y)
            if s > 0:
                r += (255 - r) * s * 0.55
                g += (210 - g) * s * 0.45
                b += (140 - b) * s * 0.25
            c = cloud_at(x, y)
            if c > 0:
                # 云：越靠下越暖（被夕阳从下面照亮）
                warmth = min(1.0, max(0.0, (y / H - 0.5) / 0.5))
                cr = 255
                cg = 226 - 40 * (1 - warmth)
                cb = 214 - 90 * warmth
                a = c * 0.62
                r += (cr - r) * a
                g += (cg - g) * a
                b += (cb - b) * a
            buf[i] = int(max(0, min(255, r)))
            buf[i + 1] = int(max(0, min(255, g)))
            buf[i + 2] = int(max(0, min(255, b)))
            buf[i + 3] = corner_alpha(x, y)
            i += 4
    return buf


def png_bytes(pixels: bytearray) -> bytes:
    raw = bytearray()
    stride = W * 4
    for y in range(H):
        raw.append(0)                                  # filter: none
        raw += pixels[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0)   # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "AppIcon.png"
    with open(out, "wb") as f:
        f.write(png_bytes(build_pixels()))
    print("✓ 已生成 %s（%dx%d）" % (out, W, H))


if __name__ == "__main__":
    main()
