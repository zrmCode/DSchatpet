"""窗口层级回归:设置面板打开时,聊天气泡与输入框**不许**盖在它上面。

用户反馈的原话:「聊天框图层在其他地方没问题,但会在本应用设置窗口上遮挡」。

成因(实测):桌宠窗口是 ``WS_EX_TOPMOST``,气泡 / 输入框 / 设置面板都是它的
**附属窗口(owned window)**,四者同处置顶带;置顶带内部谁最后被抬起谁在上面 ——
面板只在你点击它时才回到最上,而 ``bubble.show_text()`` / ``chat_input.show_passive()``
里的 ``raise_()`` 会把聊天窗口抬到面板上面。

因此本测试**不看代码怎么写的,只看屏幕上的真实 z 顺序**:
用 Win32 ``GetTopWindow`` / ``GetWindow(GW_HWNDNEXT)`` 枚举顶层窗口,
断言"面板的序号小于气泡与输入框的序号"。

用法::

    .venv\\Scripts\\python.exe tools\\zorder_test.py
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SANDBOX = ROOT / ".tmp" / "zorder_test"

user32 = ctypes.windll.user32
user32.GetTopWindow.restype = wintypes.HWND
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.restype = ctypes.c_long
GW_HWNDNEXT = 2
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008

_results = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


def z_order() -> list[int]:
    """从最上层到最下层枚举顶层窗口 HWND。"""
    result: list[int] = []
    hwnd = user32.GetTopWindow(None)
    for _ in range(4000):
        if not hwnd:
            break
        result.append(int(hwnd))
        hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
    return result


def is_topmost(hwnd: int) -> bool:
    return bool(user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE) & WS_EX_TOPMOST)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import live2d.v3 as live2d
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from pet import config as config_mod
    from pet.actions import load_actions
    from pet.memory import HistoryStore, MemoryStore, Profile
    from pet.window import PetWindow

    SANDBOX.mkdir(parents=True, exist_ok=True)
    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    live2d.init()
    app = QApplication(sys.argv[:1])

    cfg = config_mod.Config.load()
    cfg.window_x = 220
    cfg.window_y = 220
    cfg.always_on_top = True          # 被测的就是"置顶时也不许遮挡面板"
    cfg.gaze_follow = False
    cfg.idle_motion = False
    cfg.idle_autonomy = False
    cfg.idle_llm_thoughts = False
    cfg.poke_reaction = False
    cfg.chat_hover = True

    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("本机没有模型,跳过(见 assets/README.md)")
        return 0
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir),
                       load_actions(model_dir))
    window.persist_config = False
    window.profile_path = SANDBOX / "profile.json"
    window.profile = Profile()
    window.memories = MemoryStore(SANDBOX / "memories.jsonl", cap=50)
    window.history = HistoryStore(SANDBOX / "history.jsonl")
    window.show()

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    pump(4.0)
    window.input_timer.stop()          # 悬停用合成调用,不受真实鼠标影响

    pet = int(window.winId())
    bubble = int(window.bubble.winId())
    chat_in = int(window.chat_input.winId())
    print("\n[1] 基线:置顶开着、没有面板")
    check("桌宠窗口是置顶的(前置条件)", is_topmost(pet),
          f"exstyle=0x{user32.GetWindowLongW(wintypes.HWND(pet), GWL_EXSTYLE) & 0xFFFFFFFF:X}")
    check("没有面板时为非让位状态", not window.dialogs_open())

    print("\n[2] 打开设置面板 → 桌宠必须让出置顶")
    dialog = window.make_settings_dialog()
    dialog.show()
    dialog.activateWindow()
    dialog.raise_()
    pump(1.2)
    dlg = int(dialog.winId())
    check("面板已登记为打开", window.dialogs_open())
    check("面板是置顶的", is_topmost(dlg))
    check("桌宠已取消置顶", not is_topmost(pet))

    print("\n[3] 面板开着时桌宠说话 → 气泡不许盖住面板")
    window.bubble.show_text("改设置的时候我说两句,看看会不会盖住面板哦~",
                            window.frameGeometry(), 0)
    pump(0.8)
    order = z_order()
    pet_i, bub_i, in_i, dlg_i = (order.index(h) if h in order else None
                                 for h in (pet, bubble, chat_in, dlg))
    print(f"  (z 序号: 桌宠={pet_i} 气泡={bub_i} 输入框={in_i} 面板={dlg_i})")
    check("气泡不在面板上面", bub_i is not None and dlg_i is not None and bub_i > dlg_i,
          f"气泡={bub_i} 面板={dlg_i}")
    check("桌宠本体也不在面板上面", pet_i is not None and dlg_i is not None and pet_i > dlg_i,
          f"桌宠={pet_i} 面板={dlg_i}")
    check("气泡仍然正常显示(不是靠隐藏来规避)", window.bubble.isVisible())

    print("\n[4] 面板开着时鼠标靠近 → 不弹输入框")
    rect = window.frameGeometry()
    from PySide6.QtCore import QPoint

    window._update_chat_visibility(QPoint(rect.left() - 20, rect.center().y()))
    pump(0.6)
    check("输入框没有被弹出来", not window.chat_input.isVisible())

    print("\n[5] 面板开着时双击/快捷键想聊天 → 不抢面板焦点")
    window.open_chat()
    pump(0.4)
    check("输入框仍然没弹", not window.chat_input.isVisible())

    print("\n[6] 面板开着时反复 raise_() 也盖不住(模拟 show_passive/open_at 的抬升)")
    for _ in range(3):
        window.chat_input.show_passive(window.frameGeometry())
        window.bubble.raise_()
        window.chat_input.raise_()
        pump(0.25)
    order = z_order()
    bub_i = order.index(bubble) if bubble in order else None
    in_i = order.index(chat_in) if chat_in in order else None
    dlg_i = order.index(dlg) if dlg in order else None
    print(f"  (z 序号: 气泡={bub_i} 输入框={in_i} 面板={dlg_i})")
    check("强行 raise_() 之后气泡仍在面板下面", bub_i is not None and dlg_i is not None and bub_i > dlg_i,
          f"气泡={bub_i} 面板={dlg_i}")
    check("强行 raise_() 之后输入框仍在面板下面",
          in_i is not None and dlg_i is not None and in_i > dlg_i, f"输入框={in_i} 面板={dlg_i}")

    print("\n[7] 关掉面板 → 恢复置顶与悬停")
    window.chat_input.hide()
    window.bubble.hide()
    dialog.close()
    pump(1.0)
    check("面板关闭后状态复位", not window.dialogs_open())
    check("桌宠恢复置顶", is_topmost(pet))
    window._update_chat_visibility(QPoint(rect.left() - 20, rect.center().y()))
    pump(0.6)
    print(f"  (输入框可见={window.chat_input.isVisible()};本机没配对话后端时不弹是正常的)")
    if window.chat_available():
        check("关闭面板后鼠标靠近又能弹出输入框", window.chat_input.isVisible())
    else:
        check("没有对话后端时仍不弹输入框(设计如此)", not window.chat_input.isVisible())

    print("\n[8] 用户在菜单里关掉置顶时,面板关闭后不许擅自恢复置顶")
    window.set_always_on_top(False)
    dialog2 = window.make_settings_dialog()
    dialog2.show()
    pump(0.8)
    check("面板打开时桌宠非置顶", not is_topmost(pet))
    dialog2.close()
    pump(0.8)
    check("面板关闭后仍是用户设定的非置顶", not is_topmost(pet))
    check("状态集合已清空", not window.dialogs_open())

    print("\n[9] 置顶开关必须真的生效(曾经因为 ctypes 传负值伪句柄而静默失效)")
    window.set_always_on_top(True)
    pump(0.4)
    check("打开置顶 → 窗口真的置顶", is_topmost(pet))
    window.set_always_on_top(False)
    pump(0.4)
    check("关掉置顶 → 窗口真的取消置顶", not is_topmost(pet))
    window.set_always_on_top(True)
    pump(0.4)
    check("再打开又能置顶", is_topmost(pet))
    check("配置值同步", cfg.always_on_top is True)

    window.hide()
    window.close()
    app.quit()
    print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
