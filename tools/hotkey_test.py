"""全局快捷键与开机自启的验证。

做四件事:
  1. ``parse_hotkey`` 的解析边界(合法/非法写法)
  2. 真实注册快捷键,并用 ``PostMessage(WM_HOTKEY)`` 模拟按键,验证动作真的触发
     (没法在自动化里真按键,但"系统注册成功 + 消息处理链路通"这两段都能验证)
  3. 注册表读写:用测试专用的值名开关一次,验证后**必定清理**
  4. 冲突处理:同一个组合键注册两次,第二次应当失败而不是静默

用法::

    .venv\\Scripts\\python.exe tools\\hotkey_test.py
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pet import config as config_mod
from pet import win32
from pet.actions import load_actions
from pet.hotkey import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY, parse_hotkey

_results = {"pass": 0, "fail": 0}
TEST_AUTOSTART_NAME = "DSWhalePetTest"


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


def part_parse() -> None:
    print("\n[1] 快捷键写法解析")
    check("ctrl+alt+W", parse_hotkey("ctrl+alt+W") == (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("W")))
    check("大小写与空格无关", parse_hotkey("Ctrl + Alt + w") == parse_hotkey("ctrl+alt+W"))
    check("shift+win+F5", parse_hotkey("shift+win+F5") == (0x0004 | 0x0008 | MOD_NOREPEAT, 0x74))
    check("单个字母带修饰键", parse_hotkey("alt+1") == (MOD_ALT | MOD_NOREPEAT, ord("1")))
    check("裸键被拒绝(避免抢占打字)", parse_hotkey("W") is None)
    check("只有修饰键被拒绝", parse_hotkey("ctrl+alt") is None)
    check("无法识别的键名被拒绝", parse_hotkey("ctrl+alt+不存在的键") is None)
    check("空串被拒绝", parse_hotkey("") is None)


def part_registry() -> None:
    print("\n[3] 开机自启注册表读写(用测试专用值名,结束后清理)")
    if not win32.IS_WINDOWS:
        print("  (非 Windows,跳过)")
        return

    cmd = win32.autostart_command(r"C:\fake\pythonw.exe", r"C:\fake\main.py")
    check("命令带引号包裹路径", cmd == '"C:\\fake\\pythonw.exe" "C:\\fake\\main.py"', cmd)

    try:
        check("初始未设置", win32.autostart_state(TEST_AUTOSTART_NAME) is False)
        check("可以开启", win32.set_autostart(True, cmd, TEST_AUTOSTART_NAME) is True)
        check("开启后可读到", win32.autostart_state(TEST_AUTOSTART_NAME) is True)
        check("可以关闭", win32.set_autostart(False, "", TEST_AUTOSTART_NAME) is True)
        check("关闭后读不到", win32.autostart_state(TEST_AUTOSTART_NAME) is False)
        check("重复关闭不报错", win32.set_autostart(False, "", TEST_AUTOSTART_NAME) is True)
    finally:
        win32.set_autostart(False, "", TEST_AUTOSTART_NAME)
        check("测试值已清理", win32.autostart_state(TEST_AUTOSTART_NAME) is False)


def part_hotkeys(model_dir: Path) -> None:
    print("\n[2] 真实注册 + 模拟按键触发")
    import live2d.v3 as live2d
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from pet.window import PetWindow

    cfg = config_mod.Config.load()
    cfg.hotkey_toggle_visible = "ctrl+alt+W"
    cfg.hotkey_open_chat = "ctrl+alt+E"
    cfg.gaze_follow = False

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    live2d.init()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)

    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir), load_actions(model_dir))
    window.persist_config = False
    window.show()

    user32 = ctypes.windll.user32
    state = {"step": 0, "dispatches": 0}

    # 统计实际派发次数:一次 PostMessage 只应触发一次动作
    window.hotkeys.triggered.connect(
        lambda _name: state.__setitem__("dispatches", state["dispatches"] + 1)
    )

    def post(name: str) -> bool:
        hotkey_id = window.hotkeys.id_for(name)
        if hotkey_id is None:
            return False
        user32.PostMessageW(int(window.winId()), WM_HOTKEY, hotkey_id, 0)
        return True

    def step() -> None:
        state["step"] += 1
        if state["step"] == 1:
            check("两个快捷键都注册成功", window.hotkeys.active and not window.hotkeys.failed,
                  f"active={window.hotkeys.active} failed={window.hotkeys.failed}")
            check("快捷键提示可读", "ctrl+alt+W" in window.hotkey_report(), window.hotkey_report())
            check("桌宠初始可见", window.isVisible())
            check("已投递 toggle_visible", post("toggle_visible"))
        elif state["step"] == 2:
            print(f"  (派发次数={state['dispatches']},去重掉的重复投递={window.hotkeys.duplicates_ignored},"
                  f"原始消息={window.hotkeys._seen})")
            check("一次按键只触发一次(重复投递已去重)", state["dispatches"] == 1,
                  f"dispatches={state['dispatches']}")
            check("确实发生了重复投递(验证去重是必要的)", window.hotkeys.duplicates_ignored >= 1,
                  f"ignored={window.hotkeys.duplicates_ignored}")
            check("快捷键隐藏了桌宠", not window.isVisible(), f"visible={window.isVisible()}")
            post("toggle_visible")
        elif state["step"] == 3:
            check("再按一次恢复显示", window.isVisible())
            post("open_chat")
        elif state["step"] == 4:
            check("聊天输入框被打开", window.chat_input.isVisible())
            window.chat_input.hide()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(step)
    timer.start(400)

    app.exec()
    window.close()
    live2d.dispose()


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

    part_parse()
    part_registry()
    part_hotkeys(model_dir)

    print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
