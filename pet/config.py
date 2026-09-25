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
    chat_api_key_env: str = "DEEPSEEK_API_KEY"
    chat_use_dsh_credentials: bool = True   # 允许复用 ~/.dsh/.credentials.yaml 里的 key
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
        """把数值收进合理区间,避免手改配置把界面搞坏。"""
        self.window_height = int(max(200, min(1600, self.window_height)))
        self.scale = float(max(0.2, min(3.0, self.scale)))
        self.opacity = float(max(0.2, min(1.0, self.opacity)))
        self.gaze_strength = float(max(0.0, min(1.5, self.gaze_strength)))
        self.gaze_smoothing = float(max(0.02, min(1.0, self.gaze_smoothing)))
        self.fps = int(max(10, min(144, self.fps)))
        self.chat_history = int(max(0, min(50, self.chat_history)))
        self.chat_timeout = float(max(5.0, min(120.0, self.chat_timeout)))
        self.chat_bubble_seconds = int(max(0, min(120, self.chat_bubble_seconds)))
        self.chat_hover_distance = int(max(0, min(400, self.chat_hover_distance)))
        self.chat_input_gap = int(max(0, min(80, self.chat_input_gap)))
        self.idle_action_interval = int(max(5, min(1800, self.idle_action_interval)))
        self.idle_thought_interval = int(max(60, min(7200, self.idle_thought_interval)))
        self.memory_extract_every = int(max(0, min(100, self.memory_extract_every)))
        self.memory_max_items = int(max(10, min(5000, self.memory_max_items)))
        self.memory_inject_items = int(max(0, min(30, self.memory_inject_items)))
        self.memory_inject_chars = int(max(200, min(4000, self.memory_inject_chars)))
