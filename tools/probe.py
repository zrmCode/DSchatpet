"""模型结构诊断工具。

排查"表情/动画注册了但播不出来"这类问题:打印 ``GetMotions()`` /
``GetExpressionIds()`` 的真实结构,并实测 ``LoadExtraMotion`` 的返回值语义与
索引起点(我们的动作索引就是基于这个假设维护的)。

必须有桌面环境(需要真实 OpenGL 上下文)。

用法::

    .venv\\Scripts\\python.exe tools\\probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live2d.v3 as live2d
from PySide6.QtCore import QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from pet import config as config_mod
from pet.actions import load_actions
from pet.window import PetWindow


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    cfg = config_mod.Config.load()
    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("找不到模型目录")
        return 2
    model_json = config_mod.find_model_json(model_dir)
    actions = load_actions(model_dir)

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    live2d.init()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)

    window = PetWindow(cfg, model_dir, model_json, actions)
    window.persist_config = False   # 诊断工具不该改用户的 config.json
    window.show()

    state = {"tick": 0}

    def probe() -> None:
        model = window.pet.model
        assert model is not None

        print("\n=== GetMotions() 现有分组 ===")
        for group, entries in model.GetMotions().items():
            files = [Path(e.get("File", "")).name for e in entries]
            print(f"  {group}: {len(entries)} 个 -> {files}")

        ids = model.GetExpressionIds()
        print(f"\n=== 已注册表达式 {len(ids)} 个 ===")
        print("  前 8 个:", ids[:8])

        print("\n=== LoadExtraMotion 返回值实测 ===")
        group = "ProbeGroup"
        paths = sorted((model_dir / "motions").glob("*.motion3.json"))[:3]
        returns = []
        for path in paths:
            returns.append(model.LoadExtraMotion(group, str(path)))
        print("  逐个调用的返回值:", returns)
        print("  传入文件:", [p.name for p in paths])
        entries = model.GetMotions().get(group, [])
        print(f"  该组实际内容 {len(entries)} 个:", [Path(e.get('File', '')).name for e in entries])
        if returns == list(range(len(returns))):
            print("  => 返回值 = 新动作在组内的 0 基索引(与 PetModel 的计数器实现一致)")
        else:
            print(f"  => 返回值语义为 {returns},请核对 PetModel._register_motions 的索引维护方式")

        print("\n=== 逐个试播手动动画 ===")
        for name, index in window.pet._motions.items():
            ok = window.pet.play_motion(name)
            print(f"  {name:<12} index={index:<3} play={ok}")

        print("\n=== 试播表情 ===")
        for name in window.pet.expressions[:3]:
            print(f"  toggle {name} -> {window.pet.toggle_expression(name)}")

        print("\n=== IsMotionFinished() 采样(每 500ms) ===")

    def sampler() -> None:
        model = window.pet.model
        assert model is not None
        state["tick"] += 1
        finished = model.IsMotionFinished()
        print(f"  t={state['tick'] * 0.5:.1f}s  IsMotionFinished={finished}")
        if state["tick"] >= 8:
            app.quit()

    def start_sampler() -> None:
        timer = QTimer()
        timer.timeout.connect(sampler)
        timer.start(500)
        window._probe_timer = timer  # 防止被 GC 回收

    QTimer.singleShot(1200, probe)
    QTimer.singleShot(1400, start_sampler)

    code = app.exec()
    window.close()
    live2d.dispose()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
