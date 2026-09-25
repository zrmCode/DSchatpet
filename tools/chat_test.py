"""对话链路测试:不需要真实 API Key,用本地假服务器验证整条链路。

A 部分(默认,无界面):
  1. 请求形状(鉴权头、model、system 人设、历史轮数)
  2. 回复解析(多家返回结构)
  3. 错误分支(401 / 非 JSON / 缺 Key)
  4. "情绪 → 表情" 映射对着模型真实的 44 个表情做校验
  5. DSH 凭据文件的窄解析

B 部分(``--ui``,需要桌面):
  把桌宠窗口指向假服务器,发一句话,验证气泡真的显示出来、表情真的换了,并截图。

用法::

    .venv\\Scripts\\python.exe tools\\chat_test.py          # 只跑 A
    .venv\\Scripts\\python.exe tools\\chat_test.py --ui     # A + B
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pet.actions import KIND_EXPRESSION, load_actions
from pet.chat import (
    ChatClient,
    ChatError,
    ChatSettings,
    extract_reply,
    pick_expression,
    pick_thinking_expression,
    read_dsh_credential,
)

FAKE_REPLY = "哇!我一直都在哦,你回来啦"
_results = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


# ---------------------------------------------------------------- 假服务器


class _Handler(BaseHTTPRequestHandler):
    reply_text = FAKE_REPLY
    status = 200
    raw_body: bytes | None = None
    last_request: dict = {}

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            parsed = {"__raw__": raw}
        type(self).last_request = {"path": self.path, "headers": dict(self.headers), "body": parsed}

        if self.raw_body is not None:
            payload = self.raw_body
        else:
            payload = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": self.reply_text}}]},
                ensure_ascii=False,
            ).encode("utf-8")

        self.send_response(self.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # 静音
        pass


def start_server() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


# ---------------------------------------------------------------- A 部分


def part_a(model_dir: Path) -> None:
    server, port = start_server()
    base = f"http://127.0.0.1:{port}"
    settings = ChatSettings(base_url=base, model="fake-model", api_key="test-key",
                            use_dsh_credentials=False, history_turns=2, timeout=10)
    client = ChatClient(settings)

    print("\n[1] 请求形状与正常回复")
    reply = client.ask("你好呀", history=[{"role": "user", "content": "上一句"},
                                          {"role": "assistant", "content": "上一句回复"}])
    check("解析出回复正文", reply == FAKE_REPLY, f"got={reply!r}")
    req = _Handler.last_request
    check("请求路径以 /chat/completions 结尾", req["path"].endswith("/chat/completions"), req["path"])
    check("带 Authorization 头", req["headers"].get("Authorization") == "Bearer test-key")
    check("带上 model", req["body"].get("model") == "fake-model", str(req["body"].get("model")))
    roles = [m["role"] for m in req["body"].get("messages", [])]
    check("首条是 system 人设", roles[:1] == ["system"], str(roles))
    check("末条是本次用户输入", req["body"]["messages"][-1]["content"] == "你好呀")
    check("历史被带上", "上一句" in json.dumps(req["body"]["messages"], ensure_ascii=False))

    print("\n[2] 历史轮数上限")
    many = [{"role": "user", "content": f"u{i}"} for i in range(20)]
    client.ask("最新", history=many)
    kept = _Handler.last_request["body"]["messages"]
    check("history_turns=2 时只保留最近 4 条历史", len(kept) == 1 + 4 + 1, f"len={len(kept)}")

    print("\n[3] 返回结构兼容")
    check("choices[0].message.content", extract_reply({"choices": [{"message": {"content": "A"}}]}) == "A")
    check("choices[0].text", extract_reply({"choices": [{"text": "B"}]}) == "B")
    check("顶层 output_text", extract_reply({"output_text": "C"}) == "C")
    try:
        extract_reply({})
        check("空返回应报错", False)
    except ChatError:
        check("空返回应报错", True)

    print("\n[4] 错误分支")
    _Handler.status = 401
    _Handler.raw_body = json.dumps({"error": {"message": "invalid api key"}}).encode()
    try:
        ChatClient(ChatSettings(base_url=base, api_key="bad", use_dsh_credentials=False,
                                timeout=10)).ask("hi")
        check("HTTP 401 抛出 ChatError", False)
    except ChatError as exc:
        check("HTTP 401 抛出 ChatError", "401" in str(exc), str(exc))
    finally:
        _Handler.status = 200
        _Handler.raw_body = None

    _Handler.raw_body = b"not-a-json"
    try:
        ChatClient(ChatSettings(base_url=base, api_key="k", use_dsh_credentials=False,
                                timeout=10)).ask("hi")
        check("非 JSON 返回抛出 ChatError", False)
    except ChatError as exc:
        check("非 JSON 返回抛出 ChatError", "JSON" in str(exc), str(exc))
    finally:
        _Handler.raw_body = None

    missing = ChatClient(ChatSettings(api_key="", api_key_env="NO_SUCH_ENV_VAR_XYZ",
                                      use_dsh_credentials=False, timeout=5))
    try:
        missing.ask("hi")
        check("缺 Key 时给出可读提示", False)
    except ChatError as exc:
        text = str(exc)
        # 分享给别人时,提示要告诉用户"去哪配",而不是只说没找到 Key
        check("缺 Key 时提示去哪里配置", "Key" in text and "设置" in text, text)

    print("\n[5] 情绪 → 表情映射(对着模型真实的 44 个表情)")
    expressions = [a.name for a in load_actions(model_dir) if a.kind == KIND_EXPRESSION]
    check("拿到模型表情列表", len(expressions) >= 40, f"count={len(expressions)}")
    cases = [
        ("你在干什么呀?", "问号"),
        (FAKE_REPLY, "感叹号"),
        ("我也喜欢你!", "爱心眼"),
        ("哇哈哈太好笑了", "开心兴奋"),
    ]
    for text, expect in cases:
        got = pick_expression(text, expressions)
        check(f"{text!r} -> {expect}", got == expect, f"got={got}")
    check("思考表情存在于模型", pick_thinking_expression(expressions) is not None,
          str(pick_thinking_expression(expressions)))
    check("模型里没有的表情不会被选中",
          pick_expression("???", ["问号"]) == "问号" and pick_expression("???", ["别的"]) is None)

    print("\n[6] DSH 凭据文件窄解析")
    tmp = ROOT / ".tmp_creds"
    tmp.mkdir(exist_ok=True)
    cred_file = tmp / ".credentials.yaml"
    cred_file.write_text("version: 1\nrefs:\n  DEEPSEEK_API_KEY: test-value-123\n", encoding="utf-8")
    check("能读到 refs 下的值", read_dsh_credential("DEEPSEEK_API_KEY", tmp) == "test-value-123")
    check("找不到的名字返回 None", read_dsh_credential("OTHER_KEY", tmp) is None)
    cred_file.unlink()
    tmp.rmdir()

    print("\n[7] Key 来源描述(不泄露 key)")
    described = ChatClient(ChatSettings(api_key="secret-abc", use_dsh_credentials=False)).describe_key_source()
    check("描述里不含 key", "secret-abc" not in described, described)

    server.shutdown()


# ---------------------------------------------------------------- B 部分


def part_b(args, model_dir: Path) -> None:
    server, port = start_server()
    _Handler.reply_text = FAKE_REPLY

    import live2d.v3 as live2d
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication, QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from pet import config as config_mod
    from pet.window import PetWindow

    print("\n[8] 界面端到端(气泡 + 表情 + 截图)")
    cfg = config_mod.Config.load()
    cfg.chat_base_url = f"http://127.0.0.1:{port}"
    cfg.chat_api_key = "test-key"
    cfg.chat_use_dsh_credentials = False
    cfg.chat_bubble_seconds = 30
    cfg.gaze_follow = False

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    live2d.init()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)

    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir), load_actions(model_dir))
    window.persist_config = False   # 别把假服务器地址与 test-key 写回用户的 config.json
    window.show()

    state = {"sent": False, "waited": 0.0}

    def tick() -> None:
        if not state["sent"]:
            state["sent"] = True
            print("  → 发送:", "你在吗?")
            window.send_message("你在吗?")
            return

        text = window.bubble.label.text()
        if text and text != "…":
            check("气泡已显示回复", text == FAKE_REPLY, f"got={text!r}")
            check("气泡窗口可见", window.bubble.isVisible())
            check("表情按情绪切换", window.pet.current_expression == pick_expression(FAKE_REPLY, window.pet.expressions),
                  f"got={window.pet.current_expression}")
            check("对话历史已记录", len(window._chat_history) == 2, str(window._chat_history))

            rect = window.frameGeometry().united(window.bubble.frameGeometry())
            screen = QGuiApplication.primaryScreen()
            shots = ROOT / "shots"
            shots.mkdir(exist_ok=True)
            image = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
            image.save(str(shots / "chat.png"))
            print(f"  截图(桌宠+气泡): {shots / 'chat.png'}  {image.width()}x{image.height()}")
            app.quit()
            return

        state["waited"] += 0.2
        if state["waited"] > 15:
            check("气泡在 15 秒内显示回复", False, f"最后内容={text!r}")
            app.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(200)
    QTimer.singleShot(1500, lambda: None)   # 等模型加载完再发

    app.exec()
    window.close()
    live2d.dispose()
    server.shutdown()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="对话链路测试")
    parser.add_argument("--ui", action="store_true", help="额外跑界面端到端(需要桌面)")
    args = parser.parse_args()

    from pet import config as config_mod
    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("找不到模型目录")
        return 2

    part_a(model_dir)
    if args.ui:
        part_b(args, model_dir)

    print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
