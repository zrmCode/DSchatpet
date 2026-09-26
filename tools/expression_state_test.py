"""表情状态实验:判断"连续套用多个表情"是否会留下残留(眼睛图层叠加)。

做法:在同一只模型上按不同顺序施加表情,分别截图,然后比较像素:
  - ``love_only``      : 只套「爱心眼」
  - ``star_then_love`` : 先套「星星眼」再套「爱心眼」
  两者**应当完全一致**。若不一致,说明旧表情没有被正确替换 —— 这就是
  "眼睛/图层重叠"的典型成因。

同时输出每张图的不透明像素数与颜色数,便于判断是否有异常叠加。

用法::

    .venv\\Scripts\\python.exe tools\\expression_state_test.py
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

#: (状态名, 依次施加的表情)。``__freeze__`` 冻结模型,``__reset__`` 归位。
#: 设计要点:先做两次"什么都不改"的对照,确认方法本身噪声为 0,
#: 再比较「先星星后爱心」与「直接爱心眼」—— 两者应完全一致,否则就是表情残留。
STEPS: list[tuple[str, list[str]]] = [
    ("frozen_a", ["__freeze__"]),
    ("frozen_b", []),
    ("star", ["星星眼"]),
    ("love_after_star", ["爱心眼"]),
    ("reset_then_love", ["__reset__", "爱心眼"]),
    ("love_after_star_again", ["__reset__", "星星眼", "爱心眼"]),
]


def qimage_to_rgba(image: QImage) -> np.ndarray:
    """QImage -> (h, w, 4) 的数组。QImage 不能直接被 np.asarray 转换,要走 constBits。"""
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    buffer = converted.constBits()
    arr = np.frombuffer(buffer, dtype=np.uint8, count=converted.width() * converted.height() * 4)
    return arr.reshape(converted.height(), converted.width(), 4).astype(np.int16)


def alpha_stats(arr: np.ndarray) -> tuple[int, int]:
    mask = arr[..., 3] > 10
    colors = len(np.unique(arr[mask].reshape(-1, 4), axis=0)) if mask.any() else 0
    return int(mask.sum()), colors


def diff_ratio(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return float("nan")
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    return float((d > 12).sum()) / d.size


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    cfg = config_mod.Config.load()
    cfg.gaze_follow = False
    cfg.idle_motion = True
    cfg.poke_reaction = False   # 实验期间别让点击反应插进来污染状态
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
    window.enable_test_mode("expression_state_test")
    window.show()

    shots = ROOT / "shots" / "expr"
    shots.mkdir(parents=True, exist_ok=True)
    captured: dict[str, np.ndarray] = {}
    state = {"index": 0, "phase": "apply"}

    def apply_step() -> None:
        name, actions = STEPS[state["index"]]
        for action in actions:
            if action == "__freeze__":
                # 冻住模型:关掉待机动画、自动眨眼与呼吸,这样像素差异只可能来自表情
                window.cfg.idle_motion = False
                window.pet.stop_all_motions()
                window.pet.freeze_automatics(True)
                print("  已冻结动画/眨眼/呼吸", flush=True)
            elif action == "__reset__":
                window.pet.reset_expressions()
            else:
                ok = window.pet.set_expression(action)
                print(f"  施加 {action} -> {ok}", flush=True)
        state["phase"] = "capture"

    def capture() -> None:
        name, _ = STEPS[state["index"]]
        image = window.grabFramebuffer()
        path = shots / f"{name}.png"
        image.save(str(path))
        arr = qimage_to_rgba(image)
        captured[name] = arr
        opaque, colors = alpha_stats(arr)
        print(f"  [{name}] 不透明像素={opaque} 颜色数={colors} 当前表情={window.pet.current_expression!r} -> {path.name}")
        state["index"] += 1
        state["phase"] = "apply"
        if state["index"] >= len(STEPS):
            finish()

    def tick() -> None:
        try:
            print(f"  [tick] step={state['index']} phase={state['phase']}", flush=True)
            if state["phase"] == "apply":
                apply_step()
            else:
                capture()
        except Exception as exc:  # 不吞掉:打印后继续下一步,避免卡死
            import traceback
            print(f"  [异常] {exc}\n{traceback.format_exc()}", flush=True)
            state["index"] += 1
            state["phase"] = "apply"
            if state["index"] >= len(STEPS):
                finish()

    def finish() -> None:
        print("\n=== 结果 ===", flush=True)
        pairs = [
            ("frozen_a", "frozen_b", "对照组:两次不做任何改动", 0.001),
            ("frozen_b", "star", "星星眼确实改变了画面", None),
            ("love_after_star", "reset_then_love", "先星星后爱心 vs 归位后爱心", 0.001),
            ("love_after_star", "love_after_star_again", "重复同一序列(应一致)", 0.001),
        ]
        for left, right, label, threshold in pairs:
            if left in captured and right in captured:
                ratio = diff_ratio(captured[left], captured[right])
                if threshold is None:
                    verdict = "OK(有变化才正常)" if ratio > 0.001 else "异常:表情没生效"
                elif ratio > threshold:
                    verdict = "差异过大 —— 有残留/状态不确定"
                else:
                    verdict = "一致"
                print(f"  {label}: 差异 {ratio:.4%}  -> {verdict}", flush=True)
        app.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(1500)   # 留足表情淡入时间

    # 看门狗:无论如何 40 秒后退出,避免把测试挂死
    QTimer.singleShot(40_000, app.quit)

    app.exec()
    window.close()
    live2d.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
