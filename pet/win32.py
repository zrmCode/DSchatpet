"""Windows 窗口技巧:透明区域点击穿透、强制置顶、开机自启。

桌宠的透明区域不应该挡住桌面操作,所以需要按需切换窗口的 ``WS_EX_TRANSPARENT``。
Qt 自带 ``Qt.WindowTransparentForInput``,但它会同时吃掉窗口的全部输入且切换时闪烁明显,
这里直接用 user32 改扩展样式,更可控。

全部通过 ctypes / winreg 调用,不引入额外依赖;非 Windows 平台静默降级为 no-op。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import windll, wintypes

IS_WINDOWS = sys.platform == "win32"

#: 开机自启写在这个注册表位置(HKCU,不需要管理员权限)
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "DSWhalePet"

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020   # 鼠标穿透
WS_EX_TOOLWINDOW = 0x00000080    # 不出现在 Alt+Tab / 任务栏
WS_EX_NOACTIVATE = 0x08000000    # 点击不抢焦点(可选)

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

_argtypes_ready = False


def _user32():
    """取 user32,并且**先声明参数类型**。

    ⚠️ 这一条是硬要求,不是可选的洁癖:``HWND_TOPMOST(-1)`` / ``HWND_NOTOPMOST(-2)``
    是**负值伪句柄**。不声明 argtypes 时 ctypes 把 Python int 按 32 位 int 传,
    64 位下不做符号扩展 → 被调方收到 ``0x00000000FFFFFFFE`` 而不是 ``-2``,
    ``SetWindowPos`` 直接失败返回 0 —— 而返回值没人检查,于是**静默什么都不做**。
    实测后果:``set_topmost()`` 从来没生效过(右键菜单「置顶」是摆设),
    依赖它的"设置面板打开时让出置顶"也一并失效。
    """
    global _argtypes_ready
    user32 = windll.user32
    if not _argtypes_ready and IS_WINDOWS:
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        _argtypes_ready = True
    return user32


def _ex_style(hwnd: int) -> int:
    return windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)


def _set_ex_style(hwnd: int, style: int) -> None:
    windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    _user32().SetWindowPos(
        wintypes.HWND(int(hwnd)), wintypes.HWND(0), 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )


def set_click_through(hwnd: int, enabled: bool) -> bool:
    """开关鼠标穿透,返回是否真的改动了样式(用于避免每帧重复调 SetWindowPos)。"""
    if not IS_WINDOWS or not hwnd:
        return False
    style = _ex_style(hwnd)
    new_style = (style | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
    if new_style == style:
        return False
    _set_ex_style(hwnd, new_style)
    return True


def hide_from_alt_tab(hwnd: int) -> None:
    """加 WS_EX_TOOLWINDOW,让桌宠不出现在 Alt+Tab 列表里。"""
    if not IS_WINDOWS or not hwnd:
        return
    _set_ex_style(hwnd, _ex_style(hwnd) | WS_EX_TOOLWINDOW)


def set_topmost(hwnd: int, enabled: bool) -> None:
    """强制置顶 / 取消置顶。

    ⚠️ **必须显式声明 argtypes 并把伪句柄包成 HWND**。``HWND_TOPMOST(-1)`` /
    ``HWND_NOTOPMOST(-2)`` 是**负值伪句柄**:不声明 argtypes 时 ctypes 按 32 位 int 传参,
    64 位下不做符号扩展 → 实际收到 ``0x00000000FFFFFFFE`` 这种无效值,
    ``SetWindowPos`` 直接失败并返回 0(而返回值没检查,所以**静默失效**)。
    实测踩过:这个函数以前一直没生效,右键菜单里的「置顶」开关是个摆设
    (窗口的置顶只来自 Qt 的 ``WindowStaysOnTopHint``);
    而"设置面板打开时让出置顶"也依赖它,于是聊天窗口照样盖住面板。
    """
    if not IS_WINDOWS or not hwnd:
        return
    _user32().SetWindowPos(
        wintypes.HWND(int(hwnd)),
        wintypes.HWND(HWND_TOPMOST if enabled else HWND_NOTOPMOST),
        0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    )


# ---------------------------------------------------------------- 开机自启


def autostart_command(python_exe: str, script_path: str) -> str:
    """生成写进注册表的启动命令(路径都带引号,防空格/中文路径出问题)。"""
    return f'"{python_exe}" "{script_path}"'


def set_autostart(enabled: bool, command: str = "", name: str = AUTOSTART_VALUE_NAME) -> bool:
    """开关开机自启。写在 HKCU,不需要管理员权限;失败返回 False。"""
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            if enabled:
                if not command:
                    return False
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def autostart_state(name: str = AUTOSTART_VALUE_NAME) -> bool:
    """当前是否已设置开机自启。"""
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return bool(value)
    except (FileNotFoundError, OSError):
        return False


#: MessageBoxW 的按钮/图标常量
MB_OK = 0x00000000
MB_OKCANCEL = 0x00000001
MB_ICONWARNING = 0x00000030
MB_ICONINFORMATION = 0x00000040
MB_TOPMOST = 0x00040000
MB_SETFOREGROUND = 0x00010000
IDOK = 1
IDCANCEL = 2


def message_box(text: str, title: str, flags: int = MB_OK | MB_ICONWARNING) -> int:
    """弹一个原生消息框,返回按钮 ID。

    ⚠️ 为什么不用 Qt 的 QMessageBox:本函数要在**还没创建 QApplication** 的启动早期
    也能用(比如"模型文件缺失"这种必须在建窗口之前就告诉用户的错误)。
    Windows 之外返回 ``IDOK``,不阻塞。
    """
    if not IS_WINDOWS:
        return IDOK
    try:
        return int(windll.user32.MessageBoxW(None, text, title, flags | MB_TOPMOST))
    except OSError:
        return IDOK


def open_in_explorer(path: str) -> bool:
    """用资源管理器打开一个目录(不存在则先创建)。用于"模型该放哪儿"的引导。"""
    from pathlib import Path

    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    if not IS_WINDOWS:
        return False
    try:
        result = windll.shell32.ShellExecuteW(None, "open", str(target), None, None, 1)
        return int(result) > 32
    except OSError:
        return False

