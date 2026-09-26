"""截图工具:把桌宠窗口的当前画面存成 PNG,用于肉眼确认渲染是否正常。

透明窗口的截图会保留 alpha 通道(便于检查"模型有没有画出来"和"背景是否真透明")。
灰度化后的第二张图把 alpha 当亮度输出,透明区一眼可辨。

用法::

    .venv\\Scripts\\python.exe tools\\shot.py [输出目录] [--delay 秒]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live2d.v3 as live2d
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QSurfaceFormat
from PySide6.QtWidgets import QApplication

from pet import config as config_mod
from pet.actions import load_actions
from pet.window import PetWindow


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="桌宠窗口截图")
    parser.add_argument("out_dir", nargs="?", default=str(ROOT / "shots"))
    parser.add_argument("--delay", type=float, default=2.0, help="等待渲染稳定的秒数")
    parser.add_argument("--expression", default=None, help="截图前切换到的表情名")
    parser.add_argument("--scale", type=float, default=None, help="临时缩放(不写入配置)")
    parser.add_argument("--height", type=int, default=None, help="临时窗口高度(不写入配置)")
    parser.add_argument("--out", default="shot.png", help="输出文件名")
    parser.add_argument("--gaze", default=None, help="固定视线目标,格式 X,Y(如 -1,0 看左,1,0 看右)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = config_mod.Config.load()
    if args.scale is not None:
        cfg.scale = args.scale
    if args.height is not None:
        cfg.window_height = args.height
    if args.gaze is not None:
        # 固定视线时不希望被鼠标跟随拉回去
        cfg.gaze_follow = False
    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("找不到模型目录")
        return 2
    actions = load_actions(model_dir)

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    live2d.init()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)

    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir), actions)
    window.enable_test_mode("shot")
    window.show()

    def shoot() -> None:
        if args.expression:
            print("切换表情:", args.expression, window.pet.set_expression(args.expression))
        image = window.grabFramebuffer()
        if image.isNull():
            print("截图失败:grabFramebuffer 返回空")
            app.quit()
            return

        color_path = out_dir / args.out
        image.save(str(color_path))

        # alpha 单独导出一张(透明区为黑,模型区为白)
        alpha = image.convertToFormat(QImage.Format.Format_Grayscale8)
        alpha_path = out_dir / f"alpha-{args.out}"
        alpha.save(str(alpha_path))

        opaque = sum(
            1 for y in range(0, image.height(), 4) for x in range(0, image.width(), 4)
            if image.pixelColor(x, y).alpha() > 10
        )
        total = len(range(0, image.height(), 4)) * len(range(0, image.width(), 4))
        print(f"已保存 {color_path} / {alpha_path}")
        print(f"不透明像素占比 ≈ {opaque / max(1, total):.1%}(用于确认模型确实画出来了)")
        print("诊断:", window.diagnostics())
        app.quit()

    def apply_gaze() -> None:
        if args.gaze is None:
            return
        try:
            gx, gy = (float(v) for v in args.gaze.split(","))
        except ValueError:
            print("--gaze 格式应为 X,Y")
            return
        window.pet.set_gaze(gx, gy)
        print(f"视线目标已固定为 ({gx}, {gy})")

    QTimer.singleShot(max(100, int(args.delay * 1000) - 400), apply_gaze)
    QTimer.singleShot(int(args.delay * 1000), shoot)

    code = app.exec()
    window.close()
    live2d.dispose()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
