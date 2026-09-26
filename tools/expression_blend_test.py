"""表情切换的"混合残留"实验。

怀疑点:连续快速切换表情时,Cubism 的表达式管理器会让新旧表情同时以部分权重生效
(淡入淡出),若淡出没有正确结束,就会留下"两套眼睛叠在一起"的状态。

做法(模型全程冻结,排除动画干扰):
  1. 干净地套「星星眼」-> 等 2.5s -> 截图 clean_star
  2. 干净地套「爱心眼」-> 等 2.5s -> 截图 clean_love
  3. 快速连打:星星眼 -> 120ms -> 爱心眼,然后分别在 +0.1s / +1s / +4s 截图
  4. 比较:若 +4s 的图与 clean_love 不一致 => 表情状态卡住(真 bug)
           若一致 => 只是短暂的过渡混合(正常现象)

用法::

    .venv\\Scripts\\python.exe tools\\expression_blend_test.py
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
    cfg.idle_motion = False       # 冻结,排除动画噪声
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
    window.enable_test_mode("expression_blend_test")
    window.show()

    shots = ROOT / "shots" / "blend"
    shots.mkdir(parents=True, exist_ok=True)
    captured: dict[str, np.ndarray] = {}
    pending: list[tuple[int, str]] = []

    def freeze() -> None:
        window.pet.stop_all_motions()
        window.pet.freeze_automatics(True)

    def grab(label: str) -> None:
        image = window.grabFramebuffer()
        image.save(str(shots / f"{label}.png"))
        captured[label] = qimage_to_rgba(image)
        print(f"  截图 {label}(当前表情={window.pet.current_expression!r})", flush=True)

    def schedule(delay_ms: int, action) -> None:
        pending.append((delay_ms, action))

    def run_script() -> None:
        freeze()
        print("  阶段1:干净地套表情", flush=True)
        schedule(200, lambda: window.pet.set_expression("星星眼"))
        schedule(2700, lambda: grab("clean_star"))
        schedule(2900, lambda: (window.pet.reset_expressions(), window.pet.set_expression("爱心眼")))
        schedule(5400, lambda: grab("clean_love"))

        print("  阶段2:快速连打两个表情(间隔 120ms)", flush=True)
        schedule(5600, window.pet.reset_expressions)
        schedule(5900, lambda: window.pet.set_expression("星星眼"))
        schedule(6020, lambda: window.pet.set_expression("爱心眼"))
        schedule(6120, lambda: grab("rapid_0p1s"))
        schedule(7000, lambda: grab("rapid_1s"))
        schedule(10000, lambda: grab("rapid_4s"))
        schedule(10500, finish)

    def finish() -> None:
        print("\n=== 结果 ===", flush=True)
        if {"rapid_4s", "clean_love"} <= captured.keys():
            ratio = diff_ratio(captured["clean_love"], captured["rapid_4s"])
            verdict = ("一致 —— 只是过渡混合,没有卡住" if ratio < 0.002
                       else "不一致 —— 快速切换后表情状态卡住了(真 bug)")
            print(f"  +4s 后的状态 vs 干净套用爱心眼: 差异 {ratio:.4%}  -> {verdict}", flush=True)
        if {"rapid_0p1s", "clean_love"} <= captured.keys():
            ratio = diff_ratio(captured["clean_love"], captured["rapid_0p1s"])
            print(f"  +0.1s 的中间过渡 vs 干净爱心眼: 差异 {ratio:.4%}(越大说明混合越明显)", flush=True)
        if {"clean_star", "clean_love"} <= captured.keys():
            ratio = diff_ratio(captured["clean_star"], captured["clean_love"])
            print(f"  星星眼 vs 爱心眼(两个干净状态): 差异 {ratio:.4%}", flush=True)
        app.quit()

    # 用绝对时间调度,避免嵌套计时器叠加误差
    run_script()
    for delay, action in pending:
        QTimer.singleShot(delay, action)
    QTimer.singleShot(30_000, app.quit)

    app.exec()
    window.close()
    live2d.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
