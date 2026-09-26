"""桌宠主窗口:透明、无边框、置顶,用 OpenGL 渲染 Live2D,并处理交互。

窗口层负责四件事:
1. 渲染循环(QTimer 定帧 + ``paintGL`` 调 live2d-py)
2. 交互:拖拽移动、点击反应、右键动作菜单
3. 透明区域点击穿透(靠逐像素 alpha 判定,见 ``_sample_cursor_alpha``)
4. 视线跟随(把鼠标位置平滑成参数,见 ``_update_gaze_target``)
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

import OpenGL.GL as gl
import live2d.v3 as live2d
from PySide6.QtCore import QPoint, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QMouseEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import win32
from .actions import Action
from .bubble import Bubble, ChatInput
from .chat import (
    ChatClient,
    ChatReply,
    ChatSettings,
    build_memory_extract_prompt,
    pick_expression,
    pick_thinking_expression,
)
from .config import CONFIG_PATH, Config
from .hotkey import GlobalHotkeys
from .memory import (
    KIND_EVENT,
    Memory,
    MemoryStore,
    HistoryStore,
    PROFILE_PATH,
    Profile,
    build_persona_block,
    parse_extracted_memories,
    parse_manual_memory,
)
from .model import PetModel

#: 程序入口脚本(只在开发模式下用于开机自启命令)
MAIN_SCRIPT = Path(__file__).resolve().parent.parent / "main.py"


def autostart_command() -> str:
    """生成开机自启命令。

    - 打包成 exe 后:直接指向 exe 自己
    - 开发模式:用 pythonw.exe 跑 main.py,避免开机弹黑框
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.is_file():
        executable = pythonw
    return win32.autostart_command(str(executable), str(MAIN_SCRIPT))

#: 判定"点击"的位移阈值(像素),超过这个距离就算拖拽
CLICK_SLOP = 5

#: alpha 大于该值视为"鼠标压在模型身上"
ALPHA_HIT_THRESHOLD = 10

#: 穿透开着、光标一直在窗口内却始终判为"未命中"多久之后强制恢复可点击(秒)。
#: 兜住"像素采样坏掉 → 窗口永久鼠标穿透 → 用户再也点不到它"这种死局。
CLICK_THROUGH_STUCK_SECONDS = 1.5

#: 鼠标离开后延迟多久隐藏聊天输入框(毫秒)—— 避免在边缘抖动时闪来闪去
CHAT_HIDE_DELAY_MS = 500

#: 用户刚交互过多久之内,不做"内心活动"(秒)—— 别在用户刚说完话时插嘴
IDLE_THOUGHT_QUIET_SECONDS = 60

#: 聊天/动作之后,待机随机行为让位多久(秒)——
#: 否则刚选好的表情会在 1 秒内被待机的随机表情覆盖掉,用户根本看不到回应
ACTION_HOLD_SECONDS = 8.0

#: 单击之后等多久才把"被戳"告诉模型(毫秒)。
#: 有这段等待,双击(开聊天框)就不会既开框又触发一次"被戳"。
POKE_DELAY_MS = 250

#: 点击位置 → "戳到哪儿了"(按窗口内相对高度,从上到下)。
#: ⚠️ 这个模型没有 HitAreas,只能按高度粗略分:头在上部约 1/3,尾巴/裙摆在下部。
POKE_REGIONS: tuple[tuple[float, str], ...] = (
    (0.30, "头顶"),
    (0.58, "脸"),
    (0.82, "身上"),
    (1.01, "尾巴和裙摆"),
)


def _distance_to_rect(point: QPoint, rect: QRect) -> float:
    """点到矩形的最短距离(点在矩形内为 0),用于"鼠标是否靠近桌宠"的判定。"""
    dx = max(rect.left() - point.x(), 0, point.x() - rect.right())
    dy = max(rect.top() - point.y(), 0, point.y() - rect.bottom())
    return (dx * dx + dy * dy) ** 0.5


class _ChatWorker(QThread):
    """在后台线程里问 LLM,避免卡住渲染线程。

    ``call`` 是一个零参可调用对象,返回 :class:`ChatReply`(聊天或待机"内心活动"都走它)。
    """

    replied = Signal(object)
    failed = Signal(str)

    def __init__(self, call, parent=None) -> None:
        super().__init__(parent)
        self._call = call

    def run(self) -> None:  # noqa: D102 - QThread 约定
        try:
            self.replied.emit(self._call())
        except Exception as exc:            # 网络/鉴权/格式错误都走这里
            self.failed.emit(str(exc))


class PetWindow(QOpenGLWidget):
    def __init__(
        self,
        cfg: Config,
        model_dir: Path,
        model_json: Path,
        actions: list[Action],
    ) -> None:
        super().__init__()
        from .applog import log

        log("PetWindow: 构造开始")
        self.cfg = cfg
        self.model_dir = model_dir
        self.pet = PetModel(model_dir, model_json, actions)

        # 交互状态
        self._dragging = False
        self._drag_offset = QPoint()
        self._press_pos = QPoint()
        self._moved = 0
        self._cursor_alpha = 0
        self._click_through_state = False
        self._idle_check = 0

        # 诊断用
        self._frames = 0
        self._fps = 0.0

        # 对话
        # 把桌宠窗口作为父窗口:气泡与输入框成为它的**附属窗口**,层级与桌宠一致
        # (不再各自单独置顶一层),并随桌宠一起显示/隐藏。
        self.bubble = Bubble(self)
        self.chat_input = ChatInput(self)
        self.chat_input.gap_below = self.cfg.chat_input_gap
        self.chat_input.submitted.connect(self.send_message)
        #: 回车提交走 on_submit(带返回值):没真正发出去时输入框保留用户打的字
        self.chat_input.on_submit = self._submit_from_input
        self._chat_worker: _ChatWorker | None = None
        self._thought_worker: _ChatWorker | None = None
        self._memory_worker: _ChatWorker | None = None
        self._chat_busy = False                       # 有请求在飞(聊天或待机思考)
        self._last_user_action = 0.0                  # 上次用户交互时刻(monotonic)
        self._action_hold_until = 0.0                 # 表情保持期:此之前待机不抢表情
        self._turns_since_extract = 0                 # 距上次抽取记忆过了几轮

        # 本地养成档案:人格与记忆都在本地,换模型不丢
        self.profile_path = PROFILE_PATH
        self.profile = Profile.load(self.profile_path)
        self.memories = MemoryStore(cap=cfg.memory_max_items)
        self.history = HistoryStore()
        self._chat_client = self._make_chat_client()
        #: 鼠标离开后延迟隐藏输入框
        self._chat_hide_delay_ms = CHAT_HIDE_DELAY_MS
        self._chat_hide_timer = QTimer(self)
        self._chat_hide_timer.setSingleShot(True)
        self._chat_hide_timer.timeout.connect(self.chat_input.hide)

        # 单击(戳一下):延迟一点再发请求,好让双击开聊天框时取消它
        self._poke_timer = QTimer(self)
        self._poke_timer.setSingleShot(True)
        self._poke_timer.timeout.connect(self._on_poke_timeout)
        self._pending_poke_y = 0.5
        self._ignore_next_release = False

        #: 应用内面板(设置窗口)打开集合:开着的时候桌宠让出置顶,见 _sync_dialog_mode
        self._open_dialogs: set[int] = set()
        #: 点击穿透看门狗状态:光标持续在窗口内却判为未命中的起点时刻(0 = 没在计时)
        self._stuck_since = 0.0
        #: 像素采样是否可信;判为不可信时保持"可点击",不再自动开启穿透
        self._click_through_suspect = False
        #: 上一帧采到的 alpha 是否是**有效读数**(而不是"光标在窗外"或"读失败")
        self._alpha_valid = True

        # 待机自主行为:A 档本地随机 / B 档让模型"想事情"
        self._idle_action_timer = QTimer(self)
        self._idle_action_timer.setSingleShot(True)
        self._idle_action_timer.timeout.connect(self._on_idle_action)
        self._idle_thought_timer = QTimer(self)
        self._idle_thought_timer.setSingleShot(True)
        self._idle_thought_timer.timeout.connect(self._on_idle_thought)

        self.setWindowTitle("DS鲸鱼娘 桌宠")
        #: 测试工具会把它设为 False,避免把测试用的配置写回用户的 config.json
        self.persist_config = True
        #: 配置写入路径。生产环境是项目目录下的 config.json;
        #: ``enable_test_mode()`` 会把它重定向到 .tmp 沙盒,这样"保存"逻辑在测试里照样被测到
        self.config_path = CONFIG_PATH
        self.setWindowFlags(
            Qt.FramelessWindowHint      # 无边框
            | Qt.WindowStaysOnTopHint   # 置顶
            | Qt.Tool                   # 不进任务栏
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setWindowOpacity(cfg.opacity)
        self.resize(cfg.window_height, cfg.window_height)  # 稍后按画布比例修正
        log("PetWindow: 构造完成")

    # ================================================================ 渲染

    def initializeGL(self) -> None:
        from .applog import log

        # 把当前 QOpenGLWidget 的上下文交给 live2d-py
        log("GL: glInit 开始")
        live2d.glInit()
        log("GL: glInit 完成,开始加载模型")
        self.pet.load()
        stats = self.pet.stats()
        log(f"GL: 模型加载完成(参数 {stats['参数数量']} 个、表情 {stats['表情数量']} 个、"
            f"动画 {stats['动画数量']} 个)")

        # ⚠️ 必须等模型加载完再重建对话客户端:表情/动作清单来自模型,
        # __init__ 里构造时模型还没加载,清单会是空的 → 模型选的名字会被全部丢弃。
        self._chat_client = self._make_chat_client()

        self.pet.set_scale(self.cfg.scale)   # 缩放必须在加载后、首次 Resize 前设定

        self._apply_geometry()
        log("GL: 几何完成,应用窗口特性")
        self._apply_window_traits()
        log("GL: 窗口特性完成,启动定时器")

        self.render_timer = QTimer(self)
        self.render_timer.timeout.connect(self.update)
        self.render_timer.start(max(1, int(1000 / self.cfg.fps)))

        self.input_timer = QTimer(self)
        self.input_timer.timeout.connect(self._tick_input)
        self.input_timer.start(50)

        self.fps_timer = QTimer(self)
        self.fps_timer.timeout.connect(self._tick_fps)
        self.fps_timer.start(1000)

        # 首次启动又没有可用的对话后端时,提示一次(之后不再打扰)
        QTimer.singleShot(3000, self.maybe_show_chat_setup_hint)
        # 待机自主行为(A 档本地随机 / B 档模型思考)
        self._schedule_idle_action()
        self._schedule_idle_thought()
        log("GL: 初始化全部完成")

    def resizeGL(self, w: int, h: int) -> None:
        if self.pet.model is not None:
            self.pet.model.Resize(int(w), int(h))

    def paintGL(self) -> None:
        # 待机动画播完后重启(回调来自原生线程,只置标志,这里消费)
        if self.pet.consume_idle_finished() and self.cfg.idle_motion:
            self.pet.restart_idle()

        # 视线必须在 Update 之前写进参数
        self.pet.apply_gaze(self.cfg.gaze_strength)

        live2d.clearBuffer()
        self.pet.update()
        self.pet.draw()

        self._sample_cursor_alpha()
        self._frames += 1

    def _sample_cursor_alpha(self) -> None:
        """读鼠标所在像素的 alpha,用来判断"点在不在模型身上"。

        必须在 GL 上下文里做,所以放在 ``paintGL`` 末尾 —— 每帧一次 1×1 的读取,
        比整屏回读便宜得多。

        ⚠️ 区分三种情况(以前只记一个 alpha,分不清"光标在窗外"和"读不到"):
        - 光标在窗口外 → ``alpha = 0``,``_alpha_valid = True``(确实是空的)
        - 读回来是有效数据 → 用它的 alpha
        - **读失败/数据不完整 → 当作"命中"(255)**。宁可挡住鼠标也不能让桌宠"点不动":
          一旦按 0 处理,窗口会被永久设成鼠标穿透,用户**再也点不到它**去恢复
          (实测事故:用户报"点击无反应、不能拖动、打不开设置",查出来就是
          ``WS_EX_TRANSPARENT`` 卡在开启态)。
        """
        self._alpha_valid = False
        local = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(local):
            self._cursor_alpha = 0
            self._alpha_valid = True          # 明确知道:光标不在窗口里
            return

        dpr = self.devicePixelRatioF()
        fb_w = max(1, int(self.width() * dpr))
        fb_h = max(1, int(self.height() * dpr))
        x = min(fb_w - 1, max(0, int(local.x() * dpr)))
        y = min(fb_h - 1, max(0, int(fb_h - 1 - local.y() * dpr)))
        try:
            data = gl.glReadPixels(x, y, 1, 1, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE)
        except Exception:
            self._cursor_alpha = 255          # 读不到就当命中,别让桌宠点不动
            return
        try:
            if data is None or len(data) < 4:
                self._cursor_alpha = 255      # 数据不完整同样按命中处理
                return
            self._cursor_alpha = int(data[3])
            self._alpha_valid = True
        except (TypeError, ValueError):
            self._cursor_alpha = 255


    # ================================================================ 定时任务

    def _tick_input(self) -> None:
        if not self.isVisible():
            return
        if self.cfg.gaze_follow:
            self._update_gaze_target()
        if self.cfg.click_through and not self._dragging:
            if self._click_through_suspect:
                #: 采样已被判为不可信(见看门狗):保持"可点击",直到重新采到确实命中
                if self._alpha_valid and self._cursor_alpha > ALPHA_HIT_THRESHOLD:
                    self._click_through_suspect = False
                    from .applog import log

                    log("点击穿透:像素采样恢复正常,回到常规判定")
            else:
                over_model = self._cursor_alpha > ALPHA_HIT_THRESHOLD
                self._set_click_through(not over_model)
                self._watch_click_through_stuck()

        # 鼠标靠近时自动出现聊天输入框
        self._update_chat_visibility(QCursor.pos())

        # 每 ~0.5s 兜底一次:手动动画播完后把待机接上
        self._idle_check += 1
        if self.cfg.idle_motion and self._idle_check >= 10:
            self._idle_check = 0
            self.pet.ensure_idle()

    def _watch_click_through_stuck(self) -> None:
        """看门狗:穿透开着、而光标**一直在窗口内**却始终判为"没命中" → 自动恢复可点击。

        ⚠️ 这是为了兜住"像素采样出问题"这类故障:一旦 alpha 恒为 0,窗口会被永久设成
        ``WS_EX_TRANSPARENT``,用户就**再也点不到它**来把设置改回来 ——
        实测就是这样:用户报"点击无反应、不能拖动、打不开设置",一查窗口扩展样式,
        ``WS_EX_TRANSPARENT`` 卡在开启态。
        容忍 1.5 秒(正常在模型边缘来回移动也可能短暂命中不了),
        超时后强制关掉穿透并记一条日志;下次判定正常就自动恢复原逻辑。
        """
        cursor = QCursor.pos()
        inside = self.rect().contains(self.mapFromGlobal(cursor))
        now = time.monotonic()
        if not (inside and self._click_through_state):
            self._stuck_since = 0.0
            return
        if self._stuck_since == 0.0:
            self._stuck_since = now
            return
        if now - self._stuck_since < CLICK_THROUGH_STUCK_SECONDS:
            return

        from .applog import log

        log(f"点击穿透:光标已在窗口内 {CLICK_THROUGH_STUCK_SECONDS:.1f}s 却仍判为未命中"
            f"(alpha={self._cursor_alpha}, 有效={self._alpha_valid}),"
            f"强制恢复可点击;像素采样修复前不再自动开启穿透")
        self._click_through_suspect = True
        self._set_click_through(False)
        self._stuck_since = 0.0

    def force_clickable(self) -> None:
        """手动把窗口从"穿透卡死"里救回来(托盘菜单/诊断用)。

        与看门狗同一个机制:标记采样不可信 + 立刻关掉穿透;等采样重新采到命中,
        会自动回到常规的"透明处穿透"行为。
        """
        from .applog import log

        log("点击穿透:手动请求恢复可点击")
        self._click_through_suspect = True
        self._set_click_through(False)
        self._stuck_since = 0.0

    def _update_chat_visibility(self, cursor: QPoint) -> None:
        """按鼠标位置决定聊天输入框是否显示。

        规则:
        - 鼠标进入"桌宠矩形 + chat_hover_distance"范围 → 出现(**不抢焦点**)
        - 鼠标离开该范围、且**没有正在使用**(无焦点、无文字)→ 延迟隐藏
        - 正在拖拽桌宠时不改变状态

        独立成方法是为了能用合成坐标测试,不必真的移动鼠标。
        """
        if not self.cfg.chat_enabled or not self.cfg.chat_hover:
            return
        if not self.chat_available():
            # 没有可用的对话后端时不要弹出没用的输入框
            if self.chat_input.isVisible() and not self.chat_input.is_engaged():
                self.chat_input.hide()
            return
        if self._dragging:
            return
        if self.dialogs_open():
            #: 设置面板开着时不弹输入框:它会盖在面板上,或抢面板的焦点
            if self.chat_input.isVisible() and not self.chat_input.is_engaged():
                self.chat_input.hide()
            return

        pet_rect = self.frameGeometry()
        near = _distance_to_rect(cursor, pet_rect) <= self.cfg.chat_hover_distance
        visible = self.chat_input.isVisible()
        over_input = visible and self.chat_input.frameGeometry().contains(cursor)

        if near:
            self._chat_hide_timer.stop()
            if not visible:
                self.chat_input.show_passive(pet_rect)
            return

        if not visible:
            return
        if over_input:
            self._chat_hide_timer.stop()
            return
        if self.chat_input.is_engaged():
            # 正在打字 → 不隐藏,但把输入框挪回桌宠下方(视觉上仍"固定"在那里)
            self._chat_hide_timer.stop()
            self.chat_input.follow(pet_rect)
            return
        if not self._chat_hide_timer.isActive():
            self._chat_hide_timer.start(self._chat_hide_delay_ms)

    def _update_gaze_target(self) -> None:
        """把鼠标相对窗口中心的位置换算成 [-1, 1] 的视线目标。"""
        pos = QCursor.pos()
        cx = self.x() + self.width() / 2
        cy = self.y() + self.height() / 2
        nx = max(-1.0, min(1.0, (pos.x() - cx) / max(1.0, self.width() * 0.6)))
        ny = max(-1.0, min(1.0, (cy - pos.y()) / max(1.0, self.height() * 0.6)))
        self.pet.smooth_gaze(nx, ny, self.cfg.gaze_smoothing)

    def _tick_fps(self) -> None:
        self._fps = float(self._frames)
        self._frames = 0

    # ================================================================ 窗口行为

    def _apply_geometry(self) -> None:
        """按模型画布比例定窗口尺寸,再放到保存的位置(首次启动贴屏幕右下角)。"""
        cw, ch = self.pet.canvas_size()
        aspect = (cw / ch) if ch else 1.0
        height = int(self.cfg.window_height)
        width = max(120, int(height * aspect))
        self.resize(width, height)

        if self.cfg.window_x is None or self.cfg.window_y is None:
            self._move_to_default()
        else:
            self.move(int(self.cfg.window_x), int(self.cfg.window_y))
            self._clamp_to_screen()

    def _clamp_to_screen(self) -> None:
        """把窗口夹紧在屏幕可用区域内。

        缩小尺寸后,原来保存的位置可能让一部分跑到屏幕外 —— 这里兜住。
        """
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = min(max(self.x(), geo.left()), max(geo.left(), geo.right() - self.width() + 1))
        y = min(max(self.y(), geo.top()), max(geo.top(), geo.bottom() - self.height() + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(int(x), int(y))

    def _apply_window_traits(self) -> None:
        hwnd = int(self.winId())
        win32.hide_from_alt_tab(hwnd)
        win32.set_topmost(hwnd, self.cfg.always_on_top)
        self._setup_hotkeys()

    # --- 全局快捷键 ------------------------------------------------------

    def _setup_hotkeys(self) -> None:
        """注册全局快捷键。

        ⚠️ **复用已有实例**而不是每次新建:``apply_config()``(每次在设置面板点保存)都会走到
        这里,以前每次都 ``GlobalHotkeys(...)`` 新建一个 —— 旧对象只是被覆盖,
        它的原生事件过滤器与 ``QObject`` 父子关系还挂着,等于**每保存一次设置泄漏一个**,
        长时间使用后残留的过滤器会重复分发同一条热键。
        """
        bindings = {
            "toggle_visible": self.cfg.hotkey_toggle_visible,
            "open_chat": self.cfg.hotkey_open_chat,
        }
        existing = getattr(self, "hotkeys", None)
        if existing is not None:
            existing.unregister_all()
            existing.rebind(bindings)
            existing.register_all()
            return
        self.hotkeys = GlobalHotkeys(int(self.winId()), bindings, self)
        self.hotkeys.triggered.connect(self._on_hotkey)
        self.hotkeys.register_all()

    def _on_hotkey(self, name: str) -> None:
        if name == "toggle_visible":
            self.toggle_visible()
        elif name == "open_chat":
            self.show()
            self.open_chat()

    def hotkey_report(self) -> str:
        """给诊断/托盘提示用的一句话。"""
        active = self.hotkeys.active if getattr(self, "hotkeys", None) else {}
        failed = self.hotkeys.failed if getattr(self, "hotkeys", None) else []
        if not active:
            return "无可用快捷键"
        text = "、".join(f"{spec}→{name}" for name, spec in active.items())
        if failed:
            text += f"(注册失败:{', '.join(failed)})"
        return text

    # --- 开机自启 --------------------------------------------------------

    def set_autostart(self, enabled: bool) -> None:
        command = autostart_command() if enabled else ""
        ok = win32.set_autostart(enabled, command)
        if ok:
            self.cfg.autostart = enabled
            self.bubble.show_text(
                "开机自启已开启" if enabled else "开机自启已关闭",
                self.frameGeometry(), 5,
            )
        else:
            self.cfg.autostart = False
            self.bubble.show_text("设置开机自启失败(可能被安全软件拦截)", self.frameGeometry(), 6)

    def _move_to_default(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        margin = 24
        self.move(geo.right() - self.width() - margin, geo.bottom() - self.height() - margin)

    def _set_click_through(self, enabled: bool) -> None:
        if enabled == self._click_through_state:
            return
        if win32.set_click_through(int(self.winId()), enabled):
            self._click_through_state = enabled
            #: 状态变化落日志:这类问题(卡在穿透里点不到)以前完全没法从日志看出来
            if not enabled:
                self._stuck_since = 0.0
            from .applog import log

            log(f"点击穿透:{'开启(鼠标落到桌面)' if enabled else '关闭(可交互)'}")

    # --- 供托盘 / 菜单调用 ------------------------------------------------

    def set_click_through_enabled(self, enabled: bool) -> None:
        self.cfg.click_through = enabled
        if not enabled:
            self._set_click_through(False)
        else:
            # 立即按当前像素重新判定,避免要等下一次 tick
            self._tick_input()

    def set_always_on_top(self, enabled: bool) -> None:
        self.cfg.always_on_top = enabled
        win32.set_topmost(int(self.winId()), enabled)

    def reset_position(self) -> None:
        self._move_to_default()

    def reload_model(self, mask_buffer_count: int | None = None) -> None:
        """重建模型(诊断用:改遮罩缓冲数等参数必须重建)。"""
        self.pet.load(mask_buffer_count)
        self.pet.set_scale(self.cfg.scale)
        if self.pet.model is not None:
            self.pet.model.Resize(self.width(), self.height())

    def make_settings_dialog(self):
        """创建并接线设置面板(**不弹出**)。

        生产路径(``open_settings``)与自动化测试都用这个方法,保证"面板 → 窗口"的
        接线只有一份;否则测试很容易绕过接线,测出一个假的通过。
        """
        from .settings_dialog import SettingsDialog   # 延迟导入,避免启动时多加载 Qt 组件

        dialog = SettingsDialog(self.cfg, self, owner=self)
        dialog.applied.connect(self.apply_config)
        #: 面板开着的时候桌宠要让位(见 _sync_dialog_mode)。
        #: 用 id 记集合 + 两个信号都做幂等移除:``finished`` 与 ``destroyed`` 都可能触发,
        #: 用计数器会重复减,导致"还有面板开着却提前恢复置顶"。
        key = id(dialog)
        self._open_dialogs.add(key)
        dialog.finished.connect(lambda _result, k=key: self._close_dialog(k))
        dialog.destroyed.connect(lambda *_args, k=key: self._close_dialog(k))
        self._sync_dialog_mode()
        return dialog

    # --- 面板打开时让位:否则聊天窗口会盖住设置面板 ------------------------

    def _close_dialog(self, key: int) -> None:
        if key not in self._open_dialogs:
            return                      # 幂等:finished 与 destroyed 会各来一次
        self._open_dialogs.discard(key)
        self._sync_dialog_mode()

    def _sync_dialog_mode(self) -> None:
        """按"当前是否有面板开着"调整桌宠置顶与输入框。

        ⚠️ 修的是用户反馈的「聊天框会在本应用设置窗口上遮挡」。
        实测(枚举 Win32 z 序号):桌宠、气泡、输入框、设置面板**同处置顶带**,
        带内谁最后被抬起谁在上面 —— 面板只在被点击激活时才回到最上,而
        ``bubble.show_text()`` / ``chat_input.show_passive()`` 里的 ``raise_()``
        会把气泡/输入框抬到面板上面(实测气泡序号 7 vs 面板 10)。
        把桌宠的 ``WS_EX_TOPMOST`` 摘掉后,它的附属窗口一起退出置顶带,
        于是无论怎么 ``raise_()`` 都盖不住置顶的面板;面板关掉再恢复。
        """
        if self.dialogs_open():
            if self.chat_input.isVisible():
                self.chat_input.hide()      # 让位期间不弹输入框
            win32.set_topmost(int(self.winId()), False)
        else:
            win32.set_topmost(int(self.winId()), self.cfg.always_on_top)

    def dialogs_open(self) -> bool:
        """当前是否有应用内面板开着(输入框与悬停逻辑据此让位)。"""
        return bool(self._open_dialogs)

    # --- 测试/工具模式 ----------------------------------------------------

    def enable_test_mode(self, name: str = "test") -> None:
        """测试与诊断工具的**统一入口**:不写回用户配置,养成数据改指沙盒。

        ⚠️ 为什么必须有个统一入口:以前每个工具各自写 ``persist_config = False``,
        但那只管住了 ``config.json`` —— ``profile`` / ``memories`` / ``history``
        仍然指向用户的真实文件,于是**跑一次测试就把假对话写进了用户的养成档案**
        (实测踩过:真实 history.jsonl 从 10 条涨到 42 条、亲密度凭空 +9,
        测试假服务器的罐头回复「你困了吗 / 我有点困了…」混进了真实记录)。
        另外设置面板的「保存」曾经绕过 ``persist_config`` 直接写 config.json,
        把用户的 chat_enabled 等字段覆盖成测试值(那处也已修)。
        """
        from .config import APP_DIR
        from .memory import HistoryStore, MemoryStore, Profile

        sandbox = APP_DIR / ".tmp" / "testmode" / name
        sandbox.mkdir(parents=True, exist_ok=True)
        self.persist_config = False
        #: 配置写入路径也重定向:让"保存"这条路径在测试里**照常走通**,
        #: 只是落在沙盒文件上 —— 比"测试模式干脆不写盘"覆盖更完整
        self.config_path = sandbox / "config.json"
        self.profile_path = sandbox / "profile.json"
        self.profile = Profile()
        self.memories = MemoryStore(sandbox / "memories.jsonl", cap=self.cfg.memory_max_items)
        self.history = HistoryStore(sandbox / "history.jsonl")

    def open_settings(self) -> None:
        """打开设置面板;保存后即时生效,不需要重启。"""
        self.make_settings_dialog().exec()

    def apply_config(self) -> None:
        """把 ``self.cfg`` 的变化即时应用(设置面板保存后调用)。"""
        cfg = self.cfg
        #: ⚠️ 先把当前位置写回配置,再应用几何。否则 ``_apply_geometry()`` 会拿**旧的**
        #: ``cfg.window_x/y``(它们只在退出时才同步)去摆窗口 —— 用户拖过桌宠之后
        #: 一打开设置点保存,桌宠就被瞬移回旧坐标/右下角(实测必现)。
        cfg.window_x = self.x()
        cfg.window_y = self.y()
        self.setWindowOpacity(cfg.opacity)
        self.chat_input.gap_below = cfg.chat_input_gap
        render_timer = getattr(self, "render_timer", None)
        if render_timer is not None:
            render_timer.setInterval(max(1, int(1000 / max(1, cfg.fps))))

        self._apply_geometry()          # 尺寸随 window_height / 画布比例变化
        self.pet.set_scale(cfg.scale)
        if self.pet.model is not None:
            self.pet.model.Resize(self.width(), self.height())

        self.set_always_on_top(cfg.always_on_top)
        self.set_click_through_enabled(cfg.click_through)
        self._chat_client = self._make_chat_client()

        if getattr(self, "hotkeys", None) is not None:
            self.hotkeys.unregister_all()
        self._setup_hotkeys()

        if win32.IS_WINDOWS:
            win32.set_autostart(cfg.autostart, autostart_command() if cfg.autostart else "")

        # 待机的两个定时器按新配置重排
        self._schedule_idle_action()
        self._schedule_idle_thought()

        if hasattr(self, "bubble"):
            self.bubble.show_text("设置已生效", self.frameGeometry(), 3)

    def toggle_visible(self) -> None:
        """显示/隐藏桌宠。输入框与气泡是它的附属窗口,要一起显隐(同层语义)。"""
        will_show = not self.isVisible()
        if not will_show:
            self._chat_hide_timer.stop()
            self.bubble.hide()
            self.chat_input.hide()
        self.setVisible(will_show)

    def react(self) -> str | None:
        """随机换一个表情(右键菜单「随机表情」用)。

        ⚠️ 这**不再**是点击的反应:用户要求取消"点一下就随机变脸"的机器反射,
        点击改成由模型理解并回应(见 ``_fire_poke``)。
        """
        name = self.pet.random_expression()
        if random.random() < 0.35:
            self.pet.random_motion()
        return name

    def poke_region(self, relative_y: float) -> str:
        """按点击的纵向位置粗略判断"戳到哪儿了"。

        ⚠️ 这个模型没有 HitAreas(点击区域定义),所以只能按窗口内的相对高度猜。
        实测模型画布:头在上部约 1/3,身体居中,尾巴/裙摆在下部。
        """
        for limit, name in POKE_REGIONS:
            if relative_y < limit:
                return name
        return POKE_REGIONS[-1][1]

    def _on_poke_timeout(self) -> None:
        """延迟到期:确认这不是双击(双击会停掉这个定时器),才真的去"戳"。"""
        self._fire_poke(self._pending_poke_y)

    def _fire_poke(self, relative_y: float) -> None:
        """把"用户戳了我一下"这件事告诉模型,让它用一句话 + 表情回应。

        对话后端不可用时**什么都不做**(纯粹当桌宠用,不弹提示、不打扰);
        正在聊天/思考时也跳过,避免打断正在进行的那句话。
        """
        from .applog import log

        if not self.cfg.poke_reaction or not self.isVisible():
            return
        region = self.poke_region(relative_y)
        if not self.chat_available():
            log(f"点击:被戳了(的{region}),但没有可用的对话后端,不回应")
            return
        if self._chat_worker is not None and self._chat_worker.isRunning():
            log(f"点击:被戳了(的{region}),但上一句还在想,跳过这次回应")
            return

        from .chat import POKE_PROMPT

        self._last_user_action = time.monotonic()
        self._chat_busy = True
        thinking = pick_thinking_expression(self.pet.expressions)
        if thinking:
            self.pet.set_expression(thinking)
        #: ⚠️ 思考气泡**必须有上限**:以前用 ``seconds=0``(永不消失),而失败回调是空的,
        #: 于是断网/5xx/超时时「…」就永久挂在桌宠头顶(实测 42 秒后仍在,配置里是 12 秒)。
        self.bubble.show_text("…", self.frameGeometry(), self.cfg.chat_bubble_seconds or 12)

        event = f"(用户戳了戳你的{region})"
        messages = self._chat_client.build_messages(
            event,
            self.history.recent(self.cfg.chat_history),
            extra_system=POKE_PROMPT + self._memory_block(event),
        )
        worker = _ChatWorker(lambda: self._chat_client.complete(messages), self)
        #: 戳一下也算一次互动(亲密度会涨),但不算"对话轮数" —— 记忆抽取不被它催
        worker.replied.connect(lambda reply: self._on_reply(event, reply, count_turn=False))
        worker.failed.connect(lambda msg, region=region: self._on_poke_error(str(msg), region))
        worker.finished.connect(self._on_chat_worker_finished)
        self._chat_worker = worker
        worker.start()
        log(f"点击:被戳了(的{region}),已让模型回应")

    def _on_poke_error(self, message: str, region: str) -> None:
        """被戳的请求失败:静默收掉思考气泡(不打扰用户),但要在日志里留痕。"""
        from .applog import log

        log(f"点击失败:被戳了(的{region}),模型没回应:{message}")
        if self.bubble.label.text() == "…":
            self.bubble.hide()

    # ================================================================ 鼠标交互

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = event.globalPosition().toPoint()
            self._moved = 0
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            pos = event.globalPosition().toPoint()
            self._moved = max(self._moved, (pos - self._press_pos).manhattanLength())
            self.move(pos - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            if self._ignore_next_release:
                #: 这一次 release 是双击的第一次(双击要开聊天框),别当成"戳一下"
                self._ignore_next_release = False
            elif self._moved <= CLICK_SLOP:
                #: 不立刻发请求:双击开聊天框时不要再插一次"被戳"
                self._pending_poke_y = event.position().y() / max(1, self.height())
                self._poke_timer.start(POKE_DELAY_MS)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = self.build_menu(self)
        menu.exec(event.globalPos())

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击模型 = 打开聊天输入框(此时取消待发的"被戳"回应)。"""
        if event.button() == Qt.LeftButton:
            self._poke_timer.stop()
            self._ignore_next_release = True
            self.open_chat()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def moveEvent(self, event) -> None:
        """桌宠移动时,气泡与聊天输入框都跟着走(输入框"固定"在其下方不远处)。"""
        super().moveEvent(event)
        rect = self.frameGeometry()
        self.bubble.stick_to(rect)
        self.chat_input.follow(rect)

    # ================================================================ 对话

    def _make_chat_client(self) -> ChatClient:
        cfg = self.cfg
        return ChatClient(ChatSettings(
            base_url=cfg.chat_base_url,
            model=cfg.chat_model,
            api_key=cfg.chat_api_key,
            api_key_env=cfg.chat_api_key_env,
            use_dsh_credentials=cfg.chat_use_dsh_credentials,
            persona=cfg.chat_persona,
            history_turns=cfg.chat_history,
            timeout=cfg.chat_timeout,
            # 让模型"知道"有哪些表情/动作可选(名字 + 含义),并用 enum 限定候选
            expressions=tuple(self.pet.expressions) if cfg.chat_model_actions else (),
            motions=tuple(self.pet.motion_names) if cfg.chat_model_actions else (),
            use_tools=cfg.chat_use_tools,
        ))

    def chat_available(self) -> bool:
        """对话是否真的可用(有 Key,或指向本机服务)。

        关掉对话、或没配 Key 且不是本机服务 → 不可用。**不可用不是错误**:
        桌宠要能安静地当纯桌宠用。
        """
        return self.cfg.chat_enabled and self._chat_client.available()

    def _chat_setup_hint(self) -> str:
        return ("我还不会说话呢～右键点我 →「设置…」→「对话」,"
                "填一个 API Key,或者把地址改成 127.0.0.1 用本地模型(Ollama)。\n"
                "不配置也没关系,我可以只当桌宠陪你。")

    def maybe_show_chat_setup_hint(self) -> None:
        """首次启动且没配 Key 时提示一次(之后不再打扰)。"""
        if self.chat_available() or self.cfg.chat_setup_hint_shown:
            return
        self.cfg.chat_setup_hint_shown = True
        if self.persist_config:
            try:
                self.cfg.save()
            except OSError:
                pass
        self.bubble.show_text(self._chat_setup_hint(), self.frameGeometry(), 15)

    def open_chat(self) -> None:
        """弹出输入框(双击模型或菜单"聊天…"触发)。"""
        if not self.cfg.chat_enabled:
            self.bubble.show_text("对话功能已关闭(设置 →「对话」里可以打开)", self.frameGeometry(), 6)
            return
        if not self.chat_available():
            # 没有可用的对话后端:给友好提示,而不是弹出一个发不出消息的框
            self.bubble.show_text(self._chat_setup_hint(), self.frameGeometry(), 15)
            return
        if self.dialogs_open():
            #: 面板开着时不弹输入框:``open_at`` 会 activateWindow 抢走面板焦点
            from .applog import log

            log("对话:设置面板开着,先不弹输入框")
            return
        self.chat_input.open_at(self.frameGeometry())

    def send_message(self, text: str) -> bool:
        """把用户的话发出去。返回**是否真的发出**(没发出时调用方要保留用户输入)。

        同步返回,结果通过信号回到 UI 线程。
        """
        text = (text or "").strip()
        if not text:
            return False

        if not self.chat_available():
            self.bubble.show_text(self._chat_setup_hint(), self.frameGeometry(), 12)
            return False

        if self._chat_worker is not None and self._chat_worker.isRunning():
            self.bubble.show_text("我还在想上一句呢…", self.frameGeometry(), 4)
            return False

        self._last_user_action = time.monotonic()
        self._chat_busy = True

        # 手动教它记忆:"记住:xxx"
        self._maybe_learn_manually(text)

        # 思考中的小表情(模型没有就用默认脸)
        thinking = pick_thinking_expression(self.pet.expressions)
        if thinking:
            self.pet.set_expression(thinking)

        #: 思考气泡给一个上限:万一请求挂了(断网/5xx/超时)又没人来替换它,
        #: 不会永远挂在桌宠头上
        self.bubble.show_text("…", self.frameGeometry(), self.cfg.chat_bubble_seconds or 12)

        messages = self._chat_client.build_messages(
            text, self.history.recent(self.cfg.chat_history), extra_system=self._memory_block(text),
        )
        worker = _ChatWorker(lambda: self._chat_client.complete(messages), self)
        worker.replied.connect(lambda reply, user=text: self._on_reply(user, reply))
        worker.failed.connect(self._on_chat_error)
        worker.finished.connect(self._on_chat_worker_finished)
        self._chat_worker = worker
        worker.start()
        return True

    def _submit_from_input(self, text: str) -> bool:
        """输入框回车的接入口:只有真的发出去才让输入框清空收起。"""
        return self.send_message(text)

    # ================================================================ 养成档案

    def _memory_block(self, query: str) -> str:
        """按当前话题挑相关记忆,拼成注入提示词的一段(与模型无关,永远同一份档案)。"""
        if not self.cfg.memory_enabled:
            return ""
        relevant = self.memories.search(query, self.cfg.memory_inject_items)
        return build_persona_block(self.profile, relevant, self.cfg.memory_inject_chars)

    def _maybe_learn_manually(self, text: str) -> str | None:
        """识别"记住:xxx" → 直接写进记忆库。"""
        if not self.cfg.memory_enabled:
            return None
        content = parse_manual_memory(text)
        if not content:
            return None
        added = self.memories.add(Memory(id=f"m{time.time_ns()}", text=content,
                                         kind="fact", importance=5, source="manual"))
        if added:
            self.memories.save()
            from .applog import log

            log(f"养成:记住了(手动)「{content}」")
        return content if added else None

    def _save_profile(self) -> None:
        if not self.cfg.memory_enabled:
            return
        try:
            # 显式传路径:测试可以指向沙盒,避免污染真实的养成档案
            self.profile.save(self.profile_path)
            self.history.save()
        except OSError as exc:
            from .applog import log

            log(f"养成:档案保存失败 {exc}")

    def _extract_memories(self) -> None:
        """后台让模型从最近对话里抽长期记忆(每 N 轮一次,失败静默)。"""
        conversation = "\n".join(
            f"{'用户' if m['role'] == 'user' else '你'}:{m['content']}"
            for m in self.history.recent(6)
        )
        if not conversation.strip():
            return
        prompt = build_memory_extract_prompt(conversation)
        worker = _ChatWorker(lambda: self._chat_client.utility_text(prompt), self)
        worker.replied.connect(self._on_memories_extracted)
        worker.failed.connect(lambda _msg: None)
        worker.finished.connect(self._on_chat_worker_finished)
        self._memory_worker = worker
        self._chat_busy = True
        worker.start()

    def _on_memories_extracted(self, raw: str) -> None:
        from .applog import log

        found = parse_extracted_memories(raw)
        added = [m for m in found if self.memories.add(m)]
        if added:
            self.memories.save()
            log(f"养成:自动记住 {len(added)} 条 —— " + ";".join(m.text for m in added))
        elif found:
            log(f"养成:抽取到 {len(found)} 条,但都与已有记忆重复,已跳过")

    def _on_chat_worker_finished(self) -> None:
        """线程收尾:先清引用再让 Qt 回收,否则 closeEvent 会碰到已析构对象。"""
        self._chat_busy = False
        worker = self.sender()
        if worker is self._chat_worker:
            self._chat_worker = None
        if worker is self._thought_worker:
            self._thought_worker = None
        if worker is self._memory_worker:
            self._memory_worker = None
        if worker is not None:
            worker.deleteLater()

    def _apply_reply_actions(self, reply) -> str | None:
        """把模型选的表情/动作真正应用到模型上;返回最终用的表情名。"""
        expression = getattr(reply, "expression", None)
        motion = getattr(reply, "motion", None)
        text = getattr(reply, "text", "") or ""

        if motion:
            self.pet.play_motion(motion)
        if expression:
            self.pet.set_expression(expression)
            self._action_hold_until = time.monotonic() + ACTION_HOLD_SECONDS
            return expression

        # 模型没给 → 用关键词兜底,避免"面瘫"
        guessed = pick_expression(text, self.pet.expressions)
        if guessed:
            self.pet.set_expression(guessed)
            self._action_hold_until = time.monotonic() + ACTION_HOLD_SECONDS
        return guessed

    def _say(self, text: str, seconds: int | None = None) -> None:
        """显示一句话的气泡,但**桌宠被隐藏时不显示**。

        ⚠️ ``toggle_visible()`` 的约定是"隐藏桌宠时气泡一起隐藏"(气泡是它的附属窗口)。
        可是在飞的回答(或待机独白)回来时会直接 ``bubble.show_text()`` ——
        于是桌宠已经隐藏了、气泡却单独蹦到屏幕中央,既与设计冲突,也让人以为是故障。
        """
        if not text or not self.isVisible():
            return
        self.bubble.show_text(text, self.frameGeometry(),
                              self.cfg.chat_bubble_seconds if seconds is None else seconds)

    def _on_reply(self, user_text: str, reply, count_turn: bool = True) -> None:
        """一次回应的落地处理:写历史、涨亲密度、应用表情动作、显示气泡。

        ``count_turn=False`` 用于"被戳一下"这类互动:亲密度照涨,但不计入对话轮数
        (免得把记忆抽取的节奏催快)。
        """
        # 兼容旧签名(纯文本)与新的 ChatReply
        if isinstance(reply, str):
            reply = ChatReply(text=reply)

        # 历史存本地文件(跨重启连续),空正文绝不入库
        self.history.append("user", user_text)
        if reply.text:
            self.history.append("assistant", reply.text)

        # 养成数据:相处天数 / 对话次数 / 亲密度;顺手看看有没有新里程碑
        if self.cfg.memory_enabled:
            unlocked = self.profile.touch()
            if unlocked:
                from .applog import log

                log(f"养成:解锁里程碑 {'、'.join(unlocked)}(亲密度 {self.profile.affinity},"
                    f"档位 {self.profile.level})")
            if count_turn:
                self._turns_since_extract += 1
            self._save_profile()

        self._apply_reply_actions(reply)
        if reply.text_missing:
            from .applog import log

            log("对话:模型只给了动作没写字,已用兜底文字显示")
        if reply.dropped:
            from .applog import log

            log(f"对话:忽略了模型给的无效指令 {list(reply.dropped)}")
        self._say(reply.text)

        # 攒够轮数就让模型抽一次长期记忆(后台,失败静默)
        every = self.cfg.memory_extract_every
        if (self.cfg.memory_enabled and every > 0 and self.chat_available()
                and self._turns_since_extract >= every):
            self._turns_since_extract = 0
            self._extract_memories()

    def _on_chat_error(self, message: str) -> None:
        # 聊天失败原来只显示在气泡里、不落盘,导致事后无法排查 —— 这里补上日志
        from .applog import log

        log(f"对话失败:{message}")
        self._say(f"(对话失败){message}", 10)

    # ================================================================ 待机自主行为

    def _can_act_on_own(self) -> bool:
        """现在能不能"自己动":可见、没在拖、没在对话、用户没在打字、且不在"表情保持期"。"""
        if not self.isVisible() or self._dragging or self._chat_busy:
            return False
        if time.monotonic() < self._action_hold_until:
            return False          # 刚回应过用户,让那个表情多留一会儿
        return not (self.chat_input.isVisible() and self.chat_input.is_engaged())

    def _schedule_idle_action(self) -> None:
        """A 档:随机间隔后自己换表情/做个小动作(本地,不花钱)。"""
        if not self.cfg.idle_autonomy:
            self._idle_action_timer.stop()
            return
        base_ms = max(5, self.cfg.idle_action_interval) * 1000
        # 0.6~1.6 倍随机,避免像机器人一样精确周期
        self._idle_action_timer.start(int(base_ms * random.uniform(0.6, 1.6)))

    def _on_idle_action(self) -> None:
        self._schedule_idle_action()      # 先排下一次,保证不会因异常停摆
        if not self._can_act_on_own():
            return
        if self.pet.motion_names and random.random() < 0.25:
            self.pet.random_motion()      # 偶尔来个小动作
        self.pet.random_expression()

    def _schedule_idle_thought(self) -> None:
        """B 档:每隔一段时间让模型"想一下"(会花少量 API 费用)。"""
        if not (self.cfg.idle_llm_thoughts and self.chat_available()):
            self._idle_thought_timer.stop()
            return
        self._idle_thought_timer.start(max(60, self.cfg.idle_thought_interval) * 1000)

    def _on_idle_thought(self) -> None:
        self._schedule_idle_thought()
        if not self.chat_available():
            return          # 没有可用后端:静默跳过,不报错、不打扰
        if not self._can_act_on_own():
            return
        # 刚聊过就先自己静静,别打扰用户
        if time.monotonic() - self._last_user_action < IDLE_THOUGHT_QUIET_SECONDS:
            return

        worker = _ChatWorker(self._chat_client.think, self)
        worker.replied.connect(self._on_thought)
        worker.failed.connect(lambda _msg: None)   # 待机失败静默处理,不弹错误
        worker.finished.connect(self._on_chat_worker_finished)
        self._thought_worker = worker
        self._chat_busy = True
        worker.start()

    def _on_thought(self, reply) -> None:
        if isinstance(reply, str):
            reply = ChatReply(text=reply)
        self._apply_reply_actions(reply)
        if reply.text and self.cfg.idle_thought_bubble:
            self._say(reply.text)

    def chat_ready(self) -> tuple[bool, str]:
        """给设置/诊断用:对话是否可用、后端来源。"""
        if not self.cfg.chat_enabled:
            return False, "对话已在配置里关闭"
        return self._chat_client.available(), self._chat_client.describe_key_source()

    # ================================================================ 菜单

    def build_menu(self, parent: QWidget | None = None) -> QMenu:
        """动作菜单:托盘和右键共用同一份。"""
        menu = QMenu(parent if parent is not None else self)

        menu.addAction("聊天…（双击我也可以）", self.open_chat)
        menu.addSeparator()
        menu.addAction("随机表情", self.react)

        exp_menu = menu.addMenu("表情")
        current = self.pet.current_expression
        for name in self.pet.expressions:
            act = exp_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == current)
            act.triggered.connect(lambda _checked=False, n=name: self.pet.toggle_expression(n))

        motion_menu = menu.addMenu("动画")
        for name in self.pet.motion_names:
            motion_menu.addAction(name, lambda n=name: self.pet.play_motion(n))

        menu.addAction("归位（清除表情）", self.pet.reset_expressions)
        menu.addSeparator()

        top = menu.addAction("窗口置顶")
        top.setCheckable(True)
        top.setChecked(self.cfg.always_on_top)
        top.toggled.connect(self.set_always_on_top)

        through = menu.addAction("透明区域点击穿透")
        through.setCheckable(True)
        through.setChecked(self.cfg.click_through)
        through.toggled.connect(self.set_click_through_enabled)

        auto = menu.addAction("开机自启")
        auto.setCheckable(True)
        auto.setChecked(win32.autostart_state() if win32.IS_WINDOWS else False)
        auto.toggled.connect(self.set_autostart)

        menu.addAction("重置位置", self.reset_position)
        menu.addAction("设置…", self.open_settings)
        menu.addSeparator()
        hint = menu.addAction(f"快捷键:{self.cfg.hotkey_toggle_visible} 显示/隐藏、"
                              f"{self.cfg.hotkey_open_chat} 聊天")
        hint.setEnabled(False)
        menu.addSeparator()
        app = QApplication.instance()
        if app is not None:
            menu.addAction("退出", app.quit)
        return menu

    # ================================================================ 收尾

    def closeEvent(self, event) -> None:
        self.cfg.window_x = self.x()
        self.cfg.window_y = self.y()
        self._save_profile()          # 养成档案与历史落盘
        if self.persist_config:
            try:
                self.cfg.save()
            except OSError:
                pass
        self.bubble.hide()
        self.chat_input.hide()
        self._idle_action_timer.stop()
        self._idle_thought_timer.stop()
        if getattr(self, "hotkeys", None) is not None:
            self.hotkeys.unregister_all()
        self._stop_workers()
        for timer in (getattr(self, "render_timer", None),
                      getattr(self, "input_timer", None),
                      getattr(self, "fps_timer", None)):
            if timer is not None:
                timer.stop()
        super().closeEvent(event)

    def _stop_workers(self) -> None:
        """退出前把**所有**后台 worker 收干净。

        ⚠️ 这里以前只等了 ``_chat_worker``,而且只等 2 秒:
        ``_thought_worker``(待机独白)与 ``_memory_worker``(后台记忆抽取)完全没等,
        而一次 LLM 请求要 3~30 秒 —— 运行中的 ``QThread`` 在解释器退出时被析构会触发
        Qt 的 ``qFatal("QThread: Destroyed while thread is still running")``,
        实测退出码 ``0xC0000409``(聊天中退出、第 5 轮记忆抽取中退出都能复现)。
        现在:先请求中断并给一小段时间优雅收尾,仍不退出就强制终止(进程马上要退,
        这里不再追求"体面",只求不崩)。
        """
        for attr in ("_chat_worker", "_thought_worker", "_memory_worker"):
            worker = getattr(self, attr, None)
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    worker.requestInterruption()
                    if not worker.wait(400):
                        from .applog import log

                        log(f"退出:{attr} 仍在运行,强制终止")
                        worker.terminate()
                        worker.wait(1500)
            except RuntimeError:
                pass                     # 对象可能已被 Qt 回收
            setattr(self, attr, None)

    def diagnostics(self) -> dict:
        info = self.pet.stats()
        info.update({
            "窗口尺寸": f"{self.width()}x{self.height()}",
            "DPI 缩放": self.devicePixelRatioF(),
            "实测 FPS": self._fps,
            "点击穿透(当前)": self._click_through_state,
            "鼠标处 alpha": self._cursor_alpha,
            "全局快捷键": self.hotkey_report(),
            "开机自启": win32.autostart_state() if win32.IS_WINDOWS else "非 Windows",
        })
        ready, source = self.chat_ready()
        info["对话"] = f"{'可用' if ready else '不可用'}(Key 来源:{source})"
        return info
