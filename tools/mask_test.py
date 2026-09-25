"""裁剪遮罩缓冲实验:验证"遮罩不够导致绘制错位"(眼睛/图层重叠的可能成因)。

Cubism 的 mask buffer 决定同时能有多少个裁剪遮罩。数量不足时,被遮罩的绘制对象
(眼睛高光、睫毛、头发等)会画到遮罩外面 —— 视觉上就像"多出来一套眼睛"。

做法:冻结模型后,分别用 maskBufferCount=2(默认)与 8 渲染同一姿势,比较像素。
  - 完全一致 => 遮罩够用,问题不在裁剪
  - 有差异   => 遮罩不足,需要提高数量(PetModel.mask_buffer_count)

用法::

    .venv\\Scripts\\python.exe tools\\mask_test.py
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

#: (标签, maskBufferCount)。刻意安排成 "同一数量重载两次" 作为对照,
#: 否则无法区分"遮罩不足"与"重载模型本身带来的姿势差异"。
CAPTURES: list[tuple[str, int]] = [
    ("r2_a", 2),
    ("r2_b", 2),
    ("r8_a", 8),
    ("r8_b", 8),
    ("r16_a", 16),
]


def qimage_to_rgba(image: QImage) -> np.ndarray:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    buf = converted.constBits()
    arr = np.frombuffer(buf, dtype=np.uint8, count=converted.width() * converted.height() * 4)
    return arr.reshape(converted.height(), converted.width(), 4).astype(np.int16)


def diff_ratio(a: np.ndarray, b: np.ndarray) -> float:
    d = np.abs(a - b).max(axis=2)
    return float((d > 12).sum()) / d.size


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    cfg = config_mod.Config.load()
    cfg.gaze_follow = False
    cfg.idle_motion = False      # 冻住模型,差异只可能来自遮罩
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
    app.setQuitOnLastWindowClosed(True)

    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir), load_actions(model_dir))
    window.persist_config = False
    window.show()

    shots = ROOT / "shots" / "mask"
    shots.mkdir(parents=True, exist_ok=True)
    captured: dict[str, np.ndarray] = {}
    state = {"index": 0, "phase": "reload"}

    def freeze() -> None:
        window.pet.stop_all_motions()
        window.pet.freeze_automatics(True)

    def tick() -> None:
        idx = state["index"]
        if idx >= len(CAPTURES):
            return
        label, count = CAPTURES[idx]
        if state["phase"] == "reload":
            print(f"  重新加载模型(maskBufferCount={count})", flush=True)
            window.reload_model(count)
            freeze()
            state["phase"] = "capture"
            return

        image = window.grabFramebuffer()
        image.save(str(shots / f"{label}.png"))
        arr = qimage_to_rgba(image)
        captured[label] = arr
        print(f"  [{label}] mask={count} 不透明像素={int((arr[..., 3] > 10).sum())}", flush=True)
        state["index"] += 1
        state["phase"] = "reload"

        if state["index"] >= len(CAPTURES):
            report()
            app.quit()

    def report() -> None:
        print("\n=== 结果 ===", flush=True)
        noise = diff_ratio(captured["r2_a"], captured["r2_b"])
        noise8 = diff_ratio(captured["r8_a"], captured["r8_b"])
        effect = diff_ratio(captured["r2_b"], captured["r8_a"])
        effect16 = diff_ratio(captured["r8_a"], captured["r16_a"])
        print(f"  对照(同数量 mask=2 重载两次): {noise:.4%}", flush=True)
        print(f"  对照(同数量 mask=8 重载两次): {noise8:.4%}", flush=True)
        print(f"  mask=2 vs mask=8 : {effect:.4%}", flush=True)
        print(f"  mask=8 vs mask=16: {effect16:.4%}", flush=True)
        floor = max(noise, noise8)
        print(f"\n  重载噪声基线 ≈ {floor:.4%}", flush=True)
        if effect > floor * 2 + 0.002:
            print("  => 结论:mask=2 确实不足,提高遮罩数量会改变渲染(需要修)", flush=True)
        else:
            print("  => 结论:差异在噪声范围内,遮罩数量不是当前问题的成因", flush=True)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(1200)
    QTimer.singleShot(30_000, app.quit)

    app.exec()
    window.close()
    live2d.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
