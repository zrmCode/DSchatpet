"""单实例保护:避免同一个桌宠被启动两次。

为什么需要:桌宠是"透明置顶 + 位置持久化"的窗口,启动两次会**完全重叠**。
此时点表情只有前景那只变了,后面那只仍停在旧表情上 —— 看上去就是"眼睛出现永久残影/重影",
而且怎么切都不消失。这类问题必须在程序层面挡住。

实现选择(踩过坑):
- **不用命名管道(QLocalServer)**:某些受限环境会禁止创建命名管道
  (实测 ``QLocalServer.listen()`` 直接报"拒绝访问"),而且"建锁失败"必须与
  "已有实例"区分开,否则会出现"打不开程序"的假故障。
- **改用字节范围文件锁**(``msvcrt.locking``):进程退出时由系统自动释放,
  不会留下僵尸锁;锁文件里写 PID,只用于给用户提示。
- **拿不到锁就放行**(fail-open):宁可少一层保护,也不能因为环境限制而拒绝启动。
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_LOCK_NAME = "pet.lock"

#: 锁加在文件的偏移 1024 处(位置本身不重要,因为占用者 PID 写在独立的 .pid 文件里)。
#: 之所以不用文件开头:Windows 的字节范围锁会让**别的进程连打开该文件都失败**,
#: 早先把 PID 写在锁文件里时就读不出来(踩过这个坑),所以信息与锁分开存。
LOCK_OFFSET = 1024


class SingleInstance:
    """持有锁时 ``is_primary`` 为 True;``other_pid`` 是占用者的 PID(若可读)。"""

    def __init__(self, lock_path: Path) -> None:
        self.path = Path(lock_path)
        #: PID 写在**另一个不加锁的文件**里:被字节锁住的文件,别的进程连打开都会失败
        #: (实测),所以占用者信息必须分开放。
        self.pid_path = self.path.with_name(self.path.name + ".pid")
        self.other_pid: int | None = None
        self.warning: str | None = None
        self.blocked_reason: str | None = None
        self._handle = None

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+", encoding="utf-8")
        except OSError as exc:
            # 连锁文件都建不了(极端受限环境)→ 放行,只记一条警告
            self.warning = f"无法创建锁文件({exc}),跳过单实例检查"
            return

        try:
            self._lock(handle)
        except OSError as exc:
            # 非阻塞加锁失败 = 锁已被别的实例占用(实测:空闲时加锁能成功,
            # 所以这个信号是可靠的,不能当成"环境限制"放行)
            self.other_pid = self._read_pid(self.pid_path)
            try:
                handle.close()
            except OSError:
                pass
            self.blocked_reason = f"锁被占用({exc})"
            return

        try:
            self.pid_path.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass
        self._handle = handle

    @staticmethod
    def _read_pid(pid_path: Path) -> int | None:
        """读占用者 PID —— 只用于给用户提示,读不到不影响判定。"""
        try:
            raw = pid_path.read_text(encoding="utf-8").strip()
            return int(raw) if raw.isdigit() else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _lock(handle) -> bool:
        """对**偏移 1024** 处的 1 字节加非阻塞排他锁。"""
        if os.name == "nt":
            import msvcrt

            handle.seek(LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True

    @property
    def is_primary(self) -> bool:
        """拿不到锁时按"放行"处理:宁可少一层保护,也不能因为环境限制而拒绝启动。"""
        return self._handle is not None or self.warning is not None

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(LOCK_OFFSET)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
            self._handle.close()
        except OSError:
            pass
        self._handle = None

        # 只有确认是我们自己写的才删,避免误删新实例的 PID 文件
        if self._read_pid(self.pid_path) == os.getpid():
            try:
                self.pid_path.unlink(missing_ok=True)
            except OSError:
                pass
