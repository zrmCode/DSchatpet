"""DS鲸鱼娘桌宠 —— 程序入口。

用法::

    python main.py                      # 正常启动
    python main.py --selftest           # 自检:显示几秒,打印诊断后自动退出
    python main.py --model-dir <目录>   # 指定模型目录
    python main.py --no-tray            # 不创建托盘图标(调试用)
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import live2d.v3 as live2d
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QSurfaceFormat
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from pet import config as config_mod
from pet import win32
from pet.actions import load_actions
from pet.single_instance import SingleInstance
from pet.window import PetWindow, autostart_command

#: --selftest 的停留秒数
SELFTEST_SECONDS = 6

#: 崩溃日志。用 pythonw / 打包后的 exe 启动时没有控制台,异常必须落盘才查得到
LOG_PATH = config_mod.APP_DIR / "pet.log"


def _fix_console_encoding() -> None:
    """Windows 控制台默认是 GBK,打印中文诊断信息容易炸,统一切到 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _log(text: str) -> None:
    from pet.applog import log
    log(text)


def _install_crash_logger() -> None:
    """把未捕获异常与 Qt 槽里的异常都写进 pet.log。"""

    def excepthook(exc_type, exc, tb) -> None:
        _log("未捕获异常:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)

    def unraisablehook(unraisable) -> None:
        # Qt 在绘制/槽函数里抛的异常会走这里(比如 paintGL 报错)
        exc = unraisable.exc_value
        _log(f"未处理异常({unraisable.object!r}):\n"
             + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

    sys.excepthook = excepthook
    sys.unraisablehook = unraisablehook


def _configure_surface_format() -> None:
    """桌宠窗口需要 alpha 通道才能真透明。"""
    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)


def _run_qt_probe(app: QApplication, cfg, model_dir: Path, model_json: Path, actions) -> int:
    """分步探针:依次验证 普通窗口 -> 空 OpenGL 窗口 -> 完整桌宠,定位崩溃环节。"""
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtWidgets import QLabel, QWidget

    # 1. 普通窗口
    plain = QWidget()
    plain.setWindowTitle("probe-plain")
    plain.resize(200, 120)
    layout = plain.layout() if plain.layout() else None
    plain.show()
    _log("探针1:普通 QWidget show 完成")
    app.processEvents()
    plain.close()
    _log("探针1:普通 QWidget close 完成")

    # 2. 空 OpenGL 窗口(不碰 live2d)
    class EmptyGL(QOpenGLWidget):
        def initializeGL(self) -> None:
            _log("探针2:空 GL initializeGL 进入")
        def paintGL(self) -> None:
            pass

    gl_widget = EmptyGL()
    gl_widget.setWindowTitle("probe-gl")
    gl_widget.resize(240, 240)
    gl_widget.show()
    _log("探针2:空 QOpenGLWidget show 完成")
    app.processEvents()
    _log("探针2:空 QOpenGLWidget 事件处理完成")
    gl_widget.close()
    _log("探针2:空 QOpenGLWidget close 完成")

    # 3. 完整桌宠窗口
    window = PetWindow(cfg, model_dir, model_json, actions)
    window.persist_config = False
    window.show()
    _log("探针3:PetWindow show 完成")
    app.processEvents()
    _log("探针3:PetWindow 事件处理完成")
    QTimer.singleShot(2000, app.quit)
    return app.exec()


def build_tray(app: QApplication, window: PetWindow, icon_path: Path) -> QSystemTrayIcon:
    """托盘图标:显示/隐藏、完整动作菜单、退出。"""
    icon = QIcon(str(icon_path)) if icon_path.is_file() else QIcon()
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("DS鲸鱼娘 桌宠")

    menu = QMenu()
    menu.addAction("显示 / 隐藏", window.toggle_visible)

    action_menu = window.build_menu(menu)
    action_menu.setTitle("动作与设置")
    menu.addMenu(action_menu)

    menu.addSeparator()
    menu.addAction("退出", app.quit)

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: window.toggle_visible()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    tray.show()
    return tray


def _explain_missing_model() -> None:
    """模型缺失时的启动引导。

    这是**开源版最常见的第一次运行**:仓库里不含模型(版权归原作者,只随 Release
    压缩包提供),所以别人 clone 之后必须自己把模型放进来。双击运行时没有控制台,
    只往 stderr 打一行等于"闪退什么也不说",所以这里必须弹个原生消息框说清楚。
    """
    from pet import win32

    target = config_mod.APP_DIR / "assets" / "model"
    hint = (f"没有找到 Live2D 模型文件。\n\n"
            f"本程序不包含模型 —— 模型版权归原作者 B站@氵六青 所有,\n"
            f"只随本项目的 Release 免安装压缩包提供(禁止出售)。\n\n"
            f"请把模型目录放到:\n{target}\n"
            f"(该目录下需要有 *.model3.json 以及 moc3 / 贴图 / physics3.json 等文件)\n\n"
            f"点「确定」打开这个目录;也可以用命令行参数 --model-dir <目录> 指定别处。")
    print("找不到模型目录(需要包含 *.model3.json),可用 --model-dir 指定", file=sys.stderr)
    print(f"请把模型放到:{target}", file=sys.stderr)

    #: 有控制台时上面两行已经说清了;没有控制台(pythonw / 打包版)才需要弹窗
    if sys.stdout is not None and not getattr(sys, "frozen", False):
        return
    if win32.message_box(hint, "DS鲸鱼娘桌宠 —— 缺少模型文件",
                         win32.MB_OKCANCEL | win32.MB_ICONINFORMATION) == win32.IDOK:
        win32.open_in_explorer(str(target))


def main(argv: list[str] | None = None) -> int:
    _fix_console_encoding()
    _install_crash_logger()

    parser = argparse.ArgumentParser(description="DS鲸鱼娘桌宠")
    parser.add_argument("--selftest", action="store_true", help="显示数秒后自动退出并打印诊断")
    parser.add_argument("--model-dir", default=None, help="指定模型目录")
    parser.add_argument("--no-tray", action="store_true", help="不创建托盘图标")
    parser.add_argument("--qt-probe", action="store_true", help="分步探针:定位 Qt/OpenGL/live2d 哪一步崩")
    args = parser.parse_args(argv)

    cfg = config_mod.Config.load()
    _log(f"启动:frozen={bool(getattr(sys, 'frozen', False))} 可写目录={config_mod.APP_DIR}")
    model_dir = config_mod.find_model_dir(args.model_dir or cfg.model_dir or None)
    if model_dir is None:
        _explain_missing_model()
        _log("失败:找不到模型目录")
        return 2
    model_json = config_mod.find_model_json(model_dir)
    actions = load_actions(model_dir)
    _log(f"模型:{model_dir} / {model_json.name},动作 {len(actions)} 条")

    print(f"[启动] 模型目录: {model_dir}")
    print(f"[启动] 模型文件: {model_json.name}")
    print(f"[启动] 动作目录: {len(actions)} 条(表情/动画/归位来自 vtube.json)")
    print(f"[启动] 快捷键  : {cfg.hotkey_toggle_visible} 显示/隐藏、"
          f"{cfg.hotkey_open_chat} 聊天")

    _configure_surface_format()
    live2d.init()
    _log("live2d.init() 完成")

    # 原生崩溃(SIGSEGV / abort)时把各线程栈dump出来;窗口程序没有控制台,写文件
    try:
        import faulthandler
        _fatal = open(config_mod.APP_DIR / "pet-fatal.log", "a", encoding="utf-8")
        faulthandler.enable(file=_fatal, all_threads=True)
    except (OSError, ImportError):
        pass

    _log("创建 QApplication …")
    app = QApplication(sys.argv[:1])
    _log("QApplication 创建完成")
    app.setQuitOnLastWindowClosed(False)   # 托盘常驻,关窗不等于退出

    # 单实例保护:两只重叠的桌宠会表现为"眼睛永久重影"(点表情只有前景那只变)
    guard = SingleInstance(config_mod.APP_DIR / "pet.lock")
    if guard.warning:
        _log(f"单实例检查:{guard.warning}")
    if not guard.is_primary:
        pid = f"(PID {guard.other_pid})" if guard.other_pid else ""
        _log(f"已有实例在运行{pid},本进程退出(避免出现两只重叠的桌宠)")
        QMessageBox.information(
            None, "DS鲸鱼娘桌宠", "桌宠已经在运行啦～\n在托盘图标里可以找到它。",
        )
        return 0
    _log("单实例检查通过")

    if args.qt_probe:
        code = _run_qt_probe(app, cfg, model_dir, model_json, actions)
        live2d.dispose()
        return code

    icon_path = model_dir / "icon.png"
    _log("创建桌宠窗口对象 …")
    window = PetWindow(cfg, model_dir, model_json, actions)
    _log("窗口对象创建完成")
    if icon_path.is_file():
        window.setWindowIcon(QIcon(str(icon_path)))
    _log("准备 show() …")
    window.show()
    _log("窗口已创建并显示")

    # 项目挪过位置时,让注册表里的自启命令跟着更新
    if cfg.autostart and win32.IS_WINDOWS and not win32.autostart_state():
        win32.set_autostart(True, autostart_command())

    tray = None
    if not args.no_tray and QSystemTrayIcon.isSystemTrayAvailable():
        tray = build_tray(app, window, icon_path)
    else:
        app.setQuitOnLastWindowClosed(True)

    if args.selftest:
        def _finish() -> None:
            print("[自检] 诊断信息:")
            parts = []
            for key, value in window.diagnostics().items():
                print(f"        {key}: {value}")
                parts.append(f"{key}={value}")
            # 打包成 exe 后没有控制台,自检结果同时写进 pet.log 便于外部校验
            _log("[自检] " + " | ".join(parts))
            app.quit()

        QTimer.singleShot(SELFTEST_SECONDS * 1000, _finish)

    code = app.exec()
    window.close()
    live2d.dispose()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
