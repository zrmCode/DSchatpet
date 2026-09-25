"""渲染结果检查:不看图也能判断"模型到底画出来没有"。

对 PNG 做三件事:
1. 统计 alpha>阈值的包围盒与占比(模型该在画面中间、且占一定面积)
2. 统计颜色多样性(避免"一片纯色"的假渲染)
3. 对比两张截图,给出差异像素比例(用于验证换表情/换动作确实改变了画面)

用法::

    python tools/check_render.py shots/shot.png
    python tools/check_render.py shots/a.png --compare shots/b.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ALPHA_THRESHOLD = 10


def _as_array(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.int16)


def describe(path: Path) -> None:
    arr = _as_array(path)
    height, width = arr.shape[:2]
    alpha = arr[..., 3]
    mask = alpha > ALPHA_THRESHOLD
    opaque = int(mask.sum())

    print(f"[{path.name}] 尺寸 {width}x{height}")
    if opaque == 0:
        print("  ⚠️ 全部透明 —— 模型没有画出来")
        return

    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    y0, y1 = int(rows[0]), int(rows[-1])
    x0, x1 = int(cols[0]), int(cols[-1])
    colors = len(np.unique(arr[mask].reshape(-1, 4), axis=0))

    print(f"  不透明占比: {opaque / (width * height):.1%}")
    print(f"  模型包围盒: ({x0},{y0}) - ({x1},{y1}),占画面 "
          f"{(x1 - x0) / width:.0%} 宽 × {(y1 - y0) / height:.0%} 高")
    print(f"  不同颜色数: {colors}")
    print(f"  视觉中心: ({(x0 + x1) / 2 / width:.2f}, {(y0 + y1) / 2 / height:.2f})(0.5 为正中)")

    # 贴边检查:边缘整列/整行有大量不透明像素 = 大概率被裁切
    edge_hits = {
        "上": int(mask[0].sum()), "下": int(mask[-1].sum()),
        "左": int(mask[:, 0].sum()), "右": int(mask[:, -1].sum()),
    }
    clipped = [name for name, count in edge_hits.items() if count > width * 0.02]
    print(f"  边缘不透明像素: {edge_hits}")
    if clipped:
        print(f"  ⚠️ {'/'.join(clipped)}边缘有内容,模型可能被裁切(可调小 scale 或加大窗口)")
    else:
        print("  ✅ 四周留白正常,未被裁切")


def compare(left: Path, right: Path) -> None:
    a = _as_array(left)
    b = _as_array(right)
    if a.shape != b.shape:
        print(f"⚠️ 尺寸不同,无法直接比对:{a.shape} vs {b.shape}")
        return
    diff = np.abs(a - b).max(axis=2)
    changed = int((diff > 12).sum())
    total = diff.size
    print(f"[对比] {left.name} vs {right.name}: 变化像素 {changed / total:.2%}")


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染结果检查")
    parser.add_argument("image", type=Path)
    parser.add_argument("--compare", type=Path, default=None)
    args = parser.parse_args()

    if not args.image.is_file():
        print(f"找不到文件: {args.image}", file=sys.stderr)
        return 2
    describe(args.image)
    if args.compare is not None:
        compare(args.compare, args.image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
