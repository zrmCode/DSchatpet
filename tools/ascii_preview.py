"""把图片转成 ASCII 字符画 —— 让看不了图的模型也能"读"出画面结构。

用途:排查"眼睛重叠"这类视觉问题。把脸部区域放大成字符画,
两对眼睛/错位/叠加都会在字符排布里显露出来。

用法::

    .venv\\Scripts\\python.exe tools\\ascii_preview.py 图片.png
    ... --width 110 --height 55 --crop 0,0,578,300 --invert
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

#: 由暗到亮的字符梯度
RAMP = "@%#*+=-:. "


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="图片转 ASCII 字符画")
    parser.add_argument("image", type=Path)
    parser.add_argument("--width", type=int, default=100)
    parser.add_argument("--height", type=int, default=50)
    parser.add_argument("--crop", default=None, help="裁剪区域 x0,y0,x1,y1")
    parser.add_argument("--invert", action="store_true", help="反转明暗(适合深色底)")
    parser.add_argument("--contrast", action="store_true", help="自动拉对比度")
    parser.add_argument("--mode", default="lum",
                        choices=["lum", "sat", "blue", "dark", "hue"],
                        help="lum=灰度;sat=饱和度;blue=蓝色通道占优;dark=暗色掩码;hue=色相")
    args = parser.parse_args()

    if not args.image.is_file():
        print(f"找不到文件: {args.image}")
        return 2

    img = Image.open(args.image).convert("RGB")
    if args.crop:
        try:
            x0, y0, x1, y1 = (int(v) for v in args.crop.split(","))
            img = img.crop((x0, y0, min(x1, img.width), min(y1, img.height)))
        except ValueError:
            print("--crop 格式应为 x0,y0,x1,y1")
            return 2

    small = img.resize((args.width, max(1, int(args.height * 0.5))), Image.LANCZOS)
    arr = np.asarray(small, dtype=np.float32)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    lum = r * 0.299 + g * 0.587 + b * 0.114

    if args.mode == "sat":
        mx = arr.max(axis=2)
        mn = arr.min(axis=2)
        lum = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1) * 255, 0)
    elif args.mode == "blue":
        lum = np.clip(b - (r + g) / 2 + 128, 0, 255)
    elif args.mode == "dark":
        lum = np.where(lum < 110, 255, 0)
    elif args.mode == "hue":
        # 粗略色相:红/黄/绿/蓝分段映射成不同亮度,便于区分颜色区域
        hue = np.zeros_like(lum)
        hue = np.where((b > r) & (b > g), 220, hue)
        hue = np.where((g > r) & (g > b), 140, hue)
        hue = np.where((r > g) & (r > b), 60, hue)
        lum = hue

    if args.contrast:
        lo, hi = np.percentile(lum, 5), np.percentile(lum, 95)
        if hi > lo:
            lum = np.clip((lum - lo) / (hi - lo), 0, 1) * 255

    if args.invert:
        lum = 255 - lum
    # 每行重复两次,抵消字符的高宽比
    idx = (lum / 255 * (len(RAMP) - 1)).astype(int)
    print(f"[{args.image.name}] 裁剪后 {img.width}x{img.height},字符画 {args.width}x{small.height}")
    print("+" + "-" * args.width + "+")
    for row in idx:
        print("|" + "".join(RAMP[i] for i in row) + "|")
    print("+" + "-" * args.width + "+")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
