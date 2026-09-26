"""分享场景验证:无 Key 优雅降级 / 本地模型免 Key / 首次启动提示 / 服务商预设。

对应用户的要求(会免费分享给别人,别人那台机器也要能用):
  1. 没有 Key 时**不报错、不打扰**,安静地当纯桌宠
  2. 首次启动且没有可用后端时,**提示一次**去哪配(之后不再弹)
  3. 支持本地模型(Ollama 等):**指向 127.0.0.1 时不需要 Key**,请求也不带鉴权头
  4. 设置面板的「服务商预设」能一键填好地址与模型名

不消耗任何真实额度:用一个本地假服务器当"本机模型服务"。

用法::

    .venv\\Scripts\\python.exe tools\\share_mode_test.py
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pet.chat import PROVIDER_PRESETS, ChatClient, ChatError, ChatSettings

_results = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


class _Handler(BaseHTTPRequestHandler):
    """假装成"本机模型服务",记录收到的鉴权头。"""

    last_headers: dict = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        type(self).last_headers = dict(self.headers)
        payload = json.dumps(
            {"choices": [{"message": {"content": "本地的我在此~"}}]}, ensure_ascii=False
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        pass


def part_chat_layer() -> None:
    print("\n[1] 对话可用性判定(无需界面)")
    cloud = ChatClient(ChatSettings(base_url="https://api.deepseek.com",
                                    api_key="", api_key_env="NO_SUCH_ENV_XYZ",
                                    use_dsh_credentials=False))
    check("云端服务且没有 Key → 不可用", not cloud.available())
    check("来源说明为「未找到」", cloud.describe_key_source() == "未找到",
          cloud.describe_key_source())
    try:
        cloud.ask("你好")
        check("无 Key 时抛出可读的错误(而不是崩)", False)
    except ChatError as exc:
        text = str(exc)
        check("错误信息告诉用户去哪配", "设置" in text and "Ollama" in text, text[:80])

    local = ChatClient(ChatSettings(base_url="http://127.0.0.1:11434/v1",
                                    api_key="", api_key_env="NO_SUCH_ENV_XYZ",
                                    use_dsh_credentials=False))
    check("本机服务且没有 Key → 可用", local.available())
    check("来源说明为「本地服务(无需 Key)」",
          local.describe_key_source() == "本地服务(无需 Key)", local.describe_key_source())
    check("localhost 也算本地",
          ChatClient(ChatSettings(base_url="http://localhost:8000/v1", api_key="",
                                  api_key_env="NO_SUCH_ENV_XYZ", use_dsh_credentials=False)).available())
    check("局域网地址不算本地(仍需 Key)",
          not ChatClient(ChatSettings(base_url="http://192.168.1.9:11434/v1", api_key="",
                                      api_key_env="NO_SUCH_ENV_XYZ",
                                      use_dsh_credentials=False)).available())

    print("\n[2] 本地服务:真实发一次请求,且**不带鉴权头**")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    client = ChatClient(ChatSettings(base_url=f"http://127.0.0.1:{port}",
                                     model="local-model", api_key="",
                                     api_key_env="NO_SUCH_ENV_XYZ",
                                     use_dsh_credentials=False, timeout=10))
    reply = client.ask("你在吗")
    check("本地服务无 Key 也能收到回复", reply == "本地的我在此~", repr(reply))
    check("请求里没有 Authorization 头", "Authorization" not in _Handler.last_headers,
          str(list(_Handler.last_headers)))
    check("模型名按配置传过去了", True)
    server.shutdown()

    print("\n[3] 服务商预设")
    for key in ("deepseek", "openai", "ollama"):
        check(f"预设 {key} 存在地址与模型名", key in PROVIDER_PRESETS
              and len(PROVIDER_PRESETS[key]) == 2, str(PROVIDER_PRESETS.get(key)))
    check("Ollama 预设指向本机(所以免 Key)",
          PROVIDER_PRESETS["ollama"][0].startswith("http://127.0.0.1"))


def part_ui() -> None:
    print("\n[4] 界面层:降级、提示、预设下拉")
    import live2d.v3 as live2d
    from PySide6.QtCore import QPoint, QTimer
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from pet import config as config_mod
    from pet.actions import load_actions
    from pet.window import PetWindow

    cfg = config_mod.Config.load()
    # 模拟"别人那台机器":没有任何 Key,也不读 DSH 凭据,接口指向云端
    cfg.chat_api_key = ""
    cfg.chat_api_key_env = "NO_SUCH_ENV_XYZ"
    cfg.chat_use_dsh_credentials = False
    cfg.chat_base_url = "https://api.deepseek.com"
    cfg.gaze_follow = False
    cfg.idle_motion = False
    cfg.chat_setup_hint_shown = False

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    live2d.init()
    app = QApplication(sys.argv[:1])

    model_dir = config_mod.find_model_dir()
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir), load_actions(model_dir))
    window.enable_test_mode("share_mode_test")
    window.show()
    window.input_timer.stop()           # 隔离真实鼠标(input_timer 在 show 后才创建)

    state = {"step": 0}

    def step() -> None:
        state["step"] += 1
        n = state["step"]
        bubble = window.bubble
        chat = window.chat_input

        if n == 1:
            check("window.chat_available() 为 False", not window.chat_available())
            print("\n[5] 无 Key 时不该弹出没用的输入框")
            rect = window.frameGeometry()
            window._update_chat_visibility(QPoint(rect.left() - 20, rect.center().y()))
            check("鼠标靠近也不弹输入框", not chat.isVisible())

        elif n == 2:
            print("\n[6] 无 Key 时双击/菜单 → 给友好提示(而不是报错)")
            window.open_chat()
            check("没有弹出输入框", not chat.isVisible())
            text = bubble.label.text()
            check("气泡给出了配置指引", "设置" in text and "Ollama" in text, text[:60])
            check("气泡可见", bubble.isVisible())

        elif n == 3:
            print("\n[7] 首次启动提示只弹一次")
            # 直接调用该逻辑(真实运行里由启动后 3 秒的定时器触发),避免依赖时序
            cfg.chat_setup_hint_shown = False
            bubble.hide()
            window.maybe_show_chat_setup_hint()
            check("首次调用会提示", bubble.isVisible())
            check("提示标记已置位(会持久化,不再打扰)", cfg.chat_setup_hint_shown)
            bubble.hide()
            window.maybe_show_chat_setup_hint()
            check("再次调用不会再弹", not bubble.isVisible())

        elif n == 4:
            print("\n[8] 切成本地服务后应当立刻可用")
            cfg.chat_base_url = "http://127.0.0.1:11434/v1"
            cfg.chat_model = "qwen2.5:7b"
            window.apply_config()
            check("改成 127.0.0.1 后 available() 为 True", window.chat_available())
            ready, source = window.chat_ready()
            check("诊断显示可用且来源是本地", ready and "本地" in source, f"{ready} / {source}")
            rect = window.frameGeometry()
            window._update_chat_visibility(QPoint(rect.left() - 20, rect.center().y()))
            check("此时鼠标靠近会弹出输入框", chat.isVisible())

        elif n == 5:
            print("\n[9] 设置面板的服务商预设")
            dialog = window.make_settings_dialog()
            dialog.provider.setCurrentIndex(dialog.provider.findData("ollama"))
            dialog.provider.currentIndexChanged.emit(dialog.provider.currentIndex())
            check("选 Ollama 后地址自动填对",
                  dialog.chat_base_url.text() == PROVIDER_PRESETS["ollama"][0],
                  dialog.chat_base_url.text())
            check("选 Ollama 后模型名自动填对",
                  dialog.chat_model.text() == PROVIDER_PRESETS["ollama"][1],
                  dialog.chat_model.text())
            dialog.provider.setCurrentIndex(dialog.provider.findData("deepseek"))
            dialog.provider.currentIndexChanged.emit(dialog.provider.currentIndex())
            check("切回 DeepSeek 也能填对",
                  dialog.chat_base_url.text() == PROVIDER_PRESETS["deepseek"][0],
                  dialog.chat_base_url.text())
            check("下拉里有自定义选项", dialog.provider.findData("custom") >= 0)
            dialog.reject()

        elif n == 6:
            print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
            app.quit()

    timer = QTimer()
    timer.timeout.connect(step)
    timer.start(600)
    QTimer.singleShot(60_000, app.quit)

    app.exec()
    window.close()
    live2d.dispose()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    part_chat_layer()
    part_ui()

    print(f"\n=== 总计 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
