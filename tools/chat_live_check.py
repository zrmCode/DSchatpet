"""真实对话连通性检查:确认 key 能解析、接口能通,并打印一句回复。

**不会打印 API Key 本身**,只打印来源与长度。会真实调用一次接口(消耗极少额度)。

用法::

    .venv\\Scripts\\python.exe tools\\chat_live_check.py [--say "你好"]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pet import config as config_mod
from pet.chat import ChatClient, ChatError, ChatSettings


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="真实对话连通性检查")
    parser.add_argument("--say", default="用一句话跟我打个招呼")
    parser.add_argument("--actions", action="store_true",
                        help="带上模型的表情/动作清单,验证工具调用(会多花一点点额度)")
    args = parser.parse_args()

    cfg = config_mod.Config.load()

    expressions: tuple[str, ...] = ()
    motions: tuple[str, ...] = ()
    if args.actions:
        from pet.actions import KIND_EXPRESSION, KIND_MOTION, load_actions

        model_dir = config_mod.find_model_dir()
        actions = load_actions(model_dir) if model_dir else []
        expressions = tuple(a.name for a in actions if a.kind == KIND_EXPRESSION)
        motions = tuple(a.name for a in actions if a.kind == KIND_MOTION)
        print(f"表情 {len(expressions)} 个、动作 {len(motions)} 个(会作为工具候选发给模型)")

    settings = ChatSettings(
        base_url=cfg.chat_base_url,
        model=cfg.chat_model,
        api_key=cfg.chat_api_key,
        api_key_env=cfg.chat_api_key_env,
        use_dsh_credentials=cfg.chat_use_dsh_credentials,
        persona=cfg.chat_persona,
        timeout=cfg.chat_timeout,
        max_tokens=64,          # 只验证链路,少花额度
        expressions=expressions,
        motions=motions,
        use_tools=True,
    )
    client = ChatClient(settings)

    source = client.describe_key_source()
    print(f"接口地址 : {client.endpoint()}")
    print(f"模型     : {settings.model}")
    print(f"Key 来源 : {source}")

    key = client.resolve_api_key()
    if not key:
        print("\n❌ 没找到 API Key。三种可选方式:")
        print("   1) config.json 里填 chat_api_key")
        print(f"   2) 设置环境变量 {settings.api_key_env}")
        print("   3) 让 DSH 里配置好该凭据(会读 ~/.dsh/.credentials.yaml 的 refs)")
        return 2
    print(f"Key 长度 : {len(key)}(内容不打印)")

    print(f"\n发送: {args.say}")
    try:
        reply = client.ask_reply(args.say)
    except ChatError as exc:
        print(f"\n❌ 调用失败: {exc}")
        return 1

    print(f"\n✅ 回复: {reply.text}")
    if args.actions:
        print(f"   模型选的表情: {reply.expression or '(没选)'}")
        print(f"   模型选的动作: {reply.motion or '(没选)'}")
        print(f"   走了工具调用 : {'是' if reply.via_tools else '否(用了文字指令或没给)'}")
        if reply.dropped:
            print(f"   被丢弃的无效指令: {list(reply.dropped)}")
        if client.tools_rejected:
            print("   ⚠️ 这个接口不支持 tools,已自动回退到文字指令模式")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
