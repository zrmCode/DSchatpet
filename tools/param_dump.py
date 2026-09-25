"""打印模型参数的真实取值范围(含默认值),用于核对"驱动范围是否与模型匹配"。

背景:VTS 的 ``ParameterSettings`` 里把 ``ParamEyeLOpen`` 的输出范围放大到 0~1.9
(并启用 UseBlinking),而 SDK 的自动眨眼通常只走 0~1。若模型的眼皮/睫毛层需要
更大的值才能完全收起,就会出现"眼睛与眼皮叠在一起"的观感。
这个工具用来确认模型自己声明的范围。

用法::

    .venv\\Scripts\\python.exe tools\\param_dump.py [--filter Eye]
"""

from __future__ import annotations

import argparse
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

_holder = {"rows": [], "error": None}


class _Probe(QOpenGLWidget):
    def initializeGL(self) -> None:
        try:
            live2d.glInit()
            model = live2d.LAppModel()
            model.LoadModelJson(str(_holder["model_json"]))
            rows = []
            for index, param_id in enumerate(model.GetParamIds()):
                param = model.GetParameter(index)
                rows.append((param_id, param.min, param.max, param.default, param.value))
            _holder["rows"] = rows
        except Exception as exc:  # 把异常带回主线程
            _holder["error"] = repr(exc)
        finally:
            _holder["done"] = True


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="模型参数范围")
    parser.add_argument("--filter", default="", help="只显示 id 含该子串的参数")
    parser.add_argument("--wide", action="store_true", help="只显示超出常见范围(>1)的参数")
    args = parser.parse_args()

    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("找不到模型目录")
        return 2
    _holder["model_json"] = config_mod.find_model_json(model_dir)

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    live2d.init()
    app = QApplication(sys.argv[:1])
    widget = _Probe()
    widget.resize(200, 200)
    widget.show()
    QTimer.singleShot(10000, app.quit)
    app.exec()

    if _holder["error"]:
        print("加载失败:", _holder["error"])
        return 1

    rows = _holder["rows"]
    print(f"参数总数: {len(rows)}\n")
    selected = [
        r for r in rows
        if (args.filter.lower() in r[0].lower() if args.filter else True)
        and (r[2] > 1.001 or r[1] < -1.001 if args.wide else True)
    ]
    print(f"{'参数 id':<34}{'min':>9}{'max':>9}{'default':>9}{'当前':>9}")
    for pid, mn, mx, dv, val in selected:
        flag = "  <= 超过 1.0" if mx > 1.001 else ""
        print(f"{pid:<34}{mn:>9.3f}{mx:>9.3f}{dv:>9.3f}{val:>9.3f}{flag}")

    widget.close()
    live2d.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
