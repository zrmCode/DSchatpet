"""健壮性回归:一批"静默失效 / 丢数据"类 bug(审计高危清单 Batch B)。

  1. 手改 ``config.json`` 类型出错 → 以前 ``clamp()`` 抛 ``TypeError``,
     启动早期挂掉,表现为桌宠静止、无热键、零日志。
  2. 记忆库:一行"合法 JSON 但不是对象"的坏行让**整个记忆库加载失败**;
     淘汰策略在同重要度同秒时反而删掉**最新**记忆;检索打分让"无关但重要又新"的
     记忆压过"完全命中的老记忆"。
  3. 接口返回结构异常(``{"choices":[null]}``、``function: null``、``arguments`` 直接给对象
     或是坏 JSON、``content: None``)→ 以前抛 ``AttributeError`` 绕过 ``ChatError`` 约定,
     UI 会永远停在「…」。
  4. 设置面板的「鼠标靠近时自动弹出输入框」复选框与配置脱钩(影子控件没进布局)。
  5. ``LoadExtraMotion`` 的返回值是**组内序号**(实测),以前被忽略、自己计数 →
     一个动画加载失败就"点 A 播 B"。

用法::

    .venv\\Scripts\\python.exe tools\\robustness_test.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SANDBOX = ROOT / ".tmp" / "robustness"

_results = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


def now_iso() -> str:
    """当前时间的 ISO 字符串(``Memory.created_at`` 用的就是这种格式)。"""
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def days_ago_iso(days: int) -> str:
    from datetime import datetime, timedelta

    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def part_config() -> None:
    from pet.config import Config

    print("\n[1] 配置被手改成错类型,启动不许静默挂掉")
    SANDBOX.mkdir(parents=True, exist_ok=True)
    bad_path = SANDBOX / "bad-config.json"
    bad_path.write_text(json.dumps({
        "window_height": None, "scale": "0.85", "opacity": [1.0], "fps": "abc",
        "gaze_follow": "yes", "chat_enabled": 0, "chat_hover": "false",
        "chat_base_url": 12345, "hotkey_toggle_visible": None, "window_x": "1042",
        "window_y": "不是数字", "memory_max_items": -5, "chat_timeout": "9999",
    }), encoding="utf-8")
    try:
        cfg = Config.load(bad_path)
        ok = True
    except Exception as exc:                       # noqa: BLE001 - 就是要把任何异常都抓住
        ok = False
        cfg = None
        check("加载坏配置没有抛异常", False, f"{type(exc).__name__}: {exc}")
    if ok:
        check("加载坏配置没有抛异常", True)
        check("null → 退回默认值", cfg.window_height == 373, str(cfg.window_height))
        check("字符串数字被接受", abs(cfg.scale - 0.85) < 1e-6, str(cfg.scale))
        check("数组类型 → 退回默认值", abs(cfg.opacity - 1.0) < 1e-6, str(cfg.opacity))
        check("乱七八糟的 fps → 默认 60", cfg.fps == 60, str(cfg.fps))
        check("'yes' 当布尔真", cfg.gaze_follow is True, str(cfg.gaze_follow))
        check("0 当布尔假", cfg.chat_enabled is False, str(cfg.chat_enabled))
        check("数字 URL → 退回默认地址", cfg.chat_base_url.startswith("https://"),
              str(cfg.chat_base_url))
        check("null 快捷键 → 默认", cfg.hotkey_toggle_visible == "ctrl+alt+W",
              str(cfg.hotkey_toggle_visible))
        check("字符串坐标被接受", cfg.window_x == 1042, str(cfg.window_x))
        check("非数字坐标 → None(表示自动摆)", cfg.window_y is None, str(cfg.window_y))
        check("负数被夹到下限", cfg.memory_max_items == 10, str(cfg.memory_max_items))
        check("超大超时被夹到上限", abs(cfg.chat_timeout - 120.0) < 1e-6, str(cfg.chat_timeout))


def part_memory() -> None:
    from pet.memory import Memory, MemoryStore

    print("\n[2] 记忆库:坏行不许毁掉整个库")
    SANDBOX.mkdir(parents=True, exist_ok=True)
    path = SANDBOX / "memories.jsonl"
    good = Memory(id="m1", text="用户住在杭州,喜欢喝美式咖啡", kind="fact", importance=4)
    path.write_text(
        "\n".join([
            good.to_json(),
            "5",                       # 合法 JSON,但不是对象
            "null",
            "[1, 2, 3]",
            '{"text": "半行被截断',     # 坏 JSON
            Memory(id="m2", text="用户在做 Live2D 桌宠项目", kind="fact", importance=3).to_json(),
        ]) + "\n",
        encoding="utf-8",
    )
    store = MemoryStore(path, cap=50)
    check("坏行没有让加载失败", len(store) == 2, f"载入 {len(store)} 条")
    texts = [m.text for m in store.all()]
    check("两条好记忆都在", any("杭州" in t for t in texts) and any("桌宠" in t for t in texts),
          str(texts))

    print("\n[3] 淘汰策略:同重要度同秒时必须保住**最新**的")
    path2 = SANDBOX / "cap.jsonl"
    store2 = MemoryStore(path2, cap=3)
    same_second = now_iso()                            # 同一秒写入(测试与连续抽取都会出现)
    distinct = ["用户喜欢喝美式咖啡", "用户住在杭州西湖边", "用户在做 Live2D 桌宠项目",
                "用户的生日在五月", "用户养了一只叫团子的猫"]
    for index, text in enumerate(distinct):
        store2.add(Memory(id=f"m{index}", text=text, kind="fact",
                          importance=3, created_at=same_second))
    kept = [m.id for m in store2.all()]
    check("留下的是最新的 m2/m3/m4", set(kept) == {"m2", "m3", "m4"}, str(kept))

    print("\n[4] 检索打分:命中话题的老记忆要排在'无关但重要又新'之前")
    path3 = SANDBOX / "search.jsonl"
    store3 = MemoryStore(path3, cap=50)
    store3.add(Memory(id="old", text="用户住在杭州,喜欢喝美式咖啡", kind="fact",
                      importance=3, created_at=days_ago_iso(20)))
    store3.add(Memory(id="new", text="用户昨天说想看一部电影", kind="event",
                      importance=5, created_at=now_iso()))
    top = store3.search("我周末想去杭州玩,顺便喝点咖啡", limit=1)
    check("命中的老记忆排第一", top and top[0].id == "old",
          str([m.id for m in store3.search("我周末想去杭州玩,顺便喝点咖啡", limit=2)]))


def part_chat() -> None:
    from pet.chat import ChatClient, ChatError, ChatSettings, SILENT_REPLY

    print("\n[5] 接口返回结构异常:要给 ChatError,不许抛 AttributeError")
    client = ChatClient(ChatSettings(base_url="http://127.0.0.1:1", model="fake",
                                     api_key="x", use_dsh_credentials=False, timeout=5,
                                     expressions=("星星眼",), motions=("自拍动画",)))

    cases = {
        "choices 里是 null": {"choices": [None]},
        "message 是 null": {"choices": [{"message": None}]},
        "choice 是字符串": {"choices": ["我是一句话"]},
        "完全没有 choices": {},
        "tool_calls 里的 function 是 null": {
            "choices": [{"message": {"content": "好呀~",
                                     "tool_calls": [{"id": "1", "function": None}]}}]},
        "arguments 直接给对象": {
            "choices": [{"message": {"content": "",
                                     "tool_calls": [{"id": "1", "function": {
                                         "name": "set_expression",
                                         "arguments": {"name": "星星眼"}}}]}}]},
        "arguments 是坏 JSON": {
            "choices": [{"message": {"content": "嗯…",
                                     "tool_calls": [{"id": "1", "function": {
                                         "name": "play_motion", "arguments": "{不是JSON"}}]}}]},
    }
    for label, data in cases.items():
        try:
            reply = client._to_reply(data, via_tools=True)
            check(f"{label}:能正常处理(不崩)", True)
            if label == "arguments 直接给对象":
                check("  对象形式的参数也能识别表情", reply.expression == "星星眼",
                      str(reply.expression))
            if label == "arguments 是坏 JSON":
                check("  坏参数被记进 dropped 而不是静默丢弃",
                      any("play_motion" in d for d in reply.dropped), str(reply.dropped))
                check("  正文仍然保留", reply.text == "嗯…", repr(reply.text))
        except ChatError as exc:
            check(f"{label}:给出 ChatError(而不是崩)", True)
            check(f"  错误信息可读", bool(str(exc)), str(exc))
        except Exception as exc:                    # noqa: BLE001
            check(f"{label}:给出 ChatError(而不是崩)", False,
                  f"抛了 {type(exc).__name__}: {exc}")

    print("\n[6] 只调工具不说话 → 用兜底文字,不要报'对话失败'")
    reply = client._to_reply({"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "1", "function": {"name": "set_expression", "arguments": '{"name": "星星眼"}'}}]}}]},
        via_tools=True)
    check("表情已识别", reply.expression == "星星眼", str(reply.expression))
    check("用了兜底文字", reply.text == SILENT_REPLY, repr(reply.text))
    check("标记了正文缺失", reply.text_missing is True)


def part_ui(cfg_unused=None) -> None:
    import live2d.v3 as live2d
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication, QCheckBox

    from pet import config as config_mod
    from pet.actions import load_actions
    from pet.window import PetWindow

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    live2d.init()
    app = QApplication(sys.argv[:1])

    cfg = config_mod.Config.load()
    cfg.window_x, cfg.window_y = 260, 260
    cfg.gaze_follow = False
    cfg.idle_motion = False
    cfg.idle_autonomy = False
    cfg.idle_llm_thoughts = False
    cfg.chat_hover = True

    model_dir = config_mod.find_model_dir()
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir),
                       load_actions(model_dir))
    window.enable_test_mode("robustness")
    window.show()

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    pump(4.0)
    window.input_timer.stop()

    print("\n[7] 设置面板的复选框必须真的接在界面上(不是影子控件)")
    dialog = window.make_settings_dialog()
    dialog.show()
    pump(0.8)
    check("chat_hover 复选框是面板的子控件(以前是个没进布局的新控件)",
          dialog.chat_hover in dialog.findChildren(QCheckBox))
    check("它的初始状态来自配置", dialog.chat_hover.isChecked() == cfg.chat_hover,
          f"{dialog.chat_hover.isChecked()} vs {cfg.chat_hover}")
    dialog.chat_hover.setChecked(False)
    dialog._on_save()
    pump(0.5)
    check("取消勾选并保存后配置真的变了", cfg.chat_hover is False, str(cfg.chat_hover))
    dialog.close()
    pump(0.3)

    print("\n[8] 动画序号必须采用引擎返回值(失败一次也不许整体错位)")
    pet = window.pet
    real_model = pet.model

    class _FakeModel:
        """模拟:第 2 个动画加载失败,并且引擎给的序号不是从 0 连续。"""

        def __init__(self) -> None:
            self.calls = 0

        def LoadExtraMotion(self, group, path):      # noqa: N802
            self.calls += 1
            if self.calls == 2:
                return None                          # 加载失败:没有序号
            return 100 + self.calls                  # 故意给非连续序号

    pet.model = _FakeModel()
    pet._motions.clear()
    pet._group_counts.clear()
    pet._register_motions()
    values = list(pet._motions.values())
    check(f"序号取自引擎返回值(实际 {values})", values[:2] == [101, 102], str(values))
    check("加载失败的那个用自身计数兜底", len(values) >= 3, str(values))
    pet.model = real_model

    window.hide()
    window.close()
    app.quit()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    part_config()
    part_memory()
    part_chat()
    part_ui()
    print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
