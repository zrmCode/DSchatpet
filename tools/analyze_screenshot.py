"""对截图做定量结构分析(不依赖"看图"能力)。

主要用来判断"眼睛是否变成两对/重影":把图中的深色区域做连通域分析,
按面积与位置列出候选特征(眼睛通常是脸上最集中的深色块),
再看它们是否呈"两对对称排列"——那是双眼叠加的典型签名。

用法::

    .venv\\Scripts\\python.exe tools\\analyze_screenshot.py shots\\user-clip.png
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image


def label_components(mask: np.ndarray, min_area: int = 30) -> list[dict]:
    """四邻域连通域标记(自己实现,避免引入 scipy)。"""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    blobs: list[dict] = []

    for y0 in range(height):
        row = mask[y0]
        for x0 in range(width):
            if not row[x0] or seen[y0, x0]:
                continue
            queue = deque([(y0, x0)])
            seen[y0, x0] = True
            pixels = []
            while queue:
                y, x = queue.popleft()
                pixels.append((y, x))
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
            if len(pixels) < min_area:
                continue
            ys = [p[0] for p in pixels]
            xs = [p[1] for p in pixels]
            blobs.append({
                "area": len(pixels),
                "bbox": (min(xs), min(ys), max(xs), max(ys)),
                "center": (sum(xs) / len(xs), sum(ys) / len(ys)),
                "w": max(xs) - min(xs) + 1,
                "h": max(ys) - min(ys) + 1,
            })
    blobs.sort(key=lambda b: -b["area"])
    return blobs


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="截图结构分析")
    parser.add_argument("image", type=Path)
    parser.add_argument("--threshold", type=int, default=90, help="灰度阈值(默认 90)")
    parser.add_argument("--top", type=int, default=14)
    args = parser.parse_args()

    if not args.image.is_file():
        print(f"找不到文件: {args.image}")
        return 2

    img = Image.open(args.image).convert("RGB")
    arr = np.asarray(img, dtype=np.int16)
    h, w = arr.shape[:2]
    gray = (arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114)

    alpha_note = ""
    if Image.open(args.image).mode in ("RGBA", "LA"):
        a = np.asarray(Image.open(args.image).convert("RGBA"))[..., 3]
        alpha_note = f",透明像素占比 {(a < 128).mean():.1%}"

    print(f"[图像] {args.image.name} {w}x{h}{alpha_note}")
    print(f"[颜色] 不同颜色数={len(np.unique(arr.reshape(-1, 3), axis=0))}")

    mask = gray < args.threshold
    print(f"[阈值 {args.threshold}] 深色像素占比 {mask.mean():.1%}")

    blobs = label_components(mask)
    print(f"\n[深色连通域] 共 {len(blobs)} 个(面积>=30),前 {args.top} 个:")
    print(f"  {'#':>2} {'面积':>7} {'尺寸':>11} {'中心':>14}  包围盒")
    for i, b in enumerate(blobs[:args.top], 1):
        cx, cy = b["center"]
        print(f"  {i:>2} {b['area']:>7} {b['w']:>5}x{b['h']:<5} ({cx:>6.1f},{cy:>6.1f})  {b['bbox']}")

    # 眼睛签名:宽度相近、纵向位置接近、横向成对分布的块
    eye_like = [b for b in blobs if 6 <= b["w"] <= w * 0.25 and 4 <= b["h"] <= h * 0.25]
    print(f"\n[眼睛候选] 尺寸像眼睛的块: {len(eye_like)} 个")
    bands: dict[int, list[dict]] = {}
    for b in eye_like:
        band = int(b["center"][1] // (h * 0.05))
        bands.setdefault(band, []).append(b)
    for band, items in sorted(bands.items()):
        if len(items) >= 2:
            xs = sorted(b["center"][0] for b in items)
            ys = [b["center"][1] for b in items]
            print(f"  纵向带 {band}(y≈{sum(ys) / len(ys):.0f}): {len(items)} 个,"
                  f"横坐标={[round(x) for x in xs]}")
            if len(items) >= 4:
                print("    ⚠️ 同一水平带上出现 4 个以上眼睛大小的块 —— 疑似两对眼睛叠加")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
