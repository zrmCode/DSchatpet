"""「模型文件缺失」时的启动行为验证(开源版最常见的第一次运行)。

仓库里**不含** Live2D 模型(版权归原作者,只随 Release 压缩包提供),所以别人
clone 下来直接双击,必须被清楚地告知"模型该放到哪里",而不是静默闪退。

被验证的三条:
  1. 找不到模型 → 退出码 2,不抛异常;
  2. 没有控制台(pythonw / 打包版)→ 弹原生消息框,内容含目标路径与获取途径;
  3. 有控制台 → 只打印到 stderr,不弹框(不打断命令行用户)。

不会真的弹窗阻塞:消息框被打桩记录。

用法::

    .venv\\Scripts\\python.exe tools\\model_missing_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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

    import main as main_mod
    from pet import config as config_mod
    from pet import win32

    recorded: list[tuple[str, str, int]] = []
    opened: list[str] = []

    def fake_box(text: str, title: str, flags: int = 0) -> int:
        recorded.append((text, title, flags))
        return win32.IDOK

    win32.message_box = fake_box                      # type: ignore[assignment]
    win32.open_in_explorer = lambda p: (opened.append(p), True)[1]   # type: ignore[assignment]
    config_mod.find_model_dir = lambda explicit=None: None           # type: ignore[assignment]
    main_mod.config_mod.find_model_dir = config_mod.find_model_dir   # 同一模块对象

    target = str(config_mod.APP_DIR / "assets" / "model")

    print("\n[1] 没有控制台(双击 / 打包版)→ 必须弹框说清楚")
    saved_stdout = sys.stdout
    sys.stdout = None
    try:
        code = main_mod.main(["--no-tray"])
    finally:
        sys.stdout = saved_stdout
    check("退出码是 2(配置/环境错误)", code == 2, str(code))
    check("弹了一次消息框", len(recorded) == 1, str(len(recorded)))
    text, title, flags = recorded[0] if recorded else ("", "", 0)
    check("标题点名了问题", "缺少模型" in title, title)
    check("给出了模型该放的路径", target in text, text[:120])
    check("说了模型不随仓库分发 / 版权归属", "不包含模型" in text and "氵六青" in text, text[:160])
    check("说了怎么获取(Release 压缩包)", "Release" in text, text[:160])
    check("按「确定」会打开该目录", opened == [target], str(opened))
    check("用的是「确认/取消」按钮而非纯提示", flags & win32.MB_OKCANCEL, hex(flags))

    print("\n[2] 有控制台 → 只打印,不弹框(不打断命令行用户)")
    recorded.clear()
    opened.clear()
    import io

    buffer = io.StringIO()
    saved_stderr, sys.stderr = sys.stderr, buffer
    try:
        code = main_mod.main(["--no-tray"])
    finally:
        sys.stderr = saved_stderr
    check("退出码仍是 2", code == 2, str(code))
    check("没有弹消息框", not recorded, str(len(recorded)))
    check("stderr 里说明了原因和目标路径",
          "找不到模型目录" in buffer.getvalue() and target in buffer.getvalue(),
          buffer.getvalue()[:160])

    print("\n[3] 正常有模型时不走这条路")
    import importlib

    importlib.reload(config_mod)
    found = config_mod.find_model_dir()
    if found is None:
        print("  (本机当前没有模型文件,这一节跳过)")
    else:
        check("本机能定位到模型目录", Path(found).is_dir(), str(found))
        check("目录里有 .model3.json", config_mod.find_model_json(Path(found)).is_file())

    print("\n[4] 弹窗/打开目录的底层实现是纯 ctypes(不引入新依赖)")
    source = (ROOT / "pet" / "win32.py").read_text(encoding="utf-8")
    check("用 MessageBoxW", "MessageBoxW" in source)
    check("用 ShellExecuteW 打开目录", "ShellExecuteW" in source)
    check("没有 import tkinter 之类的额外依赖",
          "tkinter" not in source and "import wx" not in source)

    print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
