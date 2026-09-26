"""设置面板的验证:改值 -> 保存 -> 检查配置落盘 + 窗口/快捷键即时生效。

⚠️ **不再"备份真实 config.json → 跑测试 → 还原"**:这个过程一旦中断,就把用户配置留在
测试值上了(真实事故:一个临时脚本调了面板的保存,把 chat_enabled 覆盖成 false,
之后对话功能全都"莫名不可用")。现在 ``enable_test_mode()`` 会把写入路径
(``window.config_path``)重定向到 ``.tmp`` 沙盒,真实文件全程不碰,
测试结束时还会核对它的哈希没有变化。

用法::

    .venv\\Scripts\\python.exe tools\\settings_test.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live2d.v3 as live2d
from PySide6.QtCore import QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from pet import config as config_mod
from pet.actions import load_actions
from pet.window import PetWindow

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

    #: ⚠️ 不再"备份真实 config.json → 跑测试 → 还原"了:这个过程一旦中断就把用户配置留在测试值上
    #: (真实事故:set 面板保存被临时脚本调用,chat_enabled 被覆盖成 false)。现在断言落在
    #: ``window.config_path``(enable_test_mode 已把它重定向到 .tmp 沙盒),真实文件全程不碰。
    sandbox_cfg = ROOT / ".tmp" / "testmode" / "settings_test" / "config.json"

    real_before = (hashlib.sha256(config_mod.CONFIG_PATH.read_bytes()).hexdigest()
                   if config_mod.CONFIG_PATH.is_file() else "<不存在>")

    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("找不到模型目录")
        return 2

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    live2d.init()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)

    cfg = config_mod.Config.load()
    cfg.gaze_follow = False
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir), load_actions(model_dir))
    window.enable_test_mode("settings_test")
    cfg_path = window.config_path
    window.show()

    from pet.settings_dialog import SettingsDialog  # noqa: F401  (确保模块可导入)

    state = {"step": 0, "dialog": None}
    shots = ROOT / "shots"
    shots.mkdir(exist_ok=True)

    def step() -> None:
        state["step"] += 1
        n = state["step"]

        if n == 1:
            print("\n[1] 面板载入当前配置")
            dialog = window.make_settings_dialog()
            state["dialog"] = dialog
            dialog.show()
            check("窗口高度回填", dialog.window_height.value() == cfg.window_height,
                  f"{dialog.window_height.value()} vs {cfg.window_height}")
            check("缩放回填", abs(dialog.scale.value() - cfg.scale) < 1e-6)
            check("快捷键回填", dialog.hotkey_toggle_visible.text() == cfg.hotkey_toggle_visible)
            check("接口地址回填", dialog.chat_base_url.text() == cfg.chat_base_url)
            pixmap = dialog.grab()
            pixmap.save(str(shots / "settings.png"))
            print(f"  面板截图: shots/settings.png {pixmap.width()}x{pixmap.height()}")

        elif n == 2:
            print("\n[2] 改值并保存")
            dialog = state["dialog"]
            dialog.scale.setValue(0.6)
            dialog.window_height.setValue(420)
            dialog.opacity.setValue(0.75)
            dialog.fps.setValue(30)
            dialog.hotkey_toggle_visible.setText("ctrl+shift+F9")
            dialog.chat_model.setText("test-model")
            dialog.gaze_follow.setChecked(True)
            dialog._on_save()

        elif n == 3:
            print("\n[3] 检查配置落盘")
            on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
            check("scale 已写入", abs(on_disk["scale"] - 0.6) < 1e-6, str(on_disk.get("scale")))
            check("window_height 已写入", on_disk["window_height"] == 420)
            check("opacity 已写入", abs(on_disk["opacity"] - 0.75) < 1e-6)
            check("fps 已写入", on_disk["fps"] == 30)
            check("快捷键已写入", on_disk["hotkey_toggle_visible"] == "ctrl+shift+F9")
            check("chat_model 已写入", on_disk["chat_model"] == "test-model")

            print("\n[4] 检查即时生效(无需重启)")
            check("窗口不透明度已应用", abs(window.windowOpacity() - 0.75) < 0.02,
                  str(window.windowOpacity()))
            check("渲染定时器已改为 30fps",
                  abs(window.render_timer.interval() - 33) <= 2,
                  str(window.render_timer.interval()))
            check("窗口高度已应用", window.height() == 420, str(window.height()))
            check("快捷键已重新注册",
                  window.hotkeys.active.get("toggle_visible") == "ctrl+shift+F9",
                  str(window.hotkeys.active))
            check("对话客户端已重建",
                  window._chat_client.settings.model == "test-model",
                  window._chat_client.settings.model)

            print("\n[5] 取消不应改动配置")
            before = cfg_path.read_text(encoding="utf-8")
            dialog = window.make_settings_dialog()
            dialog.scale.setValue(1.9)
            dialog.reject()
            check("取消后文件未变", cfg_path.read_text(encoding="utf-8") == before)
            check("取消后内存值未变", abs(cfg.scale - 0.6) < 1e-6, str(cfg.scale))

            print("\n[6] 恢复默认")
            dialog = window.make_settings_dialog()
            dialog._on_reset_defaults()
            check("恢复默认后面板回到 0.85", abs(dialog.scale.value() - 0.85) < 1e-6,
                  str(dialog.scale.value()))
            dialog.reject()

            finish()

    def finish() -> None:
        real_after = (hashlib.sha256(config_mod.CONFIG_PATH.read_bytes()).hexdigest()
                      if config_mod.CONFIG_PATH.is_file() else "<不存在>")
        check("真实 config.json 全程未被触碰", real_after == real_before,
              f"{real_before[:12]}… → {real_after[:12]}…")
        print("  (写入都落在沙盒;真实文件哈希与测试前一致)")
        print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
        app.quit()

    timer = QTimer()
    timer.timeout.connect(step)
    timer.start(700)
    QTimer.singleShot(25_000, finish)

    app.exec()
    window.close()
    live2d.dispose()
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
