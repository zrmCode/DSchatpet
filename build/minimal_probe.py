"""最小复现探针:逐步引入依赖,定位"首次创建原生窗口就崩"到底由谁引起。

用法(打包后的 exe):
    minimal.exe plain                    # 只有 Qt:QApplication + QWidget.show
    minimal.exe pyopengl                 # 额外 import OpenGL.GL
    minimal.exe live2d                   # 额外 import live2d.v3 并 init()
    minimal.exe surface                  # 额外设置带 alpha 的默认 QSurfaceFormat(复刻桌宠)
    minimal.exe glwidget                 # 用 QOpenGLWidget 当窗口
    minimal.exe full                     # surface + glwidget + live2d(最接近桌宠)

把结果写进 exe 同目录的 probe.log(逐行 flush,崩溃也不丢信息)。
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
LOG = APP_DIR / "probe.log"


def w(message: str) -> None:
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")
    try:
        print(message, flush=True)
    except Exception:
        pass


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "plain"
    w(f"--- mode={mode} frozen={bool(getattr(sys, 'frozen', False))} python={sys.version.split()[0]} ---")
    use_surface = mode in ("surface", "full")
    use_gl = mode in ("glwidget", "full")
    use_live2d = mode in ("live2d", "full")

    if mode == "oured":
        # 逐个导入桌宠自己的模块,定位哪个 import 的副作用会破坏 Qt 创建窗口
        import importlib

        project_root = APP_DIR if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        for name in ("pet", "pet.config", "pet.actions", "pet.applog", "pet.win32",
                     "pet.chat", "pet.bubble", "pet.hotkey", "pet.model", "pet.window"):
            try:
                importlib.import_module(name)
                w(f"导入 {name} 完成")
            except Exception as exc:
                w(f"导入 {name} 失败: {exc!r}")
        use_surface = use_gl = True   # 桌宠本体会用到这两样

    if mode in ("pyopengl", "full", "oured"):
        import OpenGL.GL  # noqa: F401
        w("PyOpenGL 导入完成")

    if use_live2d or mode == "oured":
        import live2d.v3 as live2d
        w("live2d.v3 导入完成")
        live2d.init()
        w("live2d.init() 完成")

    if use_surface:
        # 复刻桌宠:_configure_surface_format() 在 QApplication 之前设默认格式
        from PySide6.QtGui import QSurfaceFormat
        fmt = QSurfaceFormat.defaultFormat()
        fmt.setAlphaBufferSize(8)
        QSurfaceFormat.setDefaultFormat(fmt)
        w("已设置默认 QSurfaceFormat(alphaBufferSize=8)")

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication(sys.argv[:1])
    w("QApplication 创建完成")

    if use_gl:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget

        class GLWindow(QOpenGLWidget):
            def initializeGL(self) -> None:
                w("QOpenGLWidget.initializeGL 进入")

        window = GLWindow()
        w("QOpenGLWidget 构造完成")
    else:
        window = QWidget()
        w("QWidget 构造完成")

    window.resize(200, 120)
    window.show()
    w("show() 完成 ← 关键节点")

    QTimer.singleShot(1200, app.quit)
    app.exec()
    w("正常退出")

    if use_live2d:
        live2d.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
