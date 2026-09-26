"""LLM 对话:OpenAI 兼容的 ``/chat/completions`` 客户端 + "情绪 → 表情" 映射。

只用标准库(``urllib``)实现 HTTP,不引入 ``openai`` 依赖:
桌宠要打包分发,依赖越少越好;而且这样能用本地假服务器做离线集成测试。

API Key 的解析顺序:``config.json`` 里直接填的 > 环境变量 > DSH 的凭据文件
(``~/.dsh/.credentials.yaml`` 里的 ``refs.<名字>``)。**任何日志都不打印 key**。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

#: 常见服务商的预设(填错了也能在 config.json 里覆盖)
PROVIDER_PRESETS = {
    "deepseek": ("https://api.deepseek.com", "deepseek-chat"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "ollama": ("http://127.0.0.1:11434/v1", "qwen2.5:7b"),
}

#: 设置面板里的下拉显示名
PROVIDER_LABELS = {
    "deepseek": "DeepSeek 官方(需 Key)",
    "openai": "OpenAI(需 Key)",
    "ollama": "Ollama 本地(免费,无需 Key)",
}

#: 这些主机上的服务默认不需要 API Key(本地模型/自建中转)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

#: 内置人设:气泡很小,必须限制长度
DEFAULT_PERSONA = (
    "你是「DS鲸鱼娘」,一只住在用户桌面上的深海鲸鱼娘。\n"
    "性格:黏人、好奇心重、偶尔犯困;说话口语化,爱用语气词和颜文字。\n"
    "规则:\n"
    "1. 回复要短,通常 1~3 句,总长不超过 80 字 —— 你说话的地方是一个小气泡,写长了显示不下。\n"
    "2. 只用纯文本,不要 Markdown 标题、列表、代码块。\n"
    "3. 不要自称 AI 或语言模型;不知道的事就直说不懂。\n"
    "4. 用「你」称呼用户。"
)

#: 让模型"会用表情和动作"的附加规则(仅当目录可用时附加)
ACTION_PROTOCOL = (
    "\n\n【表情与动作】\n"
    "你有一副 Live2D 身体,可以换表情、做动作。让它们配合你的语气,别每次都换。\n"
    "换表情用工具 set_expression,做动作用工具 play_motion(如果这个接口不支持工具调用,\n"
    "就在回复**末尾**附上指令,格式: [表情:名字] [动作:名字])。\n"
    "**无论做什么动作,都一定要带一句很短的话**(哪怕只有两个字),不要只调工具不出声。\n"
    "{catalog}"
)

#: 模型只给了动作、一句话都没说时的兜底显示(避免气泡空着或直接报错)
SILENT_REPLY = "……"

#: 待机"内心活动"的提示词:让它自己找点事做
IDLE_THOUGHT_PROMPT = (
    "现在没人跟你说话,你在桌面上待机。用**一句不超过 20 字**的话说说你现在在想什么或想做什么,\n"
    "并配一个合适的表情(需要的话再加一个动作)。只输出那句话,不要解释。"
)

#: 用户"戳一下"(鼠标点击模型)时给模型的说明。
#: 点击不再是"随机换个表情"的机器反射,而是**一次由模型理解并回应的互动**:
#: 它要知道自己被戳了、被戳到哪儿,然后用一句话 + 一个表情回应。
POKE_PROMPT = (
    "【刚刚发生的事】\n"
    "用户用鼠标**戳了你一下**(是点击你的身体,不是在跟你打字)。\n"
    "用一句很短的话回应这个动作(别提问、别长篇大论),并挑一个合适的表情配上去;"
    "开心、害羞、被吓一跳、气鼓鼓、犯困都可以,按你的性格来。"
)

#: 记忆抽取:把最近的对话交给模型,让它挑出值得长期记住的事
MEMORY_EXTRACT_SYSTEM = "你是一个只输出 JSON 的信息抽取器,不要输出任何解释或多余文字。"
MEMORY_EXTRACT_PROMPT = (
    "从下面这段对话里挑出**值得长期记住**的信息,只输出 JSON 数组。\n"
    "要记的:用户本人的事实(名字/工作/项目/所处状态)、用户的偏好与习惯、\n"
    "用户与你的约定、以及对你来说重要的事件。\n"
    "不要记:寒暄、一次性的闲聊、你自己说的话、以及含义不明的碎片。\n"
    "格式:[{\"text\":\"一句话,第三人称,不超过 40 字\",\"kind\":\"fact|preference|event|promise\","
    "\"importance\":1-5}]\n"
    "最多 3 条;没有值得记的就输出 []。\n\n对话:\n{conversation}"
)


def build_memory_extract_prompt(conversation: str) -> str:
    """渲染抽取提示词。

    ⚠️ **不能用 str.format()**:模板里本来就含 JSON 花括号,会被当成占位符
    (实测抛 KeyError: '"text"'),这里用最朴素的替换。
    """
    return MEMORY_EXTRACT_PROMPT.replace("{conversation}", conversation)

#: 文字指令的正则:兼容半角/全角括号与中英文写法
_DIRECTIVE_RE = re.compile(
    r"[\[【(]\s*(表情|动作|expression|expr|motion|action)\s*[:：]\s*([^\]】)]+?)\s*[\]】)]"
)
_KIND_EXPRESSION = {"表情", "expression", "expr"}
_KIND_MOTION = {"动作", "motion", "action"}


class ChatError(RuntimeError):
    """对话链路上的可预期错误(网络、鉴权、返回格式)。"""


@dataclass
class ChatSettings:
    """一次对话所需的配置(resolve_api_key 之前 api_key 可能是空的)。"""

    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    api_key: str = ""
    api_key_env: str = "DEEPSEEK_API_KEY"
    use_dsh_credentials: bool = True
    persona: str = ""
    history_turns: int = 10
    timeout: float = 30.0
    temperature: float = 1.0
    max_tokens: int = 400
    #: 可用表情/动作名(用于注入提示词与校验模型的选择)
    expressions: tuple[str, ...] = ()
    motions: tuple[str, ...] = ()
    #: 是否优先用工具调用让模型"自己选表情/动作";不支持时自动回退到文字指令
    use_tools: bool = True


@dataclass
class ChatReply:
    """一次回复的结构化结果。"""

    text: str                                   # 去掉指令后的正文(可直接显示)
    expression: str | None = None               # 模型选择的表情(已校验存在)
    motion: str | None = None                   # 模型选择动作(已校验存在)
    via_tools: bool = False                     # 是否走了工具调用
    dropped: tuple[str, ...] = ()               # 被丢弃的无效指令(便于诊断)
    text_missing: bool = False                  # 模型没写字、只有动作(已用兜底文字)


def build_tools(expressions: Sequence[str], motions: Sequence[str]) -> list[dict]:
    """生成 OpenAI 兼容的工具定义(表情/动作的候选用 enum 限定,减少乱选)。"""
    tools: list[dict] = []
    if expressions:
        tools.append({
            "type": "function",
            "function": {
                "name": "set_expression",
                "description": "切换你当前的表情,让它配合你的语气。每次回复最多调一次;不需要换就别调。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": list(expressions),
                                 "description": "表情名,只能从候选里选"},
                    },
                    "required": ["name"],
                },
            },
        })
    if motions:
        tools.append({
            "type": "function",
            "function": {
                "name": "play_motion",
                "description": "做一个动作动画。会打断当前动作,只在真的合适时用,不要每句都调。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": list(motions),
                                 "description": "动作名,只能从候选里选"},
                    },
                    "required": ["name"],
                },
            },
        })
    return tools


def parse_actions(text: str, expressions: Sequence[str] = (),
                  motions: Sequence[str] = ()) -> ChatReply:
    """从回复文本里解析 ``[表情:xx]`` / ``[动作:xx]`` 指令。

    - 兼容全角括号与中英文写法;
    - **校验名字是否真实存在**,不存在的指令丢弃(绝不让模型把状态改坏);
    - 返回的 text 已经去掉所有指令,可直接显示。
    """
    expression: str | None = None
    motion: str | None = None
    dropped: list[str] = []

    for match in _DIRECTIVE_RE.finditer(text or ""):
        kind = match.group(1).strip().lower()
        name = match.group(2).strip()
        if kind in _KIND_EXPRESSION:
            if name in expressions:
                expression = name
            else:
                dropped.append(match.group(0))
        elif kind in _KIND_MOTION:
            if name in motions:
                motion = name
            else:
                dropped.append(match.group(0))

    clean = _DIRECTIVE_RE.sub("", text or "").strip()
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    return ChatReply(text=clean, expression=expression, motion=motion,
                     dropped=tuple(dropped))


def read_dsh_credential(name: str, dsh_home: Path | None = None) -> str | None:
    """从 DSH 的凭据文件里读一个 key。

    DSH 的 ``.credentials.yaml`` 结构很扁平(``version`` / ``refs.<名字>``),
    为避免引入 PyYAML 依赖,这里只做窄解析;格式对不上就返回 None 走别的来源。
    """
    home = dsh_home or Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    path = home / ".credentials.yaml"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    pattern = re.compile(rf"^\s+{re.escape(name)}\s*:\s*(.+?)\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            value = match.group(1).strip().strip("'\"")
            if value:
                return value
    return None


class ChatClient:
    """最小的 OpenAI 兼容客户端(非流式)。"""

    def __init__(self, settings: ChatSettings) -> None:
        self.settings = settings
        self._resolved_key: str | None = None
        #: 接口是否支持 tools;被拒一次后就改成文字指令模式,不再反复试错
        self._tools_supported = True
        #: 诊断用:是否遇到过"接口不认 tools"
        self.tools_rejected = False

    # ---------------------------------------------------------------- 凭据

    def resolve_api_key(self) -> str:
        if self._resolved_key is not None:
            return self._resolved_key

        key = (self.settings.api_key or "").strip()
        if not key and self.settings.api_key_env:
            key = (os.environ.get(self.settings.api_key_env) or "").strip()
        if not key and self.settings.use_dsh_credentials:
            for name in filter(None, (self.settings.api_key_env, "DEEPSEEK_API_KEY")):
                found = read_dsh_credential(name)
                if found:
                    key = found
                    break

        self._resolved_key = key
        return key

    def describe_key_source(self) -> str:
        """给用户看的一句话说明(不含 key 本身)。"""
        if (self.settings.api_key or "").strip():
            return "config.json 的 chat_api_key"
        env = self.settings.api_key_env
        if env and (os.environ.get(env) or "").strip():
            return f"环境变量 {env}"
        if self.settings.use_dsh_credentials and read_dsh_credential(env or "DEEPSEEK_API_KEY"):
            return "DSH 凭据文件(~/.dsh/.credentials.yaml)"
        if self.is_local_endpoint():
            return "本地服务(无需 Key)"
        return "未找到"

    def is_local_endpoint(self) -> bool:
        """接口是否指向本机(本地模型/自建服务通常不需要 Key)。"""
        try:
            host = urlparse(self.endpoint()).hostname or ""
        except ValueError:
            return False
        return host in LOCAL_HOSTS

    def available(self) -> bool:
        """对话是否可用:有 Key,或指向本机服务。

        不可用时**不该报错**——桌宠要能安静地当"纯桌宠"用(用户可选择填 Key 或本地模型)。
        """
        return bool(self.resolve_api_key()) or self.is_local_endpoint()

    # ---------------------------------------------------------------- 请求

    def endpoint(self) -> str:
        base = (self.settings.base_url or "").rstrip("/")
        if not base:
            return "https://api.deepseek.com/chat/completions"
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def build_messages(self, user_text: str, history: list[dict] | None = None,
                       extra_system: str = "") -> list[dict]:
        persona = (self.settings.persona or "").strip() or DEFAULT_PERSONA
        if self.settings.expressions or self.settings.motions:
            from .actions import describe_catalog

            catalog = describe_catalog(list(self.settings.expressions), list(self.settings.motions))
            persona += ACTION_PROTOCOL.format(catalog=catalog)
        if extra_system:
            persona += "\n" + extra_system
        messages: list[dict] = [{"role": "system", "content": persona}]
        if history:
            keep = max(0, self.settings.history_turns) * 2
            for item in (history[-keep:] if keep else []):
                # ⚠️ 必须丢掉空正文的历史:OpenAI 兼容接口会以 400 拒绝 content 为
                # 空串/None 的消息 —— 一旦模型某次只调了工具没写字,下一句就会"对话失败"。
                role = item.get("role")
                content = item.get("content")
                if role and isinstance(content, str) and content.strip():
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_text})
        return messages

    def complete(self, messages: list[dict]) -> ChatReply:
        """发一次请求,返回结构化的 ChatReply(正文 + 模型选的表情/动作)。

        工具调用优先;若接口不支持 tools(报错提到 tool/function),**自动去掉 tools 重试一次**,
        之后这个 client 实例就一直用文字指令模式,不再反复试错。
        """
        key = self.resolve_api_key()
        local = self.is_local_endpoint()
        if not key and not local:
            raise ChatError(
                "还没配置对话 Key:右键桌宠 →「设置…」→ 对话 里填一个,"
                "或把接口地址改成本机服务(如 Ollama)。不填也能当纯桌宠玩。"
            )

        tools = build_tools(self.settings.expressions, self.settings.motions)
        want_tools = bool(tools) and self.settings.use_tools and self._tools_supported

        try:
            data = self._post(messages, key, tools if want_tools else None)
        except ChatError as exc:
            if want_tools and _looks_like_tools_unsupported(str(exc)):
                # 这个接口不认 tools → 记住并重试,确保老模型/中转站也能用
                self._tools_supported = False
                self.tools_rejected = True
                data = self._post(messages, key, None)
            else:
                raise

        return self._to_reply(data if isinstance(data, dict) else {}, want_tools)

    def _post(self, messages: list[dict], key: str, tools: list[dict] | None) -> dict:
        body: dict = {
            "model": self.settings.model,
            "messages": messages,
            "stream": False,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        # 本地服务(如 Ollama)通常不校验鉴权头,没有 Key 时不带 Authorization

        request = urllib.request.Request(
            self.endpoint(),
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers=headers,
        )

        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise ChatError(f"接口返回 HTTP {exc.code}:{detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise ChatError(f"连不上对话接口({exc.reason});检查网络或 chat_base_url") from exc
        except TimeoutError as exc:
            raise ChatError("对话接口超时") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ChatError(f"接口返回的不是 JSON:{raw[:200]}") from exc

    def _to_reply(self, data: dict, via_tools: bool) -> ChatReply:
        """把原始返回整理成 ChatReply:工具调用与文字指令都收进来。

        ⚠️ 关键:模型**只调工具、不写正文**时(``content`` 为 None 或空串),
        以前会直接抛「没有找到回复正文」让用户看到"对话失败";现在改为用兜底文字,
        动作照样生效。
        """
        expression: str | None = None
        motion: str | None = None
        dropped: list[str] = []
        text_missing = False

        choices = data.get("choices") or []
        #: ⚠️ ``choices[0]`` 可能是 ``null``(实测有的中转站会回 ``{"choices":[null]}``),
        #: 而 ``message`` 也可能是 ``null`` —— 以前直接 ``choices[0].get(...)`` 会抛
        #: ``AttributeError``,绕过 ``ChatError`` 这套约定:错误没人接住,
        #: 气泡永远停在「…」、``_chat_busy`` 也收不回来。
        first = choices[0] if isinstance(choices, list) and choices else None
        message = (first.get("message") or {}) if isinstance(first, dict) else {}
        if not isinstance(message, dict):
            message = {}
        content = message.get("content")
        text = content.strip() if isinstance(content, str) else ""

        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            raw_args = function.get("arguments")
            if isinstance(raw_args, dict):
                args = raw_args               # 少数实现直接给对象而不是 JSON 字符串
            else:
                try:
                    args = json.loads(raw_args or "{}")
                except (json.JSONDecodeError, TypeError):
                    #: 坏 JSON 也别静默丢掉整条工具调用:记进 dropped 供日志排查
                    dropped.append(f"{name or 'unknown'}(参数解析失败)")
                    continue
            if not isinstance(args, dict):
                dropped.append(f"{name or 'unknown'}(参数不是对象)")
                continue
            chosen = str(args.get("name") or "").strip()
            if name == "set_expression":
                if chosen in self.settings.expressions:
                    expression = chosen
                    via_tools = True
                elif chosen:
                    dropped.append(f"set_expression({chosen})")
            elif name == "play_motion":
                if chosen in self.settings.motions:
                    motion = chosen
                    via_tools = True
                elif chosen:
                    dropped.append(f"play_motion({chosen})")

        # 文字指令(接口不支持工具、或模型习惯写指令时)
        parsed = parse_actions(text, self.settings.expressions, self.settings.motions)
        text = parsed.text
        expression = expression or parsed.expression
        motion = motion or parsed.motion
        dropped.extend(parsed.dropped)

        if not text:
            try:
                text = extract_reply(data)
            except ChatError:
                if expression or motion:
                    # 只有动作没有说话:用兜底显示,别让整次对话变成"失败"
                    text = SILENT_REPLY
                    text_missing = True
                else:
                    raise

        return ChatReply(text=text, expression=expression, motion=motion,
                         via_tools=via_tools, dropped=tuple(dropped),
                         text_missing=text_missing)

    def ask_reply(self, user_text: str, history: list[dict] | None = None) -> ChatReply:
        """发一句话,拿回完整结果(正文 + 模型选的表情/动作)。"""
        return self.complete(self.build_messages(user_text, history))

    def ask(self, user_text: str, history: list[dict] | None = None) -> str:
        """只要正文的便捷入口(诊断/连通性检查用)。"""
        return self.ask_reply(user_text, history).text

    def think(self) -> ChatReply:
        """待机时的"内心活动":让模型自己说一句 + 选个表情/动作。"""
        messages = self.build_messages("", None, extra_system=IDLE_THOUGHT_PROMPT)
        messages = messages[:-1]          # 去掉空 user 消息
        messages.append({"role": "user", "content": IDLE_THOUGHT_PROMPT})
        return self.complete(messages)

    def utility_text(self, prompt: str, system: str = MEMORY_EXTRACT_SYSTEM,
                     max_tokens: int = 300) -> str:
        """内部工具式调用:不带人设、不挂工具,只要一段文本(记忆抽取等用)。

        失败不抛异常,返回空串 —— 养成类后台任务不该打断用户。
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        saved = self.settings.max_tokens
        try:
            self.settings.max_tokens = max_tokens
            data = self._post(messages, self.resolve_api_key(), None)
            return extract_reply(data)
        except (ChatError, Exception):
            return ""
        finally:
            self.settings.max_tokens = saved


def _looks_like_tools_unsupported(detail: str) -> bool:
    """判断错误信息是不是"这个接口不支持 tools"。"""
    lowered = detail.lower()
    if "tool" not in lowered and "function" not in lowered:
        return False
    markers = ("not support", "unsupported", "unknown", "invalid", "unrecognized",
               "不支持", "无法识别", "未知")
    return any(marker in lowered for marker in markers)


def extract_reply(data: dict) -> str:
    """兼容各家(以及中转站)略有差异的返回结构。"""
    choices = data.get("choices") or []
    if isinstance(choices, list) and choices:
        choice = choices[0] or {}
        #: 有的实现会把 choice 写成字符串/数组 —— 非 dict 一律当"没有正文"处理,
        #: 别抛 AttributeError(那会绕过 ChatError 约定,UI 状态收不回来)
        if isinstance(choice, dict):
            message = choice.get("message") or {}
            if not isinstance(message, dict):
                message = {}
            for candidate in (message.get("content"), choice.get("text")):
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
    for key in ("content", "output_text", "reply", "answer", "response"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ChatError("接口返回里没有找到回复正文")


# ---------------------------------------------------------------- 情绪映射

#: (关键词, 表情显示名) —— **按顺序**匹配,命中第一个存在的表情。
#: 排序原则:具体情绪词优先,「?/!」这类标点信号最泛,放到最后,
#: 否则"我也喜欢你!"会被标点抢走判成感叹号。
MOOD_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("爱你", "比心", "么么", "喜欢你"), "爱心眼"),
    (("喜欢", "害羞", "脸红"), "脸红"),
    (("哈哈", "嘿嘿", "笑", "开心", "好耶", "太棒"), "开心兴奋"),
    (("呜", "难过", "抱歉", "对不起", "可惜"), "悲伤"),
    (("哼", "讨厌", "生气", "别闹"), "生气"),
    (("略", "才不", "嘻嘻", "调皮"), "调皮"),
    (("晕", "不懂", "emmm", "唔"), "晕晕"),
    (("汗", "尴尬", "...", "…"), "流汗"),
    (("?", "？", "吗", "什么", "为什么", "怎么"), "问号"),
    (("!", "！", "哇", "厉害", "居然"), "感叹号"),
)

#: 思考中用的表情(不是所有模型都有,没有就跳过)
THINKING_EXPRESSIONS = ("呆呆眼", "晕晕")


def pick_expression(text: str, available: list[str] | None = None) -> str | None:
    """从回复文本里挑一个合适的表情;``available`` 限定为模型真正拥有的表情。"""
    if not text:
        return None
    pool = set(available) if available else None
    for keywords, name in MOOD_RULES:
        if any(keyword in text for keyword in keywords):
            if pool is None or name in pool:
                return name
    return None


def pick_thinking_expression(available: list[str] | None = None) -> str | None:
    pool = set(available) if available else None
    for name in THINKING_EXPRESSIONS:
        if pool is None or name in pool:
            return name
    return None
