"""扫描每个表情在生效后把参数推成什么样,重点找**超出模型声明范围**的参数。

背景:这个模型的动作/表情是给 VTube Studio 用的,作者会故意写超范围的数值
(例如 墨镜 把 ParamEyeLOpen 设成 -1.0,而模型声明范围是 0~1;头箍 设成 3.0;
情绪花花 设成 360)。VTS 有自己的范围映射,SDK 里这些越界值可能让美术层跑飞,
表现为眼睛等部位绘制异常(比如看起来叠在一起)。

输出:每个表情生效后所有越界参数(id、当前值、声明范围),并给出汇总。

用法::

    .venv\\Scripts\\python.exe tools\\expression_range_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live2d.v3 as live2d
from PySide6.QtCore import QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication

from pet import config as config_mod
from pet.actions import KIND_EXPRESSION, load_actions

EPS = 1e-3

state = {"phase": "load", "index": 0, "rows": [], "results": [], "error": None}


class _Probe(QOpenGLWidget):
    def initializeGL(self) -> None:
        try:
            live2d.glInit()
            model = live2d.LAppModel()
            model.LoadModelJson(str(state["model_json"]), maskBufferCount=2)
            state["model"] = model
            rows = []
            for index, param_id in enumerate(model.GetParamIds()):
                param = model.GetParameter(index)
                rows.append((param_id, float(param.min), float(param.max), float(param.default)))
            state["rows"] = rows
            for action in state["actions"]:
                model.LoadExtraExpression(action.name, str(action.file))
        except Exception as exc:
            state["error"] = repr(exc)
        state["phase"] = "run"


def out_of_range(model, rows) -> list[tuple[str, float, float, float]]:
    bad = []
    for param_id, mn, mx, _default in rows:
        value = model.GetParameterValue(model.GetParamIds().index(param_id))
        if value < mn - EPS or value > mx + EPS:
            bad.append((param_id, value, mn, mx))
    return bad


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("找不到模型目录")
        return 2
    state["model_json"] = config_mod.find_model_json(model_dir)
    state["actions"] = [a for a in load_actions(model_dir) if a.kind == KIND_EXPRESSION]

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    live2d.init()
    app = QApplication(sys.argv[:1])
    widget = _Probe()
    widget.resize(240, 240)
    widget.show()

    def tick() -> None:
        if state["phase"] != "run":
            return
        model = state.get("model")
        rows = state["rows"]
        if model is None:
            app.quit()
            return

        actions = state["actions"]
        current = state["index"]
        if current >= len(actions):
            report()
            app.quit()
            return

        # 交替:施加表情 -> 下一步检查;检查完归位
        entry = state.setdefault("pending", None)
        if entry is None:
            action = actions[current]
            model.ResetExpressions()
            model.SetExpression(action.name)
            state["pending"] = action.name
            return

        bad = out_of_range(model, rows)
        if bad:
            state["results"].append((entry, bad))
        state["pending"] = None
        state["index"] += 1

    def report() -> None:
        print("\n=== 表情生效后出现越界参数的情况 ===")
        if not state["results"]:
            print("  没有任何表情把参数推出声明范围")
        for name, bad in state["results"]:
            print(f"\n  {name}:")
            for param_id, value, mn, mx in bad:
                eye = "  <= 眼睛参数!" if "Eye" in param_id else ""
                print(f"    {param_id:<28} 当前={value:>9.3f} 声明范围=[{mn:.3f}, {mx:.3f}]{eye}")

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(260)          # 每个表情留一点淡入时间
    QTimer.singleShot(60_000, report)
    QTimer.singleShot(61_000, app.quit)

    app.exec()
    widget.close()
    live2d.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
