"""全局快捷键:用 Win32 ``RegisterHotKey`` 实现,零第三方依赖。

为什么不用 ``keyboard`` / ``pynput``:桌宠要打包分发,依赖越少越省心,而且
``RegisterHotKey`` 是系统级注册 —— 按下组合键时**系统会把按键吃掉**,不会漏到
当前前台程序里,这正是"召唤桌宠"想要的行为。

消息回流靠 Qt 的 ``QAbstractNativeEventFilter`` 抓 ``WM_HOTKEY``;
注册绑定的窗口句柄必须是桌宠自己的窗口,线程消息(hwnd=0)到不了 Qt 的过滤器。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

IS_WINDOWS = sys.platform == "win32"

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODIFIER_ALIASES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "super": MOD_WIN, "cmd": MOD_WIN,
}


class _MSG(ctypes.Structure):
    """Win32 MSG 结构(64 位下 ctypes 会按声明自动对齐)。"""

    _fields_ = [
        ("hWnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """把 ``"ctrl+alt+W"`` 解析成 (modifiers, virtual-key)。解析不了返回 None。"""
    if not spec:
        return None
    parts = [p.strip().lower() for p in spec.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return None

    modifiers = 0
    key_part = None
    for part in parts:
        if part in _MODIFIER_ALIASES:
            modifiers |= _MODIFIER_ALIASES[part]
        else:
            key_part = part
    if key_part is None or modifiers == 0:
        return None      # 不注册无修饰键的裸键,避免抢占正常打字

    if len(key_part) == 1 and key_part.isalnum():
        vk = ord(key_part.upper())
    elif key_part.startswith("f") and key_part[1:].isdigit() and 1 <= int(key_part[1:]) <= 24:
        vk = 0x70 + int(key_part[1:]) - 1
    else:
        named = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "esc": 0x1B, "escape": 0x1B,
                 "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E}
        vk = named.get(key_part)
        if vk is None:
            return None
    return modifiers | MOD_NOREPEAT, vk


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: "GlobalHotkeys") -> None:
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, event_type, message):
        # 原生事件过滤器里抛异常会打断整个消息循环,所以整体兜住
        try:
            if not IS_WINDOWS:
                return False, 0
            # event_type 可能是 str / bytes / QByteArray,统一成 str 再判断
            if isinstance(event_type, str):
                raw = event_type
            else:
                try:
                    raw = bytes(event_type).decode("utf-8", "replace")
                except (TypeError, ValueError):
                    raw = str(event_type)
            if "windows_generic_MSG" not in raw:
                return False, 0

            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
            if msg.message == WM_HOTKEY:
                self._owner._seen.append(f"{raw}|hwnd={int(msg.hWnd or 0)}|id={int(msg.wParam)}")
                self._owner._dispatch(int(msg.wParam), int(msg.time))
        except Exception:
            pass
        return False, 0


class GlobalHotkeys(QObject):
    """绑定表形如 ``{"toggle_visible": "ctrl+alt+W", "open_chat": "ctrl+alt+E"}``。

    ⚠️ 实测:同一条 ``WM_HOTKEY`` 会被 Qt 通过原生事件过滤器**投递两次**
       (窗口句柄与热键 id 完全相同)。若不去重,"显示/隐藏"会被连续触发两次而互相抵消,
       表现为"按了快捷键没反应"。

    ⚠️ 判重必须用**消息时间戳**(``MSG.time``),不能只用"距上次多少毫秒":
    重复投递的两条消息时间戳**完全相同**,而人手真实连按两下间隔约 120ms ——
    用时间窗去重会把真实连按一起吃掉(实测:250ms 窗口下快速按两下只生效一次,
    用户表现就是"按了没反应")。只有在拿不到时间戳(``time == 0``)时才退回
    一个很短的时间窗兜底。
    """

    triggered = Signal(str)

    _BASE_ID = 0xA520
    #: 兜底时间窗:仅在消息时间戳不可用时使用,取得足够小以免吃掉真实连按
    _FALLBACK_DEDUPE_SECONDS = 0.05

    def __init__(self, hwnd: int, bindings: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self._hwnd = int(hwnd)
        self._bindings = {k: v for k, v in bindings.items() if v}
        self._ids: dict[int, str] = {}
        self._filter: _HotkeyFilter | None = None
        self._failed: list[str] = []
        #: 上一次分发的 (热键 id, 消息时间戳);时间戳相同 = 同一次按键的重复投递
        self._last_message: tuple[int, int] | None = None
        self._last_dispatch: dict[int, float] = {}
        #: 诊断用:被去重掉的重复投递次数 / 过滤器看到的原始消息(截断保留)
        self.duplicates_ignored = 0
        self._seen: list[str] = []

    @property
    def failed(self) -> list[str]:
        """注册失败的绑定名(被别的程序占用、或写法无法解析)。"""
        return list(self._failed)

    @property
    def active(self) -> dict[str, str]:
        """真正注册成功的绑定。"""
        return {name: spec for name, spec in self._bindings.items() if name not in self._failed}

    def register_all(self) -> list[str]:
        """注册全部绑定,返回失败的绑定名。"""
        if not IS_WINDOWS or not self._hwnd:
            self._failed = list(self._bindings)
            return self.failed

        user32 = ctypes.windll.user32
        self._failed = []
        for index, (name, spec) in enumerate(self._bindings.items()):
            parsed = parse_hotkey(spec)
            hotkey_id = self._BASE_ID + index
            if parsed is None:
                self._failed.append(name)
                continue
            modifiers, vk = parsed
            if user32.RegisterHotKey(self._hwnd, hotkey_id, modifiers, vk):
                self._ids[hotkey_id] = name
            else:
                self._failed.append(name)

        if self._ids and self._filter is None:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                self._filter = _HotkeyFilter(self)
                app.installNativeEventFilter(self._filter)
        return self.failed

    def id_for(self, name: str) -> int | None:
        """查某个绑定对应的系统热键 id(测试用来模拟按键)。"""
        for hotkey_id, bound in self._ids.items():
            if bound == name:
                return hotkey_id
        return None

    def rebind(self, bindings: dict[str, str]) -> None:
        """换一组绑定(复用同一个实例,避免泄漏原生事件过滤器)。

        调用顺序应为 ``unregister_all()`` → ``rebind()`` → ``register_all()``。
        """
        self._bindings = {k: v for k, v in bindings.items() if v}
        self._ids.clear()
        self._last_message = None
        self._last_dispatch.clear()

    def unregister_all(self) -> None:
        if IS_WINDOWS and self._hwnd:
            user32 = ctypes.windll.user32
            for hotkey_id in list(self._ids):
                try:
                    user32.UnregisterHotKey(self._hwnd, hotkey_id)
                except Exception:
                    pass
        self._ids.clear()

        if self._filter is not None:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._filter)
            self._filter = None

    def _dispatch(self, hotkey_id: int, message_time: int = 0) -> None:
        name = self._ids.get(hotkey_id)
        if not name:
            return

        #: ① 首选判据:消息时间戳相同 = 同一条 WM_HOTKEY 的重复投递
        if message_time:
            if self._last_message == (hotkey_id, message_time):
                self.duplicates_ignored += 1
                return
            self._last_message = (hotkey_id, message_time)
        else:
            #: ② 兜底:拿不到时间戳时只压掉极短时间内的重复(不能吃掉人手连按)
            now = time.monotonic()
            if now - self._last_dispatch.get(hotkey_id, 0.0) < self._FALLBACK_DEDUPE_SECONDS:
                self.duplicates_ignored += 1
                return

        now = time.monotonic()
        self._last_dispatch[hotkey_id] = now
        self.triggered.emit(name)
