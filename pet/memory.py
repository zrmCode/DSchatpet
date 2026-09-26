"""本地养成档案:个性、长期记忆、关系数值。

设计原则:**人格与记忆都存本地,模型只是"演员"**。
换模型 / 换中转站 / 换 Key 时,同一份档案会生成同一段人格提示,所以养成的个性不会丢;
档案还能导出导入,方便备份或迁移。

文件(默认在 ``APP_DIR/memory/`` 下,纯文本,便于备份与手改):

- ``profile.json``    个性档案:名字、对你的称呼、性格、喜好、亲密度、统计、里程碑
- ``memories.jsonl``  长期记忆,每行一条 JSON
- ``history.jsonl``   最近对话(滚动保留,跨重启连续)

隐私:这些文件**只在本机**;只有"自动抽取记忆"那一步会把最近的对话发给所配置的
模型服务(用本地 Ollama 时不出机器)。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from .config import APP_DIR

MEMORY_DIR = APP_DIR / "memory"
PROFILE_PATH = MEMORY_DIR / "profile.json"
MEMORIES_PATH = MEMORY_DIR / "memories.jsonl"
HISTORY_PATH = MEMORY_DIR / "history.jsonl"

#: 亲密度档位:(所需点数, 档位名, 给模型看的语气说明)
AFFINITY_LEVELS: tuple[tuple[int, str, str], ...] = (
    (0, "陌生", "你们刚认识,礼貌但有点拘谨,称呼对方为「你」。"),
    (30, "熟悉", "你们已经聊过不少次,语气自然放松,会主动接话。"),
    (120, "亲近", "你们很熟了,会开玩笑、撒娇,偶尔提起之前聊过的事。"),
    (400, "挚友", "你们是关系很好的老朋友,说话随意亲昵,会关心对方的近况。"),
)

#: 里程碑:达到点数或次数时记一笔
MILESTONES: tuple[tuple[str, int, str], ...] = (
    ("chat_count", 1, "第一次聊天"),
    ("chat_count", 10, "聊满 10 句"),
    ("chat_count", 50, "聊满 50 句"),
    ("chat_count", 200, "聊满 200 句"),
    ("chat_count", 1000, "聊满 1000 句"),
)

#: 用户教它记忆时的开头词
MANUAL_MEMORY_PREFIXES = ("记住", "记一下", "记下来", "记着", "别忘了")

#: 记忆类型
KIND_FACT = "fact"
KIND_PREFERENCE = "preference"
KIND_EVENT = "event"
KIND_PROMISE = "promise"
KIND_OTHER = "other"
KIND_NAMES = {
    KIND_FACT: "事实", KIND_PREFERENCE: "偏好", KIND_EVENT: "事件",
    KIND_PROMISE: "约定", KIND_OTHER: "其它",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write(path: Path, text: str) -> None:
    """原子写入:先写临时文件再改名,避免写一半断电把档案弄坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _normalize(text: str) -> str:
    """用于查重:去掉空白与标点。"""
    return re.sub(r"[\s,。!?！?、;:：;'\"“”‘’()()\[\]【】~～…—\-]+", "", text or "")


def _ngrams(text: str, size: int = 2) -> set[str]:
    """字符 n-gram —— 中文按字切分比按词更稳,且零依赖。"""
    cleaned = _normalize(text)
    if len(cleaned) <= size:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i + size] for i in range(len(cleaned) - size + 1)}


def affinity_level(points: int) -> tuple[str, str]:
    """按点数返回 (档位名, 语气说明)。"""
    name, tone = AFFINITY_LEVELS[0][1], AFFINITY_LEVELS[0][2]
    for threshold, level_name, level_tone in AFFINITY_LEVELS:
        if points >= threshold:
            name, tone = level_name, level_tone
        else:
            break
    return name, tone


@dataclass
class Profile:
    """它是谁、和你是什么关系。"""

    name: str = "DS鲸鱼娘"
    user_title: str = "你"                 # 怎么称呼用户
    traits: list[str] = field(default_factory=lambda: ["黏人", "好奇心重", "偶尔犯困"])
    likes: list[str] = field(default_factory=list)
    dislikes: list[str] = field(default_factory=list)
    affinity: int = 0                      # 亲密度点数
    chat_count: int = 0                    # 累计对话轮数
    first_met: str = field(default_factory=lambda: date.today().isoformat())
    last_seen: str = field(default_factory=_now_iso)
    milestones: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- 派生

    @property
    def level(self) -> str:
        return affinity_level(self.affinity)[0]

    @property
    def tone(self) -> str:
        return affinity_level(self.affinity)[1]

    @property
    def days_together(self) -> int:
        try:
            started = date.fromisoformat(self.first_met)
        except ValueError:
            return 1
        return max(1, (date.today() - started).days + 1)

    def touch(self, chats: int = 1, affinity: int = 1) -> list[str]:
        """记一次对话,返回本次**新解锁**的里程碑。"""
        self.chat_count += chats
        self.affinity += affinity
        self.last_seen = _now_iso()
        unlocked = []
        for key, need, label in MILESTONES:
            if key == "chat_count" and self.chat_count >= need and label not in self.milestones:
                self.milestones.append(label)
                unlocked.append(label)
        return unlocked

    # ---------------------------------------------------------------- 读写

    @classmethod
    def load(cls, path: Path = PROFILE_PATH) -> "Profile":
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            known = {f for f in cls().__dict__}
            profile = cls()
            for key, value in raw.items():
                if key in known:
                    setattr(profile, key, value)
            return profile
        return cls()

    def save(self, path: Path = PROFILE_PATH) -> None:
        _atomic_write(path, json.dumps(asdict(self), ensure_ascii=False, indent=2))


@dataclass
class Memory:
    """一条长期记忆。"""

    id: str
    text: str
    kind: str = KIND_FACT
    importance: int = 3
    tags: tuple[str, ...] = ()
    source: str = "auto"                   # auto / manual
    created_at: str = field(default_factory=_now_iso)

    @property
    def timestamp(self) -> float:
        try:
            return datetime.fromisoformat(self.created_at).timestamp()
        except ValueError:
            return time.time()

    @property
    def kind_label(self) -> str:
        return KIND_NAMES.get(self.kind, self.kind)

    def to_json(self) -> str:
        return json.dumps({
            "id": self.id, "text": self.text, "kind": self.kind,
            "importance": self.importance, "tags": list(self.tags),
            "source": self.source, "created_at": self.created_at,
        }, ensure_ascii=False)

    @classmethod
    def from_dict(cls, raw: dict) -> "Memory | None":
        text = str(raw.get("text") or "").strip()
        if not text:
            return None
        try:
            importance = int(raw.get("importance") or 3)
        except (TypeError, ValueError):
            importance = 3
        tags = raw.get("tags") or []
        return cls(
            id=str(raw.get("id") or f"m{time.time_ns()}"),
            text=text[:300],
            kind=str(raw.get("kind") or KIND_FACT),
            importance=max(1, min(5, importance)),
            tags=tuple(str(t) for t in tags if str(t).strip()),
            source=str(raw.get("source") or "auto"),
            created_at=str(raw.get("created_at") or _now_iso()),
        )


class MemoryStore:
    """长期记忆库:去重、限量、轻量相关性检索。"""

    #: 超过这个条数就开始淘汰(按分数保留最重要的)
    DEFAULT_CAP = 200

    def __init__(self, path: Path = MEMORIES_PATH, cap: int = DEFAULT_CAP) -> None:
        self.path = path
        self.cap = cap
        self._items: list[Memory] = []
        #: id → 单调递增的插入序号。用作淘汰排序的**次级键**(见 ``add``)。
        #: ⚠️ 不能拿列表下标当插入序号:淘汰会重排列表,下标就不再等于插入顺序,
        #: 于是"同重要度同时间"时会丢掉刚写入的那条(实测踩过)。
        self._order: dict[str, int] = {}
        self._seq = 0
        self.load()

    # ---------------------------------------------------------------- 读写

    def load(self) -> None:
        self._items = []
        if not self.path.is_file():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue                       # 半行/坏行:跳过这一条,别让整个库加载失败
                #: ⚠️ 合法 JSON 但**不是对象**(``5`` / ``null`` / ``[1,2]``)也要挡住:
                #: 以前只 catch JSONDecodeError,``Memory.from_dict`` 会在这些行上抛
                #: ``AttributeError``,整个记忆库直接加载失败(一条坏行毁掉全部记忆)。
                if not isinstance(raw, dict):
                    continue
                try:
                    memory = Memory.from_dict(raw)
                except (AttributeError, TypeError, ValueError):
                    continue
                if memory:
                    self._items.append(memory)
                    self._seq += 1
                    self._order[memory.id] = self._seq      # 文件顺序即插入顺序
        except OSError:
            pass

    def save(self) -> None:
        _atomic_write(self.path, "\n".join(m.to_json() for m in self._items) + "\n"
                      if self._items else "")

    # ---------------------------------------------------------------- 增删

    def is_duplicate(self, text: str) -> bool:
        """高重合度的视为同一条,避免"我很困"被记十遍。"""
        grams = _ngrams(text)
        if not grams:
            return True
        for existing in self._items:
            other = _ngrams(existing.text)
            if not other:
                continue
            overlap = len(grams & other) / min(len(grams), len(other))
            if overlap >= 0.8:
                return True
        return False

    def add(self, memory: Memory) -> bool:
        """加入一条记忆;重复则跳过并返回 False。"""
        if self.is_duplicate(memory.text):
            return False
        self._items.append(memory)
        self._seq += 1
        self._order[memory.id] = self._seq
        if len(self._items) > self.cap:
            #: 淘汰排序:重要度 → 写入时间 → **插入序号**(都是降序,新写入的胜出)。
            #: ⚠️ 以前是"稳定排序 + 截尾":同重要度且同一秒写入时(测试与连续记忆抽取
            #: 都会出现),稳定排序保持插入顺序,截掉的恰好是**最新**那条 —— 与
            #: "记住最近发生的事"完全相反。
            ranked = sorted(
                self._items,
                key=lambda item: (item.importance, item.timestamp,
                                  self._order.get(item.id, 0)),
                reverse=True,
            )
            self._items = ranked[: self.cap]
            alive = {item.id for item in self._items}
            self._order = {key: value for key, value in self._order.items() if key in alive}
        return True

    def remove(self, memory_id: str) -> bool:
        before = len(self._items)
        self._items = [m for m in self._items if m.id != memory_id]
        return len(self._items) != before

    def clear(self) -> None:
        self._items = []

    # ---------------------------------------------------------------- 查询

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> list[Memory]:
        return list(self._items)

    def search(self, query: str, limit: int = 6) -> list[Memory]:
        """轻量相关性检索:**先按"有没有命中话题"分层**,层内再比加权分。

        ⚠️ 以前的公式 ``overlap*2 + importance*0.6 + recency*0.5`` 有个结构性毛病:
        重要度(最高 5 → 3.0 分)与新鲜度(最高 0.5 分)是**恒定加成**,
        而相关性被"命中词数 ÷ 查询词数"一除,长查询里命中两三个词也只有 0.2~0.4 ——
        于是"完全无关但重要又新"的记忆会压过"确实命中话题的老记忆",
        注入人设的记忆跟当前话题毫不相干。现在用分层键:
        有命中的(第 1 层)永远排在没命中的(第 0 层)前面,层内才比权重。
        """
        if not self._items:
            return []
        now = time.time()
        grams = _ngrams(query)
        scored: list[tuple[tuple[int, float], Memory]] = []
        for memory in self._items:
            other = _ngrams(memory.text)
            overlap = (len(grams & other) / max(1, len(grams))) if grams else 0.0
            recency = 1.0 / (1.0 + (now - memory.timestamp) / (30 * 86400))
            weight = overlap * 4.0 + min(max(memory.importance, 0), 5) * 0.3 + recency * 0.5
            if memory.source == "manual":
                weight += 0.3       # 用户亲口教的,优先级稍高
            scored.append(((1 if overlap > 0 else 0, weight), memory))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [memory for _, memory in scored[:limit]]


class HistoryStore:
    """最近对话(跨重启连续)。"""

    def __init__(self, path: Path = HISTORY_PATH, keep_turns: int = 40) -> None:
        self.path = path
        self.keep_turns = keep_turns
        self._items: list[dict] = []
        self.load()

    def load(self) -> None:
        self._items = []
        if not self.path.is_file():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and item.get("role") and isinstance(item.get("content"), str):
                    self._items.append({"role": item["role"], "content": item["content"]})
        except OSError:
            pass

    def save(self) -> None:
        keep = max(2, self.keep_turns) * 2
        self._items = self._items[-keep:]
        _atomic_write(self.path, "\n".join(json.dumps(i, ensure_ascii=False) for i in self._items) + "\n"
                      if self._items else "")

    def append(self, role: str, content: str) -> None:
        if not content or not content.strip():
            return          # 空正文绝不能进历史(会让接口 400)
        self._items.append({"role": role, "content": content})
        keep = max(2, self.keep_turns) * 2
        if len(self._items) > keep:
            self._items = self._items[-keep:]

    def recent(self, turns: int) -> list[dict]:
        keep = max(0, turns) * 2
        return self._items[-keep:] if keep else []

    def clear(self) -> None:
        self._items = []


# ---------------------------------------------------------------- 提示词拼装


def build_persona_block(profile: Profile, memories: list[Memory],
                        max_chars: int = 800) -> str:
    """把档案 + 记忆渲染成注入提示词的文本。

    ⚠️ 只依赖本地档案 —— 所以换模型时这段内容完全一致,个性不会丢。
    """
    lines = ["\n【你和这个人的关系】"]
    lines.append(f"- 你叫「{profile.name}」,称呼对方为「{profile.user_title}」。")
    lines.append(f"- 你们已经相处 {profile.days_together} 天,聊过 {profile.chat_count} 句,"
                 f"关系档位:{profile.level}。")
    lines.append(f"- 语气要求:{profile.tone}")
    if profile.traits:
        lines.append(f"- 你的性格:{'、'.join(profile.traits)}。")
    if profile.likes:
        lines.append(f"- 你喜欢:{'、'.join(profile.likes)}。")
    if profile.dislikes:
        lines.append(f"- 你不喜欢:{'、'.join(profile.dislikes)}。")
    if profile.milestones:
        lines.append(f"- 你们的里程碑:{'、'.join(profile.milestones[-6:])}。")

    if memories:
        lines.append("\n【你记得关于对方的事(自然地带出来,别像念清单)】")
        for memory in memories:
            lines.append(f"- ({memory.kind_label}) {memory.text}")

    lines.append("\n要求:把上面的记忆当成**你自己经历过的事**,不要说出「根据记忆」这类话;"
                 "没记住的事就坦白说不记得,不要编造。")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n(记忆过长,已截断)"
    return text


def parse_manual_memory(text: str) -> str | None:
    """识别"记住:xxx"这类指令,返回要记住的内容。"""
    stripped = (text or "").strip()
    for prefix in MANUAL_MEMORY_PREFIXES:
        if stripped.startswith(prefix):
            rest = stripped[len(prefix):].lstrip(":：,、。 　")
            return rest.strip() or None
    return None


def export_bundle(profile: Profile, memories: list[Memory], path: Path) -> None:
    """把养成档案导出成一个 JSON 文件(换电脑/换模型/分享都能带走)。"""
    payload = {
        "format": "dsh-deep-whale-profile",
        "version": 1,
        "exported_at": _now_iso(),
        "profile": asdict(profile),
        "memories": [json.loads(m.to_json()) for m in memories],
    }
    _atomic_write(Path(path), json.dumps(payload, ensure_ascii=False, indent=2))


def import_bundle(path: Path) -> tuple[Profile, list[Memory]]:
    """读回导出文件;格式不对就抛 ValueError(调用方提示用户)。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "profile" not in raw:
        raise ValueError("不是有效的养成档案文件")
    profile = Profile()
    for key, value in (raw.get("profile") or {}).items():
        if key in profile.__dict__:
            setattr(profile, key, value)
    memories: list[Memory] = []
    for item in raw.get("memories") or []:
        memory = Memory.from_dict(item if isinstance(item, dict) else {"text": str(item)})
        if memory:
            memories.append(memory)
    return profile, memories


def parse_extracted_memories(raw: str) -> list[Memory]:
    """从模型返回的文本里解析记忆 JSON(容忍 markdown 代码块与多余文字)。"""
    if not raw:
        return []
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()

    payload = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\[.*\]|\{.*\})", text, re.S)
        if match:
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                payload = None

    if payload is None:
        return []
    if isinstance(payload, dict):
        payload = payload.get("memories") or payload.get("items") or [payload]
    if not isinstance(payload, list):
        return []

    memories: list[Memory] = []
    for item in payload:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        memory = Memory.from_dict({**item, "source": "auto"})
        if memory:
            memories.append(memory)
    return memories
