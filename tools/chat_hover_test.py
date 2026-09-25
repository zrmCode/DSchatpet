"""聊天输入框新行为的验证:锚定下方 / 跟随拖动 / 悬停出现不抢焦点 / 提示文本。

对应用户要求的四条:
  1. 桌宠整体缩到 2/3(配置默认 window_height 373)
  2. 输入框固定在桌宠**下方不远处**,并**跟随拖动**
  3. 鼠标**靠近时**输入框才出现(不抢焦点)
  4. 输入框提示文本为「聊天…」

悬停判定被设计成可以传合成坐标(``_update_chat_visibility(cursor)``),
所以本测试**不需要真的移动鼠标**。

⚠️ 但应用每 50ms 会拿**真实鼠标位置**做一次悬停判定,会覆盖合成坐标的结果
(真实光标通常离桌宠很远 → 输入框被自动隐藏)。因此测试开始时会**停掉输入轮询定时器**,
只让合成调用生效。

⚠️ 另外:如果你**测试期间正在用鼠标点/拖桌宠**(桌宠是置顶的,很容易被压到),
`_dragging` 会为真,此时按设计会**暂停悬停判定**,合成调用会被忽略 ——
偶发的"重新靠近又出现"失败多半是这个原因。失败信息里已经带上
`dragging=` / `available=` / `桌宠可见=` 等状态,可直接判定。

用法::

    .venv\\Scripts\\python.exe tools\\chat_hover_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live2d.v3 as live2d
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from pet import config as config_mod
from pet.actions import load_actions
from pet.window import CHAT_HIDE_DELAY_MS, PetWindow, _distance_to_rect

_results = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    cfg = config_mod.Config.load()
    cfg.gaze_follow = False
    cfg.idle_motion = False
    cfg.poke_reaction = False
    cfg.chat_hover = True
    cfg.chat_hover_distance = 60

    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("找不到模型目录")
        return 2

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    live2d.init()
    app = QApplication(sys.argv[:1])
    # ⚠️ 不能沿用用户保存的窗口位置:用户当前把桌宠停在 y=454,任务栏又占掉屏幕底部,
    # 那个位置下方放不下输入框,会让「下方且贴近」这条断言测的其实是贴边情形。
    # 这里显式把桌宠放到屏幕中上部,保证下方有空间(必须在 QApplication 之后再取屏幕)。
    screen = app.primaryScreen().availableGeometry()
    cfg.window_x = screen.left() + 120
    cfg.window_y = screen.top() + 80
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir), load_actions(model_dir))
    window.persist_config = False        # 绝不写回用户的 config.json
    window.show()
    # show() 会触发 initializeGL 创建输入轮询定时器;在事件循环开始前停掉它,
    # 否则真实鼠标位置可能先把输入框弹出来,污染"初始不可见"等断言
    window.input_timer.stop()

    far = QPoint(-5000, -5000)           # 很远的点,确保"不在附近"
    state = {"step": 0}

    def near_point() -> QPoint:
        """桌宠矩形外、但距离小于阈值的一点(从左边缘向左 20px)。"""
        rect = window.frameGeometry()
        return QPoint(rect.left() - 20, rect.center().y())

    def step() -> None:
        state["step"] += 1
        n = state["step"]
        rect = window.frameGeometry()
        chat = window.chat_input

        if n == 1:
            print("\n[1] 尺寸(缩到 2/3)")
            print("  (输入轮询已在 show 后立即停掉,只让合成坐标生效)")
            check("窗口高度 = 373", window.height() == 373, str(window.height()))
            check("窗口宽度 = 373(画布 1:1)", window.width() == 373, str(window.width()))

        elif n == 2:
            print("\n[2] 提示文本、配色与层级")
            check("提示文本为「聊天…」", chat.edit.placeholderText() == "聊天…",
                  repr(chat.edit.placeholderText()))
            check("tooltip 里写了用法", "回车" in chat.edit.toolTip(), chat.edit.toolTip())
            qss = chat.edit.styleSheet()
            check("输入框是天蓝色背景", "126, 200, 246" in qss, qss.splitlines()[1].strip())
            check("天蓝底上用的是深色字", "#06283d" in qss, "未找到深色前景")
            flags = chat.windowFlags()
            check("输入框**不再单独置顶一层**(与桌宠同层)",
                  not bool(flags & Qt.WindowType.WindowStaysOnTopHint), str(flags))
            check("气泡也不再单独置顶",
                  not bool(window.bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
            check("输入框是桌宠的附属窗口",
                  chat.parent() is window and window.bubble.parent() is window)
            check("初始不可见", not chat.isVisible())

        elif n == 3:
            print("\n[3] 鼠标靠近 → 出现")
            window._update_chat_visibility(near_point())
            check("靠近后输入框出现", chat.isVisible())
            check("出现时**不抢焦点**", not chat.edit.hasFocus(), "焦点被抢走了")
            check("出现时没有内容", chat.edit.text() == "")

        elif n == 4:
            print("\n[4] 位置:紧贴桌宠下方不远处并水平居中")
            gap = chat.y() - rect.bottom()
            center_offset = abs((chat.x() + chat.width() // 2) - rect.center().x())
            check(f"在下方且贴近(间距 {gap}px,配置值 {cfg.chat_input_gap})",
                  gap == cfg.chat_input_gap, f"gap={gap}")
            check(f"水平居中对齐(偏移 {center_offset}px)", center_offset <= 4,
                  f"offset={center_offset}")

        elif n == 5:
            print("\n[5] 拖动桌宠 → 输入框跟随")
            before = (chat.x(), chat.y())
            window.move(window.x() - 120, window.y() - 60)
            app.processEvents()
            after = (chat.x(), chat.y())
            moved = (after[0] - before[0], after[1] - before[1])
            check("输入框跟着移动了(-120, -60)", moved == (-120, -60), f"实际 {moved}")
            gap = chat.y() - window.frameGeometry().bottom()
            check("跟随之后仍保持同样的贴紧间距", gap == cfg.chat_input_gap, f"gap={gap}")

        elif n == 6:
            print("\n[6] 桌宠贴到屏幕底部时,输入框仍在**下方**(贴底边,不翻到上方)")
            geo = QApplication.primaryScreen().availableGeometry()
            window.move(window.x(), geo.bottom() - window.height() - 2)
            app.processEvents()
            chat_bottom = window.chat_input
            chat_bottom._place_below(window.frameGeometry())
            app.processEvents()
            rect = window.frameGeometry()
            check("没有被翻到桌宠上方", chat_bottom.y() >= rect.top(),
                  f"输入框 y={chat_bottom.y()} 桌宠 top={rect.top()}")
            check("仍在屏幕可用区内(不压到任务栏)",
                  chat_bottom.y() + chat_bottom.height() <= geo.bottom(),
                  f"底边 {chat_bottom.y() + chat_bottom.height()} 屏幕底 {geo.bottom()}")
            check("水平仍然居中", abs((chat_bottom.x() + chat_bottom.width() // 2)
                                   - rect.center().x()) <= 4)

        elif n == 7:
            print("\n[7] 鼠标远离 → 延迟隐藏")
            # 把延迟调短,便于在本测试的步进节奏里稳定观察
            window._chat_hide_delay_ms = 150
            window._update_chat_visibility(far)
            check("远离的瞬间仍可见(在延迟窗口内)", chat.isVisible())

        elif n == 8:
            check("延迟结束后已自动隐藏", not chat.isVisible(), "仍然可见")

        elif n == 9:
            print("\n[8] 正在输入时不该被隐藏")
            near = near_point()
            window._update_chat_visibility(near)
            rect2 = window.frameGeometry()
            detail = (f"距离={_distance_to_rect(near, rect2):.0f} "
                      f"available={window.chat_available()} "
                      f"桌宠可见={window.isVisible()} hover={cfg.chat_hover} "
                      f"dragging={window._dragging} 窗口=({rect2.x()},{rect2.y()},{rect2.width()}x{rect2.height()})")
            check("重新靠近又出现", chat.isVisible(), detail)
            chat.edit.setText("我在打字")
            window._update_chat_visibility(far)
            check("有内容时远离仍然保留", chat.isVisible(), detail)
            chat.edit.clear()
            chat.hide()

        elif n == 10:
            print("\n[9] 双击/菜单打开时要聚焦(方便直接打字)")
            window.open_chat()
            check("open_chat 后可见", chat.isVisible())
            focused = chat.edit.hasFocus()
            print(f"  (焦点状态:{focused};无窗口管理器的环境下可能为 False,不算失败)")
            chat.hide()

        elif n == 11:
            print("\n[10] 位置夹紧:窗口不会被放到屏幕外")
            window.move(-9000, -9000)
            app.processEvents()
            window._clamp_to_screen()
            screen = QApplication.primaryScreen().availableGeometry()
            inside = (screen.left() <= window.x() and screen.top() <= window.y()
                      and window.x() + window.width() <= screen.right() + 1
                      and window.y() + window.height() <= screen.bottom() + 1)
            check("夹紧后完全在屏幕内", inside,
                  f"窗口 ({window.x()},{window.y()}) 屏幕 {screen}")

        elif n == 12:
            print("\n[11] 与桌宠同层:桌宠隐藏时输入框/气泡一起隐藏")
            rect = window.frameGeometry()
            window._update_chat_visibility(QPoint(rect.left() - 20, rect.center().y()))
            bubble_visible = window.bubble.isVisible()
            window.bubble.show_text("测试", rect, 30)
            check("输入框此刻可见", chat.isVisible())
            window.toggle_visible()          # 隐藏桌宠
            app.processEvents()
            check("桌宠隐藏后输入框也隐藏", not chat.isVisible(),
                  f"chat visible={chat.isVisible()}")
            check("桌宠隐藏后气泡也隐藏", not window.bubble.isVisible())
            window.toggle_visible()          # 恢复
            app.processEvents()
            check("恢复显示后桌宠可见", window.isVisible())

        elif n == 13:
            print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
            app.quit()

    timer = QTimer()
    timer.timeout.connect(step)
    timer.start(600)
    QTimer.singleShot(60_000, app.quit)

    app.exec()
    window.close()
    live2d.dispose()
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
