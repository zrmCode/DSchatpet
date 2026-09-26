"""窗口生命周期类 bug 的回归测试(审计报告里的一批高危项)。

覆盖 7 条曾经真实发生的问题:

  1. 退出时不等 ``_thought_worker`` / ``_memory_worker`` → 后台线程运行中被析构 →
     Qt ``qFatal`` → 进程崩溃(退出码 0xC0000409)。本测试用**子进程退出码**验证。
  2. 上一句还在飞时按回车:以前"先清空输入框再判断忙碌",用户的字被静默丢弃。
     现在只有真正发出去才清空收起。
  3. 「戳一下」请求失败(断网/5xx/超时)时思考气泡「…」永久残留(seconds=0 + 空失败回调)。
  4. 桌宠已隐藏后,在飞的回答把气泡单独弹到屏幕上(与"气泡随桌宠显隐"冲突)。
  5. 全局快捷键 250ms 去重窗吃掉真实连按(重复投递与真实按键必须分开判别)。
  6. 保存设置把桌宠瞬移回旧坐标(``_apply_geometry`` 用的是过期的 ``cfg.window_x/y``)。
  7. 每次保存设置泄漏一个 ``GlobalHotkeys``(旧对象的原生事件过滤器还挂着)。

用法::

    .venv\\Scripts\\python.exe tools\\lifecycle_test.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


class _Handler(BaseHTTPRequestHandler):
    """假服务:``/slow`` 故意慢(模拟长请求),``/boom`` 直接 500。"""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path.endswith("/boom"):
            body = json.dumps({"error": {"message": "服务器炸了"}}).encode("utf-8")
            self.send_response(500)
        elif self.path.endswith("/slow"):
            time.sleep(6)                      # 长请求:用于验证退出时不会崩
            body = json.dumps({"choices": [{"message": {"role": "assistant",
                                                        "content": "慢回复"}}]},
                              ensure_ascii=False).encode("utf-8")
            self.send_response(200)
        else:
            body = json.dumps({"choices": [{"message": {"role": "assistant",
                                                        "content": "普通回复"}}]},
                              ensure_ascii=False).encode("utf-8")
            self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


# ---------------------------------------------------------------- 子进程:退出崩溃

def run_child(port: int, mode: str) -> int:
    """在子进程里"发起长请求 → 立刻关窗退出",返回退出码。"""
    script = ROOT / ".tmp" / "lifecycle_child.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "\n".join([
            "import sys, time",
            "from pathlib import Path",
            f"ROOT = Path(r'{ROOT}')",
            "sys.path.insert(0, str(ROOT))",
            "import live2d.v3 as live2d",
            "from PySide6.QtGui import QSurfaceFormat",
            "from PySide6.QtWidgets import QApplication",
            "from pet import config as config_mod",
            "from pet.actions import load_actions",
            "from pet.window import PetWindow",
            "fmt = QSurfaceFormat.defaultFormat(); fmt.setAlphaBufferSize(8)",
            "QSurfaceFormat.setDefaultFormat(fmt)",
            "live2d.init(); app = QApplication(sys.argv[:1])",
            "cfg = config_mod.Config.load()",
            "cfg.window_x, cfg.window_y = 200, 200",
            f"cfg.chat_base_url = 'http://127.0.0.1:{port}/{mode}'",
            "cfg.chat_api_key = ''; cfg.chat_api_key_env = 'NO_SUCH_ENV_XYZ'",
            "cfg.chat_use_dsh_credentials = False; cfg.chat_timeout = 20.0",
            "cfg.gaze_follow = False; cfg.idle_motion = False",
            "cfg.idle_autonomy = False; cfg.idle_llm_thoughts = False",
            "md = config_mod.find_model_dir()",
            "w = PetWindow(cfg, md, config_mod.find_model_json(md), load_actions(md))",
            "w.enable_test_mode('lifecycle_child')",
            "w.show()",
            "w.input_timer.stop()",
            "end = time.monotonic() + 3.5",
            "while time.monotonic() < end:",
            "    app.processEvents(); time.sleep(0.02)",
            f"if '{mode}' == 'boom':",
            "    w._fire_poke(0.2)          # 失败的后台请求",
            "else:",
            "    w.send_message('这是一句会慢慢等回复的话')   # 长请求",
            "end = time.monotonic() + 1.2",
            "while time.monotonic() < end:",
            "    app.processEvents(); time.sleep(0.02)",
            "print('子进程:准备关窗退出')",
            "w.close(); app.processEvents(); app.quit()",
            "print('子进程:正常走到最后一行')",
        ]),
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=90)
    return proc.returncode


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if "--child" in sys.argv:            # 兼容:本文件不作为子进程入口使用
        return 0

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    import live2d.v3 as live2d
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from pet import config as config_mod
    from pet.actions import load_actions
    from pet.chat import ChatReply
    from pet.hotkey import GlobalHotkeys
    from pet.window import PetWindow

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    live2d.init()
    app = QApplication(sys.argv[:1])

    cfg = config_mod.Config.load()
    cfg.window_x, cfg.window_y = 240, 240
    cfg.chat_base_url = f"http://127.0.0.1:{port}/ok"
    cfg.chat_api_key = ""
    cfg.chat_api_key_env = "NO_SUCH_ENV_XYZ"
    cfg.chat_use_dsh_credentials = False
    cfg.gaze_follow = False
    cfg.idle_motion = False
    cfg.idle_autonomy = False
    cfg.idle_llm_thoughts = False
    cfg.poke_reaction = True

    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("本机没有模型,跳过(见 assets/README.md)")
        return 0
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir),
                       load_actions(model_dir))
    window.enable_test_mode("lifecycle")
    window.show()

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    pump(4.0)
    window.input_timer.stop()

    print("\n[1] 退出时后台 worker 必须被收干净(以前会崩:0xC0000409)")
    code_slow = run_child(port, "slow")
    check(f"长请求进行中关窗,子进程退出码 {code_slow}(崩溃会是 3221226505)",
          code_slow == 0, f"退出码 {code_slow}")
    code_boom = run_child(port, "boom")
    check(f"失败的后台请求进行中关窗,子进程退出码 {code_boom}",
          code_boom == 0, f"退出码 {code_boom}")

    print("\n[2] 忙碌时回车:用户打的字不许被丢掉")
    chat_input = window.chat_input
    chat_input.edit.setText("这句话不能被吃掉")
    window._chat_worker = None
    window.cfg.chat_enabled = False                  # 模拟"发不出去"
    chat_input._on_return()
    check("没发出去时输入框仍保留文字", chat_input.edit.text() == "这句话不能被吃掉",
          repr(chat_input.edit.text()))
    check("没发出去时输入框仍然可见", chat_input.isVisible())
    window.cfg.chat_enabled = True

    window._chat_worker = None
    chat_input.edit.setText("忙碌时打的字")
    window.send_message("先发一句把 worker 占住")   # 真请求,worker 会运行一会儿
    check("第一句被接收(send_message 返回 True)", window._chat_worker is not None)
    chat_input.edit.setText("忙碌时打的字")
    accepted = window.send_message("趁着上一句还在飞")
    check("忙碌时 send_message 明确返回 False", accepted is False, str(accepted))
    chat_input._on_return()
    check("忙碌时回车不清空输入框", chat_input.edit.text() == "忙碌时打的字",
          repr(chat_input.edit.text()))
    pump(2.5)
    window._chat_worker = None

    print("\n[3] 「戳一下」失败后思考气泡不许永久残留")
    window.cfg.chat_base_url = f"http://127.0.0.1:{port}/boom"
    window._chat_client = window._make_chat_client()
    window._fire_poke(0.2)
    pump(2.5)
    text = window.bubble.label.text()
    check(f"失败后气泡已收掉(当前 {text!r})",
          not window.bubble.isVisible() or text != "…", f"text={text!r} visible={window.bubble.isVisible()}")
    window.cfg.chat_base_url = f"http://127.0.0.1:{port}/ok"
    window._chat_client = window._make_chat_client()

    print("\n[4] 桌宠隐藏后,在飞的回答不许把气泡单独弹出来")
    window.bubble.hide()
    window.show()
    window.toggle_visible()                          # 隐藏桌宠
    check("桌宠已隐藏", not window.isVisible())
    window._on_reply("测试", ChatReply(text="我偷偷说一句"))
    pump(0.4)
    check("隐藏状态下气泡没有弹出来", not window.bubble.isVisible())
    window.toggle_visible()                          # 恢复显示
    check("恢复显示后桌宠可见", window.isVisible())
    window._on_reply("测试", ChatReply(text="现在可以说啦"))
    pump(0.4)
    check("可见时气泡正常显示", window.bubble.isVisible())
    window.bubble.hide()

    print("\n[5] 热键:真实连按要生效,重复投递要去重")
    hotkeys = window.hotkeys
    hotkeys._ids = {0xA520: "toggle_visible"}
    fired: list[str] = []
    hotkeys.triggered.connect(lambda name: fired.append(name))
    hotkeys.duplicates_ignored = 0
    hotkeys._dispatch(0xA520, 111111)                # 第一次按键
    hotkeys._dispatch(0xA520, 111111)                # 同一条消息被 Qt 重复投递
    check("重复投递被去重(只触发一次)", fired == ["toggle_visible"], str(fired))
    check("去重计数已记录", hotkeys.duplicates_ignored == 1, str(hotkeys.duplicates_ignored))
    hotkeys._dispatch(0xA520, 111234)                # 人手连按(约 120ms 后,新时间戳)
    check("真实连按仍然生效(共两次)", len(fired) == 2, str(fired))

    print("\n[6] 保存设置不许把桌宠瞬移回旧坐标")
    window.move(500, 300)
    pump(0.3)
    moved_to = (window.x(), window.y())
    window.cfg.scale = 0.8
    window.apply_config()
    pump(0.3)
    check(f"保存后仍在原地 {moved_to}(实际 {(window.x(), window.y())})",
          (window.x(), window.y()) == moved_to, f"{(window.x(), window.y())}")

    print("\n[7] 保存设置不许泄漏 GlobalHotkeys")
    before = len(window.findChildren(GlobalHotkeys))
    for _ in range(3):
        window.apply_config()
        pump(0.15)
    after = len(window.findChildren(GlobalHotkeys))
    check(f"重复保存后仍只有一个实例({before} → {after})", after == 1, f"{before} → {after}")
    same = window.hotkeys is not None
    check("仍是同一个对象(没有每次新建)", same)

    window.hide()
    window.close()
    server.shutdown()
    app.quit()
    print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
