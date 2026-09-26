"""切换表情后眼睛区域是否留下**像素级**残影,并对比不同裁剪遮罩数的效果。

背景(用户反馈):切换表情后,眼睛区域出现**永久残影**。
- 参数级测试(``expression_residue_test.py``)已证明:**参数会完全恢复**,没有残留。
- 所以问题若存在,只能在**渲染层**。最可疑的是 Cubism 的**裁剪遮罩缓冲(mask buffer)**:
  切换表情会改变"有哪些美术层需要被遮罩",缓冲区不够时绘制状态就会出错,
  表现就是眼睛这类靠遮罩裁剪的部位出现多余/残留的图像。

之前做过 mask 2/8/16 对比,但那次是**冻结模型下的全画面比较**,测不到"切表情之后"才出现的
问题 —— 这次专门按用户的步骤来:

    归位 -> 截图 clean -> 施加 星星眼 -> 切到中性表情 -> 截图 after
    比较 clean 与 after:差异比例 + 差异区域包围盒(残影应集中在眼睛附近)
再用 maskBufferCount=8 重跑一遍(重建模型),看残影是否消失。

用法::

    .venv\\Scripts\\python.exe tools\\expression_ghost_pixel_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live2d.v3 as live2d
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QSurfaceFormat
from PySide6.QtWidgets import QApplication

from pet import config as config_mod
from pet.actions import load_actions
from pet.window import PetWindow

TRIGGER = "星星眼"      # 用来触发的表情
NEUTRAL = "撤回"        # 切回的中性表情
MASK_COUNTS = [2, 8]
SETTLE_MS = 2000

S = {"stage": 0, "captures": {}, "results": [], "mask_index": 0}


def qimage_to_rgba(image: QImage) -> np.ndarray:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    buf = converted.constBits()
    arr = np.frombuffer(buf, dtype=np.uint8, count=converted.width() * converted.height() * 4)
    return arr.reshape(converted.height(), converted.width(), 4).astype(np.int16)


def diff_stats(a: np.ndarray, b: np.ndarray) -> tuple[float, tuple[int, int, int, int] | None]:
    d = np.abs(a - b).max(axis=2)
    mask = d > 12
    ratio = float(mask.sum()) / mask.size
    if not mask.any():
        return ratio, None
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    return ratio, (int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1]))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    cfg = config_mod.Config.load()
    cfg.gaze_follow = False
    cfg.idle_motion = False      # 冻结,排除动画干扰
    cfg.poke_reaction = False
    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("找不到模型目录")
        return 2

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    live2d.init()
    app = QApplication(sys.argv[:1])
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir), load_actions(model_dir))
    window.enable_test_mode("expression_ghost_pixel_test")
    window.show()

    shots = ROOT / "shots" / "ghost"
    shots.mkdir(parents=True, exist_ok=True)

    def freeze() -> None:
        window.pet.stop_all_motions()
        window.pet.freeze_automatics(True)

    def capture(name: str) -> None:
        image = window.grabFramebuffer()
        image.save(str(shots / f"{name}.png"))
        S["captures"][name] = qimage_to_rgba(image)

    def step() -> None:
        stage = S["stage"]
        S["stage"] += 1
        mask = MASK_COUNTS[S["mask_index"]]

        if stage == 0:
            print(f"\n########## maskBufferCount = {mask} ##########")
            if S["mask_index"] > 0:
                window.reload_model(mask)
            freeze()
            return
        if stage == 1:
            window.pet.reset_expressions()
            return
        if stage == 2:
            capture(f"clean_mask{mask}")
            return
        if stage == 3:
            window.pet.set_expression(TRIGGER)
            return
        if stage == 4:
            window.pet.set_expression(NEUTRAL)
            return
        if stage == 5:
            capture(f"after_mask{mask}")
            return
        if stage == 6:
            clean = S["captures"][f"clean_mask{mask}"]
            after = S["captures"][f"after_mask{mask}"]
            ratio, bbox = diff_stats(clean, after)
            S["results"].append((mask, ratio, bbox))
            print(f"  归位截图 vs 切回中性表情后截图:差异 {ratio:.4%}"
                  + (f",差异区域 {bbox}" if bbox else ",完全一致"))
            if bbox:
                w, h = after.shape[1], after.shape[0]
                cx = (bbox[0] + bbox[2]) / 2 / w
                cy = (bbox[1] + bbox[3]) / 2 / h
                print(f"    差异区域中心在画面 ({cx:.2f}, {cy:.2f})(0.5,0.5 为正中;"
                      f"眼睛通常在上半部)")
            S["mask_index"] += 1
            S["stage"] = 0
            if S["mask_index"] >= len(MASK_COUNTS):
                report()
            return

    def report() -> None:
        print("\n=== 汇总 ===")
        for mask, ratio, bbox in S["results"]:
            verdict = "有残影!" if ratio > 0.001 else "无残影"
            print(f"  mask={mask}: 差异 {ratio:.4%} -> {verdict}"
                  + (f",区域 {bbox}" if bbox else ""))
        if len(S["results"]) >= 2:
            first, second = S["results"][0][1], S["results"][1][1]
            if first > 0.001 and second < first / 3:
                print("  => 提高遮罩缓冲数能消除残影 —— 修法:把 maskBufferCount 调大")
            elif first > 0.001 and abs(first - second) < first / 3:
                print("  => 提高遮罩缓冲数无效,残影另有原因")
            else:
                print("  => 两种配置都没有复现出残影")
        app.quit()

    timer = QTimer()
    timer.timeout.connect(step)
    timer.start(SETTLE_MS)
    QTimer.singleShot(120_000, app.quit)

    app.exec()
    window.close()
    live2d.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
