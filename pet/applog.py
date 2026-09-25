"""极简日志:统一写进 ``APP_DIR/pet.log``。

打包成 exe 后是窗口程序、没有控制台,出问题时只能靠日志文件排查。
各模块都用这里的 ``log()``,保证排查时有完整的阶段轨迹。
"""

from __future__ import annotations

from datetime import datetime

from .config import APP_DIR

LOG_PATH = APP_DIR / "pet.log"


def log(message: str, echo: bool = True) -> None:
    """写一行日志(带时间戳),同时尽量打印到控制台。"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except OSError:
        pass

    if echo:
        try:
            print(message, flush=True)
        except Exception:
            pass   # 无控制台时 print 可能失败,忽略
