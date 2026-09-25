"""「点一下」行为验证:取消随机反射,改成让 AI 理解点击并回应。

用户要求:
  1. **取消**"点一下就随机换表情/动画"的机器反射;
  2. 让 AI **理解你的点击** —— 知道被戳了、戳到哪儿,然后用一句话 + 表情/动作回应。

这里同时钉住几件容易回归的事:
  - 拖拽 ≠ 点击(拖动窗口不该触发回应);
  - 双击是"开聊天框",不该顺带再触发一次"被戳";
  - 没配对话后端时点击**什么都不做**(纯粹当桌宠用,不打扰);
  - 上一句还在想的时候点击被跳过,不打断。

不消耗真实额度:全部用本地假服务器。

用法::

    .venv\\Scripts\\python.exe tools\\poke_test.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

SANDBOX = ROOT / ".tmp" / "poke_test"

_results = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


class _Handler(BaseHTTPRequestHandler):
    """假对话服务:记录收到的请求,返回一句"被戳"的回应 + 一个表情指令。"""

    bodies: list[dict] = []
    expression = ""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        body = json.loads(raw or "{}")
        type(self).bodies.append(body)
        content = f"呀!别戳我啦～ [表情:{type(self).expression}]"
        payload = {"choices": [{"message": {"role": "assistant", "content": content}}]}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


def pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def send_click(window, kind: str, local: QPointF, buttons=Qt.LeftButton) -> None:
    """把真实的 Qt 鼠标事件投递给窗口(与用户操作走同一条代码路径)。"""
    event = QMouseEvent(kind, local, window.mapToGlobal(local.toPoint()),
                        Qt.LeftButton, buttons, Qt.NoModifier)
    QApplication.sendEvent(window, event)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    SANDBOX.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    import live2d.v3 as live2d
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from pet import config as config_mod
    from pet.actions import load_actions
    from pet.memory import HistoryStore, MemoryStore, Profile
    from pet.window import POKE_DELAY_MS, POKE_REGIONS, PetWindow

    cfg = config_mod.Config.load()
    cfg.chat_base_url = f"http://127.0.0.1:{port}"
    cfg.chat_api_key = ""
    cfg.chat_api_key_env = "NO_SUCH_ENV_XYZ"
    cfg.chat_use_dsh_credentials = False
    cfg.chat_use_tools = True
    cfg.chat_model_actions = True
    cfg.gaze_follow = False
    cfg.idle_motion = False
    cfg.idle_autonomy = False
    cfg.idle_llm_thoughts = False
    cfg.poke_reaction = True
    cfg.chat_hover = False

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    live2d.init()
    app = QApplication(sys.argv[:1])

    model_dir = config_mod.find_model_dir()
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir), load_actions(model_dir))
    window.persist_config = False
    # 测试隔离:档案/记忆/历史都指到沙盒,别污染真实养成数据
    window.profile_path = SANDBOX / "profile.json"
    window.profile = Profile()
    window.memories = MemoryStore(SANDBOX / "memories.jsonl", cap=50)
    window.history = HistoryStore(SANDBOX / "history.jsonl")
    window.show()
    window.input_timer.stop()
    _Handler.expression = window.pet.expressions[0]
    pump(app, 3.0)          # 等模型加载/首帧渲染完成

    print("\n[1] 戳哪儿了 —— 按窗口纵向位置判断(模型没有 HitAreas,只能粗判)")
    limits = [limit for limit, _ in POKE_REGIONS]
    for rel, want in ((0.05, "头顶"), (limits[0] - 0.01, "头顶"), (limits[0] + 0.01, "脸"),
                      (limits[1] + 0.01, "身上"), (0.99, "尾巴和裙摆")):
        got = window.poke_region(rel)
        check(f"相对高度 {rel:.2f} → {want}", got == want, f"实际 {got}")

    print("\n[2] 单击 = 模型理解并回应(说话 + 表情)")
    before_expression = window.pet.current_expression
    random_calls = {"n": 0}
    real_random = window.pet.random_expression

    def counting_random():
        random_calls["n"] += 1
        return real_random()

    window.pet.random_expression = counting_random      # 旧行为(随机换表情)必须不再被调用
    _Handler.bodies.clear()
    send_click(window, QEvent.Type.MouseButtonPress, QPointF(180, 60))
    send_click(window, QEvent.Type.MouseButtonRelease, QPointF(180, 60))
    pump(app, POKE_DELAY_MS / 1000 + 0.4)
    check("单击后发出了「被戳」请求", len(_Handler.bodies) == 1, f"{len(_Handler.bodies)} 次")
    sent = json.dumps(_Handler.bodies[-1] if _Handler.bodies else {}, ensure_ascii=False)
    check("请求里说明了被戳的位置", "戳了戳你的头顶" in sent, sent[:160])
    check("提示词要求「很短的话 + 表情」", "很短的话" in sent and "表情" in sent)
    check("没有调用旧的随机表情反射", random_calls["n"] == 0, f"调用 {random_calls['n']} 次")

    pump(app, 2.5)
    check("气泡显示的是模型的话", window.bubble.label.text().startswith("呀!别戳我啦"),
          repr(window.bubble.label.text()))
    check("正文里没有指令残留", "[表情:" not in window.bubble.label.text(),
          repr(window.bubble.label.text()))
    check("模型给的表情已作用到模型上",
          window.pet.current_expression == _Handler.expression,
          f"{before_expression!r} → {window.pet.current_expression!r}")
    check("亲密度涨了(互动算数)", window.profile.affinity >= 1, str(window.profile.affinity))
    check("戳一下不计入对话轮数", window._turns_since_extract == 0,
          str(window._turns_since_extract))

    print("\n[3] 拖拽 ≠ 点击(拖动窗口不该触发回应)")
    _Handler.bodies.clear()
    window._chat_worker = None
    send_click(window, QEvent.Type.MouseButtonPress, QPointF(180, 60))
    send_click(window, QEvent.Type.MouseMove, QPointF(220, 90))
    send_click(window, QEvent.Type.MouseButtonRelease, QPointF(220, 90))
    pump(app, POKE_DELAY_MS / 1000 + 0.5)
    check("拖动后没有发请求", not _Handler.bodies, f"{len(_Handler.bodies)} 次")

    print("\n[4] 双击 = 开聊天框,不该顺带触发「被戳」")
    _Handler.bodies.clear()
    send_click(window, QEvent.Type.MouseButtonPress, QPointF(180, 60))
    send_click(window, QEvent.Type.MouseButtonRelease, QPointF(180, 60))
    send_click(window, QEvent.Type.MouseButtonDblClick, QPointF(180, 60))
    send_click(window, QEvent.Type.MouseButtonRelease, QPointF(180, 60))
    pump(app, POKE_DELAY_MS / 1000 + 0.5)
    check("双击后没有「被戳」请求", not _Handler.bodies, f"{len(_Handler.bodies)} 次")
    check("聊天输入框被打开了", window.chat_input.isVisible())
    window.chat_input.hide()

    print("\n[5] 开关关掉后,点击不做任何事")
    cfg.poke_reaction = False
    _Handler.bodies.clear()
    send_click(window, QEvent.Type.MouseButtonPress, QPointF(180, 60))
    send_click(window, QEvent.Type.MouseButtonRelease, QPointF(180, 60))
    pump(app, POKE_DELAY_MS / 1000 + 0.4)
    check("没有发请求", not _Handler.bodies, f"{len(_Handler.bodies)} 次")
    cfg.poke_reaction = True

    print("\n[6] 没有可用后端时,点击静默跳过(不打扰、不报错)")
    good_url = cfg.chat_base_url
    cfg.chat_base_url = "https://api.example.invalid"   # 远端地址 + 没 Key = 不可用
    window._chat_client = window._make_chat_client()
    check("后端确实不可用", not window.chat_available())
    _Handler.bodies.clear()
    bubble_before = window.bubble.label.text()
    send_click(window, QEvent.Type.MouseButtonPress, QPointF(180, 60))
    send_click(window, QEvent.Type.MouseButtonRelease, QPointF(180, 60))
    pump(app, POKE_DELAY_MS / 1000 + 0.4)
    check("没有发请求", not _Handler.bodies, f"{len(_Handler.bodies)} 次")
    check("气泡内容没被改动", window.bubble.label.text() == bubble_before,
          repr(window.bubble.label.text()))
    cfg.chat_base_url = good_url
    window._chat_client = window._make_chat_client()

    print("\n[7] 上一句还在想的时候,点击被跳过")
    class _BusyWorker:
        def isRunning(self) -> bool:      # noqa: N802 - 模仿 QThread 接口
            return True

    window._chat_worker = _BusyWorker()
    _Handler.bodies.clear()
    window._fire_poke(0.5)
    check("忙碌时没有发请求", not _Handler.bodies, f"{len(_Handler.bodies)} 次")
    check("忙碌时也没改气泡", "…" not in window.bubble.label.text(),
          repr(window.bubble.label.text()))
    window._chat_worker = None

    print("\n[8] 提示词与操作说明")
    import pet.chat as chat_mod

    check("POKE_PROMPT 说清了「是点击、不是打字」", "戳了你一下" in chat_mod.POKE_PROMPT
          and "不是在跟你打字" in chat_mod.POKE_PROMPT)
    check("POKE_PROMPT 不让它长篇大论", "别长篇大论" in chat_mod.POKE_PROMPT)
    check("菜单里的「随机表情」还在(那是有意为之的手动功能)",
          any("随机表情" in a.text() for a in window.build_menu(window).actions()))

    window.hide()
    window.close()
    server.shutdown()
    print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    app.quit()
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
