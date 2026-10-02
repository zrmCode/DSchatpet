"""桌宠配置:模型路径、窗口与行为参数,持久化到项目内的 config.json。

配置文件刻意放在项目目录(而不是 %APPDATA%),这样整个文件夹可以直接拷走,
后面打包成绿色版/安装包时也不用额外处理。
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def _is_frozen() -> bool:
    """是否运行在 PyInstaller 打出的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def _app_dir() -> Path:
    """**可写**目录:打包后是 exe 所在目录,开发时是项目根目录。

    配置与日志都放这里,这样绿色版可以直接拷走;exe 放在 Program Files 时需要
    用户有写权限,否则请装到用户目录或改用手动配置。
    """
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_dir() -> Path:
    """**只读**资源目录:打包后是 PyInstaller 的解包目录,开发时与 APP_DIR 相同。"""
    if _is_frozen():
        return Path(getattr(sys, "_MEIPASS", _app_dir()))
    return _app_dir()


#: 可写目录(配置文件、日志)
APP_DIR = _app_dir()
#: 只读资源目录(打包进 exe 的模型等)
BUNDLE_DIR = _bundle_dir()
#: 兼容旧命名
PROJECT_DIR = APP_DIR

CONFIG_PATH = APP_DIR / "config.json"

#: 模型清单文件名(自动探测,避免把模型文件名写死在代码里)
MODEL_JSON_GLOB = "*.model3.json"


def find_model_dir(explicit: str | None = None) -> Path | None:
    """按优先级定位模型目录。

    顺序:显式传入 > 打包资源/assets/model > 可写目录/assets/model > 工作区原始模型目录。
    找到的第一个含 ``*.model3.json`` 的目录即返回;都没有则返回 None。
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend([
        BUNDLE_DIR / "assets" / "model",
        APP_DIR / "assets" / "model",
        APP_DIR.parent / "DS鲸鱼娘" / "鼠控版",
        BUNDLE_DIR.parent / "DS鲸鱼娘" / "鼠控版",
    ])

    for cand in candidates:
        if cand.is_dir() and any(cand.glob(MODEL_JSON_GLOB)):
            return cand
    return None


def find_model_json(model_dir: Path) -> Path:
    """在模型目录里找到 .model3.json(live2d-py 的加载入口)。"""
    matches = sorted(model_dir.glob(MODEL_JSON_GLOB))
    if not matches:
        raise FileNotFoundError(f"模型目录里没有 .model3.json: {model_dir}")
    return matches[0]


def _as_float(value, default: float, lo: float, hi: float) -> float:
    """容错取浮点:非数字(``None`` / ``"abc"`` / ``NaN``)一律退回默认值。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number:          # NaN
        number = float(default)
    return max(lo, min(hi, number))


def _as_int(value, default: int, lo: int, hi: int) -> int:
    """容错取整数:先按浮点容错(兼容 ``"420"`` 这种手写成字符串的值),再夹取范围。"""
    return int(_as_float(value, float(default), float(lo), float(hi)))


def _as_bool(value, default: bool) -> bool:
    """容错取布尔:接受 ``true/false``、``1/0``、``"true"/"yes"/"on"`` 等写法。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
    return bool(default)


def _as_str(value, default: str) -> str:
    """容错取字符串:非字符串退回默认值(避免后面 ``.strip()`` / ``.startswith`` 炸)。"""
    return value if isinstance(value, str) else str(default)


def _as_optional_int(value) -> int | None:
    """``window_x`` / ``window_y``:可以是 ``None``(表示"首次启动自动摆"),也可以是整数。"""
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@dataclass
class Config:
    """可调参数。字段都有默认值,旧配置缺少新字段时自动补默认值。"""

    # 位置与外观
    window_x: int | None = None          # None = 首次启动自动放到屏幕右下角
    window_y: int | None = None
    window_height: int = 373             # 窗口高度(像素),宽度按模型画布比例推导
    #: 之前是 560,实测"有点大、挡屏幕",按用户要求缩到 2/3 → 560 * 2/3 ≈ 373
    #: 模型缩放。该模型画布里的内容偏右且贴边,1.0 会在右边缘被裁切,
    #: 0.85 实测四周留白正常(tools/check_render.py 可复核)
    scale: float = 0.85
    opacity: float = 1.0                 # 整体不透明度 0.2 ~ 1.0

    # 行为
    always_on_top: bool = True           # 置顶
    click_through: bool = True           # 透明区域鼠标穿透(桌宠不挡住桌面操作)
    gaze_follow: bool = True             # 视线跟随鼠标
    gaze_strength: float = 1.0           # 跟随幅度 0 ~ 1.5
    gaze_smoothing: float = 0.18         # 平滑系数,越小越"迟钝"
    idle_motion: bool = True             # 待机动画循环
    #: 点一下(单击模型)时让 **AI 理解并回应**:说一句 + 配个表情/动作。
    #: 旧版这里是"随机换表情/动画"的本地反射(auto_reaction),已按用户要求取消 ——
    #: 随机会让人一眼看出是机器,而让模型自己反应才有"它真的注意到我了"的感觉。
    poke_reaction: bool = True
    fps: int = 60                        # 渲染帧率上限

    # 模型
    model_dir: str = ""                  # 空 = 自动探测
    mute: bool = False                   # 预留:静音

    # 对话(阶段三)
    chat_enabled: bool = True
    chat_base_url: str = "https://api.deepseek.com"   # OpenAI 兼容接口
    chat_model: str = "deepseek-chat"
    chat_api_key: str = ""               # 直接填 key(明文存在本文件,注意别外传)
    #: ⚠️ Key **只由使用者自己在设置面板里填**。下面两项默认都关:
    #: 环境变量对普通用户是隐形来源,``~/.dsh/.credentials.yaml`` 更是本机开发工具的
    #: 凭据文件 —— 一个要免费分享出去的桌宠不该去读它们(用户要求"apikey 只应该自己填")。
    #: 真要自动化(脚本/CI)时再手动在 config.json 里打开。
    chat_api_key_env: str = ""
    chat_use_dsh_credentials: bool = False
    chat_persona: str = ""               # 空 = 用 pet/chat.py 的内置人设
    chat_history: int = 10               # 记住最近几轮对话
    chat_timeout: float = 30.0
    chat_bubble_seconds: int = 12        # 气泡自动消失秒数,0 = 不自动消失
    chat_hover: bool = True             # 鼠标靠近桌宠时自动弹出输入框
    chat_hover_distance: int = 60       # "靠近"的判定距离(像素)
    chat_input_gap: int = 4             # 输入框与桌宠底边的间距(像素),越小越贴近
    chat_setup_hint_shown: bool = False # 首次无 Key 时的提示是否已经弹过(避免反复打扰)
    chat_model_actions: bool = True     # 允许模型自己选表情/动作(工具调用或文字指令)
    chat_use_tools: bool = True         # 优先用工具调用;接口不支持时自动回退文字指令

    # 待机自主行为
    idle_autonomy: bool = True          # A 档:本地随机换表情/做小动作(免费、离线可用)
    idle_action_interval: int = 45      # A 档平均间隔(秒),实际会在 0.6~1.6 倍之间随机
    idle_llm_thoughts: bool = False     # B 档:让模型自己"想事情"(会消耗少量 API 费用)
    idle_thought_interval: int = 300    # B 档最短间隔(秒)
    idle_thought_bubble: bool = True    # B 档:把内心独白显示在气泡里

    # 记忆与养成(档案与记忆都存在本地文件,换模型也不丢)
    memory_enabled: bool = True         # 总开关:注入记忆 + 记录养成数据
    memory_extract_every: int = 5       # 每 N 轮对话自动抽取一次长期记忆(0 = 不自动抽)
    memory_max_items: int = 200         # 记忆库容量上限
    memory_inject_items: int = 6        # 每次请求注入多少条相关记忆
    memory_inject_chars: int = 800      # 注入记忆的字符预算

    # 软件化(阶段四)
    hotkey_toggle_visible: str = "ctrl+alt+W"   # 显示/隐藏桌宠
    hotkey_open_chat: str = "ctrl+alt+E"        # 打开聊天输入框
    autostart: bool = False                     # 开机自启(写 HKCU Run)

    # ---------------------------------------------------------------- 读写

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        cfg = cls()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return cfg
            known = {f.name for f in fields(cls)}
            for key, value in raw.items():
                if key in known:
                    setattr(cfg, key, value)
        cfg.clamp()
        return cfg

    def save(self, path: Path = CONFIG_PATH) -> None:
        self.clamp()
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clamp(self) -> None:
        """把字段收进合理区间,并把**类型也纠回来**。

        ⚠️ 以前这里直接 ``max(200, min(1600, self.window_height))``:只要用户手改
        ``config.json`` 时写了个 ``null`` 或带引号的字符串(``"373"``),
        比较就会抛 ``TypeError`` —— ``Config.load()`` 挂掉,而它发生在启动早期,
        表现是**桌宠静止、无热键、也没有任何日志**(实测极难排查)。
        现在非法值一律退回该字段的默认值,而不是让程序半死不活。
        """
        for name, lo, hi in (
            ("window_height", 200, 1600),
            ("fps", 10, 144),
            ("chat_history", 0, 50),
            ("chat_bubble_seconds", 0, 120),
            ("chat_hover_distance", 0, 400),
            ("chat_input_gap", 0, 80),
            ("idle_action_interval", 5, 1800),
            ("idle_thought_interval", 60, 7200),
            ("memory_extract_every", 0, 100),
            ("memory_max_items", 10, 5000),
            ("memory_inject_items", 0, 30),
            ("memory_inject_chars", 200, 4000),
        ):
            setattr(self, name, _as_int(getattr(self, name), getattr(Config, name), lo, hi))

        for name, lo, hi in (
            ("scale", 0.2, 3.0),
            ("opacity", 0.2, 1.0),
            ("gaze_strength", 0.0, 1.5),
            ("gaze_smoothing", 0.02, 1.0),
            ("chat_timeout", 5.0, 120.0),
        ):
            setattr(self, name, _as_float(getattr(self, name), getattr(Config, name), lo, hi))

        for name in (
            "always_on_top", "click_through", "gaze_follow", "idle_motion", "poke_reaction",
            "chat_enabled", "chat_use_dsh_credentials", "chat_model_actions", "chat_use_tools",
            "chat_hover", "chat_setup_hint_shown", "idle_autonomy", "idle_llm_thoughts",
            "idle_thought_bubble", "memory_enabled", "mute", "autostart",
        ):
            setattr(self, name, _as_bool(getattr(self, name), getattr(Config, name)))

        for name in (
            "model_dir", "hotkey_toggle_visible", "hotkey_open_chat", "chat_base_url",
            "chat_model", "chat_api_key", "chat_api_key_env", "chat_persona",
        ):
            setattr(self, name, _as_str(getattr(self, name), getattr(Config, name)))

        self.window_x = _as_optional_int(self.window_x)
        self.window_y = _as_optional_int(self.window_y)
