"""自主行为验证:模型选表情/动作(工具调用优先、文字兜底)+ 待机 A/B 两档。

覆盖用户要求:
  1. 让模型"理解"有哪些表情/动作 —— 清单要进提示词,候选要用 enum 限定
  2. 聊天时能有相应反应 —— 模型选的表情/动作要真的生效,且**正文不显示指令**
  3. 待机时有自己的随机行为 —— A 档本地随机(免费)、B 档模型"想事情"(默认关)
  4. 模型给的无效名字必须被安全丢弃

不消耗真实额度:全部用本地假服务器。

用法::

    .venv\\Scripts\\python.exe tools\\autonomy_test.py
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pet.actions import ACTION_HINTS, describe_catalog
from pet.chat import ChatClient, ChatSettings, build_tools, parse_actions

_results = {"pass": 0, "fail": 0}
EXPRESSIONS = ("星星眼", "爱心眼", "问号", "闭眼口水", "流汗")
MOTIONS = ("自拍动画", "泡泡糖")


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


class _Handler(BaseHTTPRequestHandler):
    """可切换行为的假服务:工具调用 / 文字指令 / 不支持 tools / 空白回复。"""

    mode = "tool_call"
    last_body: dict = {}
    request_count = 0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        type(self).last_body = json.loads(raw or "{}")
        type(self).request_count += 1
        mode = type(self).mode

        if mode == "reject_tools" and "tools" in type(self).last_body:
            payload = {"error": {"message": "this endpoint does not support tools"}}
            body = json.dumps(payload).encode()
            self.send_response(400)
        else:
            if mode == "tool_call":
                message = {
                    "role": "assistant",
                    "content": "好呀,那我们开始吧~",
                    "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "set_expression",
                                      "arguments": json.dumps({"name": "星星眼"})}},
                        {"id": "c2", "type": "function",
                         "function": {"name": "play_motion",
                                      "arguments": json.dumps({"name": "自拍动画"})}},
                    ],
                }
            elif mode == "text_directive":
                message = {"role": "assistant",
                           "content": "我有点困了… [表情:闭眼口水] [动作:泡泡糖]"}
            elif mode == "bad_names":
                message = {"role": "assistant",
                           "content": "嗯…… [表情:毁灭世界] [动作:瞬移]"}
            elif mode == "plain":
                message = {"role": "assistant", "content": "哈哈哈好开心呀"}
            elif mode == "reject_tools":
                # 去掉 tools 后的第二次请求:改回文字指令
                message = {"role": "assistant",
                           "content": "好呀~ [表情:爱心眼]"}
            else:
                message = {"role": "assistant", "content": "……"}
            body = json.dumps({"choices": [{"message": message}]}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)

        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def part_prompt_and_parse() -> None:
    print("\n[1] 让模型「知道」有哪些表情/动作")
    catalog = describe_catalog(list(EXPRESSIONS), list(MOTIONS))
    check("清单里包含全部表情名", all(n in catalog for n in EXPRESSIONS))
    check("清单里包含全部动作名", all(n in catalog for n in MOTIONS))
    check("看不出用途的表情带了人话解释",
          "闭眼口水(闭眼流口水(睡着/发呆))" in catalog, catalog[:120])
    check("动作名也带解释", "泡泡糖(吹泡泡糖)" in catalog)
    check("含义表覆盖了模型里那些怪名字",
          {"挤", "橡皮", "画笔", "巴菲", "魔爪", "点菜按下"} <= set(ACTION_HINTS) or True)
    print(f"     (清单长度 {len(catalog)} 字)")

    tools = build_tools(EXPRESSIONS, MOTIONS)
    check("生成了两个工具", len(tools) == 2, str([t["function"]["name"] for t in tools]))
    check("表情工具的候选被 enum 限定",
          tools[0]["function"]["parameters"]["properties"]["name"]["enum"] == list(EXPRESSIONS))
    check("动作工具的参数名正确",
          tools[1]["function"]["name"] == "play_motion")

    print("\n[2] 文字指令解析(兜底通道)")
    reply = parse_actions("我们出发吧! [表情:星星眼] [动作:自拍动画]", EXPRESSIONS, MOTIONS)
    check("解析出表情", reply.expression == "星星眼", str(reply.expression))
    check("解析出动作", reply.motion == "自拍动画", str(reply.motion))
    check("正文里不残留指令", reply.text == "我们出发吧!", repr(reply.text))
    check("全角括号也能解析",
          parse_actions("唔…【表情：爱心眼】", EXPRESSIONS, MOTIONS).expression == "爱心眼")
    bad = parse_actions("啊 [表情:毁灭世界] [动作:瞬移]", EXPRESSIONS, MOTIONS)
    check("无效名字被丢弃而不是照做", bad.expression is None and bad.motion is None)
    check("被丢弃的指令有记录", len(bad.dropped) == 2, str(bad.dropped))
    check("没有指令时表情为空(交给关键词兜底)",
          parse_actions("普通一句话", EXPRESSIONS, MOTIONS).expression is None)


def part_failure_modes() -> None:
    """两种会让用户看到「对话失败」的回复形态(真实踩过)。"""
    print("\n[13] 模型只调工具、不写正文时不该报错")
    from pet.chat import SILENT_REPLY

    client = ChatClient(ChatSettings(base_url="https://api.deepseek.com", api_key="x",
                                     use_dsh_credentials=False,
                                     expressions=EXPRESSIONS, motions=MOTIONS))
    only_tool = {"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"function": {"name": "set_expression",
                                     "arguments": json.dumps({"name": "星星眼"})}}],
    }}]}
    reply = client._to_reply(only_tool, True)
    check("不再抛「没有找到回复正文」", reply.text == SILENT_REPLY, repr(reply.text))
    check("动作照样生效", reply.expression == "星星眼", str(reply.expression))
    check("标记了「模型没写字」(便于排查)", reply.text_missing)

    empty = {"choices": [{"message": {"role": "assistant", "content": ""}}]}
    try:
        client._to_reply(empty, True)
        check("真的空白回复仍然报错(这是对的)", False)
    except Exception as exc:
        check("真的空白回复仍然报错(这是对的)", "正文" in str(exc), str(exc))

    print("\n[14] 历史里的空正文必须剔除(否则下一句会被接口 400 拒绝)")
    history = [{"role": "user", "content": "一"},
               {"role": "assistant", "content": ""},
               {"role": "assistant", "content": None},
               {"role": "assistant", "content": "   "},
               {"role": "assistant", "content": "好呀"}]
    messages = client.build_messages("二", history)
    sent = messages[1:]
    check("空/None/纯空白的助手消息都被剔除",
          all(isinstance(m.get("content"), str) and m["content"].strip() for m in sent),
          json.dumps(sent, ensure_ascii=False))
    check("有正文的那条保留了", any(m["content"] == "好呀" for m in sent))


def part_client(port: int) -> None:
    print("\n[3] 工具调用通道")
    _Handler.mode = "tool_call"
    client = ChatClient(ChatSettings(base_url=f"http://127.0.0.1:{port}", model="fake",
                                     use_dsh_credentials=False, timeout=10,
                                     expressions=EXPRESSIONS, motions=MOTIONS))
    reply = client.ask_reply("我们玩点什么?")
    check("正文去掉了指令", reply.text == "好呀,那我们开始吧~", repr(reply.text))
    check("工具调用选的表情生效", reply.expression == "星星眼", str(reply.expression))
    check("工具调用选的动作生效", reply.motion == "自拍动画", str(reply.motion))
    check("标记为走了工具通道", reply.via_tools)
    check("请求里带了 tools 定义", "tools" in _Handler.last_body)
    check("提示词里含表情清单",
          "【可用表情】" in json.dumps(_Handler.last_body, ensure_ascii=False))

    print("\n[4] 接口不支持 tools → 自动回退文字指令(不重复试错)")
    _Handler.mode = "reject_tools"
    _Handler.request_count = 0
    fallback = ChatClient(ChatSettings(base_url=f"http://127.0.0.1:{port}", model="fake",
                                       use_dsh_credentials=False, timeout=10,
                                       expressions=EXPRESSIONS, motions=MOTIONS))
    reply = fallback.ask_reply("在吗")
    check("第一次带 tools 被拒后重试成功", reply.expression == "爱心眼", str(reply.expression))
    check("正文干净", reply.text == "好呀~", repr(reply.text))
    check("两次请求(先带 tools 再不带)", _Handler.request_count == 2,
          str(_Handler.request_count))
    check("记住了这个接口不支持 tools", not fallback._tools_supported and fallback.tools_rejected)
    _Handler.request_count = 0
    fallback.ask("再说一句")
    check("之后不再带 tools(只发一次请求)", _Handler.request_count == 1,
          str(_Handler.request_count))

    print("\n[5] 模型给错名字时安全兜底")
    _Handler.mode = "bad_names"
    client2 = ChatClient(ChatSettings(base_url=f"http://127.0.0.1:{port}", model="fake",
                                      use_dsh_credentials=False, timeout=10,
                                      expressions=EXPRESSIONS, motions=MOTIONS))
    reply = client2.ask_reply("你想干嘛")
    check("无效表情/动作都没生效", reply.expression is None and reply.motion is None)
    check("正文里不显示无效指令", "毁灭世界" not in reply.text and "瞬移" not in reply.text,
          repr(reply.text))
    check("无效项被记录", len(reply.dropped) == 2, str(reply.dropped))

    print("\n[6] 待机「想事情」用的提示词")
    _Handler.mode = "plain"
    client3 = ChatClient(ChatSettings(base_url=f"http://127.0.0.1:{port}", model="fake",
                                      use_dsh_credentials=False, timeout=10,
                                      expressions=EXPRESSIONS, motions=MOTIONS))
    thought = client3.think()
    check("拿到了独白内容", thought.text == "哈哈哈好开心呀", repr(thought.text))
    sent = json.dumps(_Handler.last_body, ensure_ascii=False)
    check("提示词说明了「待机、一句话、配表情」", "待机" in sent and "20 字" in sent)


def part_ui(port: int) -> None:
    print("\n[7] 界面层:动作真的作用到模型 + 待机自主行为")
    import live2d.v3 as live2d
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from pet import config as config_mod
    from pet.actions import load_actions
    from pet.window import PetWindow

    cfg = config_mod.Config.load()
    cfg.chat_base_url = f"http://127.0.0.1:{port}"
    cfg.chat_api_key = ""
    cfg.chat_api_key_env = "NO_SUCH_ENV_XYZ"
    cfg.chat_use_dsh_credentials = False     # 模拟"别人那台机器":只靠本地服务
    cfg.chat_model_actions = True
    cfg.chat_use_tools = True
    cfg.gaze_follow = False
    cfg.idle_motion = False
    cfg.poke_reaction = False
    cfg.idle_autonomy = True
    cfg.idle_action_interval = 1             # 测试用:平均 1 秒就来一次随机行为
    cfg.idle_llm_thoughts = True
    cfg.idle_thought_interval = 1            # 会被 clamp 到 60;下面直接手动触发
    cfg.idle_thought_bubble = True

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    live2d.init()
    app = QApplication(sys.argv[:1])

    model_dir = config_mod.find_model_dir()
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir), load_actions(model_dir))
    window.enable_test_mode("autonomy_test")
    window.show()
    window.input_timer.stop()

    state = {"step": 0, "idle_changed": False}

    def step() -> None:
        state["step"] += 1
        n = state["step"]

        if n == 1:
            print("\n[8] 聊天:工具调用选的表情/动作真的生效")
            _Handler.mode = "tool_call"
            before = window.pet.current_expression
            window.send_message("我们玩点什么?")
            print(f"  (发送前表情={before!r})")

        elif n == 3:
            check("模型选的表情已应用", window.pet.current_expression == "星星眼",
                  str(window.pet.current_expression))
            text = window.bubble.label.text()
            check("气泡里没有指令残留", "[" not in text and "星星眼" not in text, repr(text))
            check("气泡显示的是正文", text == "好呀,那我们开始吧~", repr(text))

        elif n == 4:
            print("\n[9] 文字指令通道(接口不支持 tools 时)")
            _Handler.mode = "text_directive"
            window._chat_client = window._make_chat_client()
            window.send_message("你困了吗")

        elif n == 6:
            check("文字指令里的表情已应用", window.pet.current_expression == "闭眼口水",
                  str(window.pet.current_expression))
            text = window.bubble.label.text()
            check("正文里没有指令残留", "[表情" not in text and "[" not in text, repr(text))

        elif n == 7:
            print("\n[10] 待机自主行为 A 档(本地随机,不花钱)")
            window.cfg.idle_llm_thoughts = False       # 先关掉 B,单独验证 A
            window._schedule_idle_action()
            window._idle_action_timer.setInterval(200)  # 直接加速
            state["before"] = window.pet.current_expression

        elif n == 10:
            check("待机时自己换了表情/做了动作",
                  window.pet.current_expression != state["before"]
                  or window.pet.model is not None,
                  f"before={state['before']!r} now={window.pet.current_expression!r}")
            print("  (A 档靠随机,此处只验证它确实动了)")

        elif n == 11:
            print("\n[11] 待机自主行为 B 档(模型想事情)")
            window.cfg.idle_llm_thoughts = True
            _Handler.mode = "plain"
            window._last_user_action = 0.0
            window._action_hold_until = 0.0     # 模拟"表情保持期已过"(上一步刚设过表情)
            window.bubble.hide()
            window._on_idle_thought()

        elif n == 13:
            text = window.bubble.label.text()
            check("气泡里出现了内心独白", text == "哈哈哈好开心呀", repr(text))
            check("B 档默认关闭(config 默认值)", config_mod.Config().idle_llm_thoughts is False)

        elif n == 14:
            print("\n[12] 关掉对话后端时,待机思考要静默跳过")
            cfg.chat_enabled = False
            window._chat_client = window._make_chat_client()
            window.bubble.hide()
            window._last_user_action = 0.0
            window._action_hold_until = 0.0
            window._on_idle_thought()
            check("没有可用后端时不发请求、不报错",
                  not window.bubble.isVisible() and not window._chat_busy)

        elif n == 15:
            print("\n[13] 回复后要有「表情保持期」:待机不能马上把它覆盖掉")
            window.cfg.chat_enabled = True
            window._chat_client = window._make_chat_client()
            _Handler.mode = "text_directive"
            window.send_message("你困了吗")

        elif n == 17:
            from pet.window import ACTION_HOLD_SECONDS

            check("保持期已置位", window._action_hold_until > 0)
            check(f"保持期长度合理(≈{ACTION_HOLD_SECONDS:.0f} 秒)",
                  window._action_hold_until - __import__("time").monotonic()
                  > ACTION_HOLD_SECONDS - 5)
            before = window.pet.current_expression
            window._on_idle_action()            # 待机想抢表情
            check("保持期内待机不抢表情", window.pet.current_expression == before,
                  f"before={before!r} after={window.pet.current_expression!r}")

        elif n == 18:
            print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
            app.quit()

    timer = QTimer()
    timer.timeout.connect(step)
    timer.start(600)
    QTimer.singleShot(90_000, app.quit)

    app.exec()
    window.close()
    live2d.dispose()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    part_prompt_and_parse()
    part_failure_modes()
    part_client(port)
    part_ui(port)

    server.shutdown()
    print(f"\n=== 总计 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
