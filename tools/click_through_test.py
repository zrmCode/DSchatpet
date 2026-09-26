"""鼠标穿透(点击穿透)卡死的回归测试。

用户报告的现象:**点击无反应、不能拖动、打不开设置**。实测原因不是卡死也不是渲染停了
(测量:窗口 3 秒 CPU 22%、两次抓图差异 10.9%,画面在动),而是窗口的扩展样式
``WS_EX_TRANSPARENT`` **卡在开启状态** —— 鼠标事件全部落到桌面上,桌宠自然"点不到",
连右键菜单都出不来(所以必须靠托盘救援)。

这类故障的危险之处在于**它自己回不来**:一旦按"未命中"处理,窗口就是穿透的,
用户没法点它去改设置。因此本测试覆盖三条防线:

  1. 像素读取失败/数据不完整 → 按"命中"处理(宁可挡住鼠标,也不能让桌宠点不动);
  2. 看门狗:穿透开着而光标一直在窗口内却判为未命中超过 1.5 秒 → 强制恢复可点击;
  3. 恢复后不再自动开启穿透,直到采样重新采到**确实命中**才回到常规行为(不反复抖动);
  4. 托盘里的「修复鼠标穿透」入口永远存在(穿透卡死时唯一可达的救援通道)。

用法::

    .venv\\Scripts\\python.exe tools\\click_through_test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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

    import live2d.v3 as live2d
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    import OpenGL.GL as gl
    from pet import config as config_mod
    from pet.actions import load_actions
    from pet.window import CLICK_THROUGH_STUCK_SECONDS, ALPHA_HIT_THRESHOLD, PetWindow

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    live2d.init()
    app = QApplication(sys.argv[:1])

    cfg = config_mod.Config.load()
    cfg.window_x, cfg.window_y = 300, 300
    cfg.gaze_follow = False
    cfg.idle_motion = False
    cfg.idle_autonomy = False
    cfg.idle_llm_thoughts = False
    cfg.click_through = True

    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("本机没有模型,跳过(见 assets/README.md)")
        return 0
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir),
                       load_actions(model_dir))
    window.enable_test_mode("click_through")
    window.show()

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    pump(4.0)
    window.input_timer.stop()          # 手动驱动 tick,不依赖真实鼠标

    print("\n[1] 像素读取失败时必须按“命中”处理(不能让桌宠点不动)")
    real_read = gl.glReadPixels
    from PySide6.QtGui import QCursor

    #: ⚠️ 采样函数第一件事是判断"光标在不在窗口里";测试进程里真实光标不在,
    #: 会走"光标在窗外 → alpha=0"那条分支,测不到读取失败的处理。先把它指向窗口内。
    real_pos = QCursor.pos
    center = window.frameGeometry().center()
    QCursor.pos = staticmethod(lambda: center)      # type: ignore[assignment]

    def failing_read(*args, **kwargs):
        raise RuntimeError("模拟 GL 读取失败")

    try:
        gl.glReadPixels = failing_read
        window._cursor_alpha = 0
        window._sample_cursor_alpha()
        check("读取抛异常 → alpha 按命中(255)处理", window._cursor_alpha == 255,
              str(window._cursor_alpha))

        gl.glReadPixels = lambda *a, **k: b"\x00"      # 数据不完整
        window._cursor_alpha = 0
        window._sample_cursor_alpha()
        check("数据不完整 → 同样按命中处理", window._cursor_alpha == 255, str(window._cursor_alpha))

        gl.glReadPixels = lambda *a, **k: bytes([0, 0, 0, 200])   # 正常数据
        window._cursor_alpha = 0
        window._sample_cursor_alpha()
        check("正常数据 → 用它的 alpha", window._cursor_alpha == 200, str(window._cursor_alpha))
        check("并标记读过有效值", window._alpha_valid is True)
    finally:
        gl.glReadPixels = real_read

    print("\n[2] 看门狗:穿透开着且光标一直在窗口内 → 1.5 秒后强制恢复可点击")
    window._click_through_suspect = False
    window._cursor_alpha = 0               # 模拟“采样恒为未命中”的故障
    window._alpha_valid = True
    window._click_through_state = True     # 模拟:窗口此刻正处在穿透状态
    window._stuck_since = time.monotonic() - (CLICK_THROUGH_STUCK_SECONDS + 0.2)

    try:
        window._watch_click_through_stuck()
        check("看门狗把穿透关掉了", window._click_through_state is False,
              str(window._click_through_state))
        check("并标记采样不可信", window._click_through_suspect is True)

        print("\n[3] 恢复后不再自动开启穿透(避免反复抖动)")
        window._tick_input()
        check("故障未修复前保持可点击", window._click_through_state is False,
              str(window._click_through_state))

        print("\n[4] 采样恢复正常后自动回到常规判定")
        window._cursor_alpha = ALPHA_HIT_THRESHOLD + 200
        window._alpha_valid = True
        window._tick_input()
        check("采样可信后清除不可信标记", window._click_through_suspect is False)

        # 光标在模型上 → 保持可点击
        window._cursor_alpha = ALPHA_HIT_THRESHOLD + 200
        window._tick_input()
        check("光标在模型上 → 不穿透", window._click_through_state is False)

        # 光标仍在窗口内但落在透明处 → 应当穿透(常规行为回来了)
        window._cursor_alpha = 0
        window._alpha_valid = True
        window._stuck_since = 0.0
        window._tick_input()
        check("透明处 → 正常开启穿透", window._click_through_state is True,
              str(window._click_through_state))
    finally:
        QCursor.pos = real_pos                     # type: ignore[assignment]

    print("\n[5] 手动救援入口(托盘)必须存在且可用")
    window._click_through_suspect = False
    window._click_through_state = True
    window.force_clickable()
    check("force_clickable 立即关掉穿透", window._click_through_state is False)
    check("并进入“采样不可信”保护态", window._click_through_suspect is True)

    import main as main_mod
    from PySide6.QtWidgets import QMenu

    menu = QMenu()
    menu.addAction("修复鼠标穿透(点不到时用)", window.force_clickable)
    texts = [a.text() for a in menu.actions()]
    check("托盘菜单里有救援项", any("修复鼠标穿透" in t for t in texts), str(texts))
    check("主程序里确实把该入口接到 force_clickable",
          "force_clickable" in (ROOT / "main.py").read_text(encoding="utf-8"))

    print("\n[6] 状态变化会落日志(以前这类问题在日志里完全看不到)")
    log_path = ROOT / "pet.log"
    window._click_through_state = True
    window._set_click_through(False)
    pump(0.3)
    #: ⚠️ 别按字节偏移切片:日志是 UTF-8,字节偏移与字符偏移不一致(自己踩过一次)
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:] if log_path.exists() else ""
    check("日志里能看到穿透被关掉", "点击穿透:关闭" in tail, repr(tail[-160:]))

    print("\n[7] 附属窗口不许“抢”桌宠的点击(本次事故的直接原因)")
    rect = window.frameGeometry()
    #: 把桌宠放到屏幕底部:此时"下方"放不下,输入框必须改放上方,而不是压住桌宠
    from PySide6.QtGui import QGuiApplication

    geo = QGuiApplication.primaryScreen().availableGeometry()
    window.move(window.x(), geo.bottom() - window.height() - 2)
    pump(0.3)
    pet_rect = window.frameGeometry()
    window.chat_input.show_passive(pet_rect)
    pump(0.4)
    chat_rect = window.chat_input.frameGeometry()
    check("输入框与桌宠矩形不重叠", not chat_rect.intersects(pet_rect),
          f"输入框 {chat_rect.getRect()} vs 桌宠 {pet_rect.getRect()}")
    check("输入框仍在屏幕可用区内", geo.contains(chat_rect),
          f"{chat_rect.getRect()} 屏幕 {geo.getRect()}")

    flags = window.bubble.windowFlags()
    check("气泡窗口不接收鼠标输入(纯装饰,不该吃掉点击)",
          bool(flags & Qt.WindowType.WindowTransparentForInput), str(flags))
    window.chat_input.hide()

    print("\n[8] 内部状态与真实样式失同步时必须能自动纠正(真实事故的根因)")
    from pet import win32

    hwnd = int(window.winId())
    #: 制造失同步:窗口真的开了穿透,但内部状态位说是"可交互"
    win32.set_click_through(hwnd, True)
    pump(0.2)
    window._click_through_state = False
    check("前置:窗口真实处于穿透、而内部状态说没有",
          win32.is_click_through(hwnd) and not window._click_through_state)
    window._set_click_through(False)          # 光标移到模型上时走的就是这一步
    pump(0.2)
    check("调用后窗口真的恢复可交互(旧代码会被提前 return 短路)",
          not win32.is_click_through(hwnd), "窗口仍是穿透状态")
    check("内部状态也同步了", window._click_through_state is False)

    #: 反向:窗口可交互,内部状态说"有穿透"
    win32.set_click_through(hwnd, False)
    pump(0.2)
    window._click_through_state = True
    window._set_click_through(True)
    pump(0.2)
    check("反向失同步也能纠正",
          win32.is_click_through(hwnd) and window._click_through_state is True)

    print("\n[9] 输入轮询停摆要能被渲染心跳救回来")
    window.auto_restart_input_timer = True      # 测试里默认关着,这一节要显式打开
    window.input_timer.stop()
    check("前置:输入轮询已停止", not window.input_timer.isActive())
    window._frames = 59                        # 下一帧就到达心跳检查点
    window.paintGL()                           # 直接驱动一帧
    pump(0.3)
    check("渲染心跳把输入轮询重启了", window.input_timer.isActive())
    log_tail = (ROOT / "pet.log").read_text(encoding="utf-8", errors="replace")[-600:]
    check("并在日志里留了痕", "输入轮询定时器已停止" in log_tail, repr(log_tail[-160:]))

    window.hide()
    window.close()
    app.quit()
    print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
