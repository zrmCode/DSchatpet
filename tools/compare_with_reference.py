"""把用户截图与本地参考渲染做配准比对,定位差异区域。

我看不了图片,所以用"数值化"的办法替代眼睛:
  1. 把参考渲染(带 alpha)按截图背景色合成成不透明图
  2. 在小范围内搜索缩放与平移,找最佳对齐
  3. 输出最佳对齐下的差异热区(8x8 网格),指出差异集中在画面的哪个位置

有了"差异集中在哪",就能判断异常部位(例如"上部中央两个对称小块"= 眼睛区域)。

用法::

    .venv\\Scripts\\python.exe tools\\compare_with_reference.py shots\\user-clip.png shots\\expr\\frozen_a.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

WORK = 200          # 配准用的工作分辨率(越小越快)
GRID = 8            # 差异热区网格
SEARCH_OFFSET = 40  # 平移搜索范围(工作分辨率下的像素)
SEARCH_SCALES = [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15]


def to_gray(img: Image.Image) -> np.ndarray:
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    return arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114


def composite_on(image: Image.Image, bg: tuple[int, int, int]) -> Image.Image:
    """把带 alpha 的参考图合成到指定背景色上。"""
    rgba = image.convert("RGBA")
    canvas = Image.new("RGBA", rgba.size, (*bg, 255))
    canvas.alpha_composite(rgba)
    return canvas.convert("RGB")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="截图与参考渲染的配准比对")
    parser.add_argument("user_image", type=Path)
    parser.add_argument("reference", type=Path)
    args = parser.parse_args()

    if not args.user_image.is_file() or not args.reference.is_file():
        print("文件不存在")
        return 2

    user_img = Image.open(args.user_image).convert("RGB")
    ref_img = Image.open(args.reference).convert("RGBA")

    # 背景色取用户截图边缘的中位数
    u = np.asarray(user_img, dtype=np.int16)
    border = np.concatenate([
        u[0:3].reshape(-1, 3), u[-3:].reshape(-1, 3),
        u[:, 0:3].reshape(-1, 3), u[:, -3:].reshape(-1, 3),
    ])
    bg = tuple(int(v) for v in np.median(border, axis=0))
    print(f"[背景] 估计为 RGB{bg}")

    ref_on_bg = composite_on(ref_img, bg)
    user_work = to_gray(user_img.resize((WORK, WORK), Image.LANCZOS))
    ref_base = to_gray(ref_on_bg)

    best = None
    for scale in SEARCH_SCALES:
        size = max(20, int(WORK * scale))
        ref_small = np.asarray(
            Image.fromarray(ref_base.astype(np.uint8)).resize((size, size), Image.LANCZOS),
            dtype=np.float32,
        )
        for dy in range(-SEARCH_OFFSET, SEARCH_OFFSET + 1, 4):
            for dx in range(-SEARCH_OFFSET, SEARCH_OFFSET + 1, 4):
                # 把参考放到工作画布上(带平移)
                canvas = np.full((WORK, WORK), 0.0, dtype=np.float32)
                mask = np.zeros((WORK, WORK), dtype=bool)
                y0, x0 = WORK // 2 - size // 2 + dy, WORK // 2 - size // 2 + dx
                ys, xs = max(0, y0), max(0, x0)
                ye, xe = min(WORK, y0 + size), min(WORK, x0 + size)
                if ye <= ys or xe <= xs:
                    continue
                canvas[ys:ye, xs:xe] = ref_small[ys - y0:ye - y0, xs - x0:xe - x0]
                mask[ys:ye, xs:xe] = True
                base_patch = ref_base[ys * (ref_base.shape[0] // WORK):ye * (ref_base.shape[0] // WORK),
                                      xs * (ref_base.shape[1] // WORK):xe * (ref_base.shape[1] // WORK)]
                # 只比较参考图里"模型存在"的区域(alpha>10)
                alpha_small = np.asarray(
                    ref_img.convert("RGBA").resize((size, size), Image.LANCZOS)
                )[..., 3]
                a_ys, a_xs = ys - y0, xs - x0
                sub_alpha = alpha_small[a_ys:a_ys + (ye - ys), a_xs:a_xs + (xe - xs)]
                model_mask = sub_alpha > 10
                if model_mask.sum() < 200:
                    continue
                diff = np.abs(user_work[ys:ye, xs:xe] - canvas[ys:ye, xs:xe])[model_mask].mean()
                if best is None or diff < best["score"]:
                    best = {"score": float(diff), "scale": scale, "dx": dx, "dy": dy,
                            "box": (ys, xs, ye, xe), "mask": model_mask.copy()}

    if best is None:
        print("未能对齐(参考图里几乎没有不透明区域?)")
        return 1

    print(f"[配准] 最佳: 缩放={best['scale']} 偏移=({best['dx']},{best['dy']}) "
          f"平均灰度差={best['score']:.1f}")
    if best["score"] > 60:
        print("  ⚠️ 平均差异偏大,可能没真正对齐(截图内容与参考差别较大)")

    # 差异热区
    ys, xs, ye, xe = best["box"]
    box_w, box_h = xe - xs, ye - ys
    ref_small = np.asarray(
        Image.fromarray(ref_base.astype(np.uint8)).resize((box_w, box_h), Image.LANCZOS),
        dtype=np.float32,
    )
    sub_user = user_work[ys:ye, xs:xe]
    diff_map = np.abs(sub_user - ref_small) * best["mask"]
    cell_y = max(1, box_h // GRID)
    cell_x = max(1, box_w // GRID)
    print("\n[差异热区] 每格平均灰度差(值越大差异越大):")
    cells = []
    for gy in range(GRID):
        row = []
        for gx in range(GRID):
            block = diff_map[gy * cell_y:(gy + 1) * cell_y, gx * cell_x:(gx + 1) * cell_x]
            block_mask = best["mask"][gy * cell_y:(gy + 1) * cell_y, gx * cell_x:(gx + 1) * cell_x]
            value = float(block[block_mask].mean()) if block_mask.any() else 0.0
            row.append(value)
        cells.append(row)
    for gy, row in enumerate(cells):
        print("  " + " ".join(f"{v:5.1f}" for v in row))

    flat = [(v, gy, gx) for gy, row in enumerate(cells) for gx, v in enumerate(row)]
    flat.sort(reverse=True)
    print("\n[最不一致的 5 格](网格坐标, 左上为 0,0):")
    for value, gy, gx in flat[:5]:
        print(f"  格({gx},{gy}) 差异 {value:.1f}  -> 画面{'上' if gy < GRID / 2 else '下'}"
              f"{'左' if gx < GRID / 2 else '右'}部")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
