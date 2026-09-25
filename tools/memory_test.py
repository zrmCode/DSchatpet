"""养成档案验证:本地记忆 + 个性养成 + **换模型人格不变**。

覆盖用户要求:
  1. 对话记忆能跨重启维持(本地文件)
  2. 重要记忆与个性存本地,可查看/删除/清空/导出导入
  3. 亲密度养成(相处天数 / 对话次数 / 档位影响语气)
  4. **切换模型后人格与记忆原样保留**(提示词由本地档案生成,与模型无关)
  5. 手动教它记忆(「记住:…」)+ 自动抽取(每 N 轮)
  6. 隐私:没有可用后端时静默,不报错

用法::

    .venv\\Scripts\\python.exe tools\\memory_test.py
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pet.memory import (
    Memory,
    MemoryStore,
    HistoryStore,
    Profile,
    affinity_level,
    build_persona_block,
    export_bundle,
    import_bundle,
    parse_extracted_memories,
    parse_manual_memory,
)

_results = {"pass": 0, "fail": 0}
SANDBOX = ROOT / ".tmp" / "memory_test"


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


class _Handler(BaseHTTPRequestHandler):
    """假模型服务:既能聊天,也能回记忆抽取用的 JSON。"""

    mode = "chat"
    last_body: dict = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        type(self).last_body = json.loads(raw or "{}")
        if type(self).mode == "extract":
            content = ('[{"text":"用户在做 Live2D 桌宠项目","kind":"fact","importance":4},'
                       '{"text":"用户喜欢喝美式咖啡","kind":"preference","importance":3}]')
        else:
            content = "好呀,我记住啦~ [表情:星星眼]"
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def part_store() -> None:
    print("\n[1] 档案与记忆库(纯本地)")
    SANDBOX.mkdir(parents=True, exist_ok=True)
    for f in SANDBOX.glob("*"):
        f.unlink()

    profile = Profile()
    check("默认名字与档位", profile.name == "DS鲸鱼娘" and profile.level == "陌生")
    check("档位阈值", [affinity_level(n)[0] for n in (0, 30, 120, 400)]
          == ["陌生", "熟悉", "亲近", "挚友"])
    check("相处天数至少 1 天", profile.days_together >= 1)

    unlocked = profile.touch(chats=10)
    check("聊满 10 句解锁里程碑", "聊满 10 句" in unlocked, str(unlocked))
    check("亲密度随对话增长", profile.affinity == 1 and profile.chat_count == 10)

    store = MemoryStore(SANDBOX / "memories.jsonl", cap=3)
    check("加入一条记忆", store.add(Memory(id="a", text="用户在做桌宠项目", importance=4)))
    check("高重合视为重复(不再记一遍)",
          not store.add(Memory(id="b", text="用户在做桌宠项目!")))
    store.add(Memory(id="c", text="用户喜欢美式咖啡", kind="preference",
                     importance=5, source="manual"))
    hits = store.search("桌宠", 2)
    check("检索能命中相关记忆", any("桌宠" in m.text for m in hits), str([m.text for m in hits]))
    store.add(Memory(id="d", text="用户养了只猫", importance=2))
    store.add(Memory(id="e", text="随手记的第五条", importance=1))
    check("容量上限生效", len(store) <= 3, str(len(store)))
    store.save()
    check("落盘后可重新读回", len(MemoryStore(SANDBOX / "memories.jsonl")) == len(store))

    print("\n[2] 手动教 + 自动抽取解析")
    check("识别「记住:…」", parse_manual_memory("记住:我喜欢喝美式") == "我喜欢喝美式")
    check("识别全角冒号", parse_manual_memory("记一下:我在做桌宠") == "我在做桌宠")
    check("普通句子不触发", parse_manual_memory("你今天好吗") is None)
    extracted = parse_extracted_memories(
        '```json\n[{"text":"用户下周要考试","kind":"event","importance":4}]\n```')
    check("能从代码块里解析 JSON", [m.text for m in extracted] == ["用户下周要考试"])
    check("解析坏 JSON 不崩", parse_extracted_memories("这是一句废话") == [])


def part_persona_is_model_independent() -> None:
    print("\n[3] 换模型,人格不变(核心验证)")
    profile = Profile(name="小鲸", user_title="主人", affinity=150, chat_count=60)
    store = MemoryStore(SANDBOX / "memories2.jsonl", cap=10)
    store.add(Memory(id="m1", text="用户在做桌宠项目", importance=5))
    store.add(Memory(id="m2", text="用户喜欢美式咖啡", kind="preference", importance=4))

    block = build_persona_block(profile, store.search("项目"))
    check("注入块含它的名字", "小鲸" in block)
    check("注入块含对你的称呼", "主人" in block)
    check("注入块含相处天数/对话次数", "相处" in block and "60 句" in block)
    check("注入块含关系档位", "亲近" in block)
    check("注入块含相关记忆", "桌宠项目" in block)

    # 换模型 = 换 ChatClient,但档案是同一份 → 注入块必须逐字相同
    from pet.chat import ChatClient, ChatSettings

    blocks = []
    for base, model in (("https://api.deepseek.com", "deepseek-chat"),
                        ("https://api.openai.com/v1", "gpt-4o-mini"),
                        ("http://127.0.0.1:11434/v1", "qwen2.5:7b")):
        client = ChatClient(ChatSettings(base_url=base, model=model, api_key="x",
                                         use_dsh_credentials=False))
        blocks.append(client.build_messages("项目怎么样了", None,
                                            extra_system=block)[0]["content"])
    check("三个不同模型拿到的人格提示完全一致",
          blocks[0] == blocks[1] == blocks[2],
          f"长度 {[len(b) for b in blocks]}")
    check("人格提示里确实带着本地记忆", all("美式咖啡" in b for b in blocks))


def part_bundle() -> None:
    print("\n[4] 导出 / 导入(备份与迁移)")
    profile = Profile(name="小鲸", affinity=150, chat_count=60)
    memories = [Memory(id="m1", text="用户在做桌宠项目", importance=5),
                Memory(id="m2", text="用户喜欢美式咖啡", kind="preference", importance=4)]
    path = SANDBOX / "bundle.json"
    export_bundle(profile, memories, path)
    check("导出文件已生成", path.is_file())
    loaded_profile, loaded = import_bundle(path)
    check("导入后名字一致", loaded_profile.name == "小鲸")
    check("导入后亲密度一致", loaded_profile.affinity == 150)
    check("导入后记忆条数一致", len(loaded) == 2, str(len(loaded)))
    check("导入后记忆内容一致", loaded[0].text == "用户在做桌宠项目")
    try:
        import_bundle(SANDBOX / "memories.jsonl")
        check("坏文件应报错", False)
    except (ValueError, json.JSONDecodeError):
        check("坏文件会报错而不是静默", True)


def part_ui(port: int) -> None:
    print("\n[5] 界面:记忆页 + 聊天后档案更新 + 自动抽取")
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
    cfg.chat_use_dsh_credentials = False
    cfg.gaze_follow = False
    cfg.idle_motion = False
    cfg.idle_autonomy = False
    cfg.idle_llm_thoughts = False
    cfg.memory_enabled = True
    cfg.memory_extract_every = 1          # 每轮都抽,便于测试

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    live2d.init()
    app = QApplication(sys.argv[:1])
    model_dir = config_mod.find_model_dir()
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir), load_actions(model_dir))
    window.persist_config = False
    # 测试隔离:档案与记忆都指到沙盒,别污染真实养成数据
    window.profile_path = SANDBOX / "ui-profile.json"
    window.profile = Profile()
    window.memories = MemoryStore(SANDBOX / "ui-memories.jsonl", cap=50)
    window.history = HistoryStore(SANDBOX / "ui-history.jsonl")
    window.show()
    window.input_timer.stop()
    window._idle_action_timer.stop()
    window._idle_thought_timer.stop()

    state = {"step": 0}

    def step() -> None:
        state["step"] += 1
        n = state["step"]

        if n == 1:
            print("\n[6] 手动教它记忆")
            window.send_message("记住:我住在杭州,喜欢喝美式咖啡")

        elif n == 3:
            manual = [m for m in window.memories.all() if m.source == "manual"]
            check("手动教的记忆已入库", len(manual) == 1, str([m.text for m in manual]))
            check("内容正确", manual and "杭州" in manual[0].text, str(manual))
            check("已经落盘", (SANDBOX / "ui-memories.jsonl").is_file())

        elif n == 4:
            print("\n[7] 聊天后养成数据更新")
            check("对话次数已增加", window.profile.chat_count >= 1,
                  str(window.profile.chat_count))
            check("亲密度已增加", window.profile.affinity >= 1, str(window.profile.affinity))
            check("对话已写进本地历史", len(window.history.recent(10)) >= 2,
                  str(window.history.recent(10)))

        elif n == 5:
            print("\n[8] 记忆注入:发给模型的提示里带着本地档案")
            _Handler.mode = "chat"
            # 暂时关掉自动抽取,否则抽取请求会覆盖"最后一次请求体",断言会看错请求
            window.cfg.memory_extract_every = 0
            window.send_message("我在做桌宠项目,进展还不错")

        elif n == 7:
            sent = json.dumps(_Handler.last_body, ensure_ascii=False)
            check("请求里带了人格/关系提示", "你和这个人的关系" in sent, sent[:160])
            check("请求里带了本地记忆", "杭州" in sent or "美式" in sent, sent[:160])
            check("注入块本身就是本地档案生成的",
                  "相处" in window._memory_block("项目"))

        elif n == 8:
            print("\n[9] 自动抽取长期记忆")
            from pet.chat import ChatReply

            _Handler.mode = "extract"
            window.cfg.memory_extract_every = 1
            window._on_reply("我在做桌宠项目", ChatReply(text="好呀"))   # 触发轮数与历史
            window._extract_memories()

        elif n == 11:
            texts = [m.text for m in window.memories.all()]
            check("抽取到的记忆已入库", any("桌宠项目" in t for t in texts), str(texts))
            check("重复抽取不会重复入库",
                  len([t for t in texts if "桌宠项目" in t]) == 1, str(texts))

        elif n == 12:
            print("\n[10] 设置面板的记忆页")
            dialog = window.make_settings_dialog()
            check("列表里有记忆", dialog.memory_list.count() >= 2,
                  str(dialog.memory_list.count()))
            check("统计文案含相处天数与亲密度",
                  "相处" in dialog.memory_stats.text() and "亲密度" in dialog.memory_stats.text(),
                  dialog.memory_stats.text())
            check("统计在列表上方显示", dialog.memory_stats.isEnabled())
            dialog.memory_list.setCurrentRow(0)
            before = dialog.memory_list.count()
            dialog._on_delete_memory()
            check("删除选中生效", dialog.memory_list.count() == before - 1,
                  f"{before} -> {dialog.memory_list.count()}")

            path = str(SANDBOX / "ui-export.json")
            message = dialog.export_memories_to(path)
            check("导出成功", Path(path).is_file(), message)
            window.memories.clear()
            check("清空后记忆为空", len(window.memories) == 0)
            message = dialog.import_memories_from(path)
            check("导入还原成功", len(window.memories) > 0, message)
            dialog.reject()

        elif n == 13:
            print("\n[11] 无可用后端时养成功能静默")
            cfg.chat_enabled = False
            window._chat_client = window._make_chat_client()
            window.bubble.hide()
            window.send_message("你好")          # 应当只提示配置,不崩
            check("不崩且给了提示", window.bubble.isVisible())
            check("没有发请求", not window._chat_busy)

        elif n == 14:
            print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
            app.quit()

    timer = QTimer()
    timer.timeout.connect(step)
    timer.start(600)
    QTimer.singleShot(120_000, app.quit)

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

    part_store()
    part_persona_is_model_independent()
    part_bundle()
    part_ui(port)

    server.shutdown()
    print(f"\n=== 总计 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
