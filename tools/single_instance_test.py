"""单实例保护的验证:第二个实例必须被挡住。

背景:两只重叠的桌宠会表现为"眼睛永久重影/残影"(点表情只有前景那只变),
所以程序必须有单实例保护。这里验证:
  1. 第一个实例能拿到锁
  2. 第二个、第三个实例拿不到(会被拒绝)
  3. 释放后又能重新拿到
  4. 进程退出后锁会自动释放(不留僵尸锁)——用子进程验证

用法::

    .venv\\Scripts\\python.exe tools\\single_instance_test.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from pet.single_instance import SingleInstance

_results = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    app = QApplication(sys.argv[:1])
    # 锁文件放项目内(工作区可写);用系统临时目录在受限沙箱下会被拒绝访问
    lock = ROOT / ".tmp" / "single_instance_test.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.unlink(missing_ok=True)

    print("[1] 第一个实例")
    first = SingleInstance(lock)
    check("第一个实例拿到锁", first.is_primary, f"warning={first.warning}")
    check("锁文件里写了 PID",
          lock.with_name(lock.name + ".pid").read_text(encoding="utf-8").strip() == str(os.getpid()),
          "(缺 PID 文件)")

    print("\n[2] 第二、三个实例(应当被拒绝)")
    second = SingleInstance(lock)
    check("第二个实例被拒绝(避免两只重叠)", not second.is_primary, f"warning={second.warning}")
    check("能报出占用者的 PID", second.other_pid is not None, str(second.other_pid))
    third = SingleInstance(lock)
    check("第三个实例同样被拒绝(模拟连点两次)", not third.is_primary)

    print("\n[3] 释放后可重新获取")
    first.release()
    again = SingleInstance(lock)
    check("释放后新实例拿到锁", again.is_primary)
    again.release()

    print("\n[4] 子进程退出后锁自动释放(不留僵尸锁)")
    script = (
        "import sys; sys.path.insert(0, r'%s');"
        "from pet.single_instance import SingleInstance;"
        "from pathlib import Path;"
        "g = SingleInstance(Path(r'%s'));"
        "print('PRIMARY' if g.is_primary else 'BLOCKED')" % (ROOT, lock)
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    check("子进程能拿到锁", "PRIMARY" in out.stdout, out.stdout.strip() + out.stderr.strip()[:200])
    after = SingleInstance(lock)
    check("子进程结束后锁可再次获取(无僵尸锁)", after.is_primary, f"warning={after.warning}")
    after.release()

    lock.unlink(missing_ok=True)
    print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    app.quit()
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
