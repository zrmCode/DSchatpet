"""生成可公开的界面截图(只抓控件本身,绝不含桌面内容)。

为什么不用系统截图:`shots/` 里那些 `chat.png` / `settings.png` 是无 alpha 的屏幕抓图,
可能把桌面、别的窗口甚至私人内容一起拍进去 —— 开源前不能这么发。

这里的做法是**分别抓每个控件**(桌宠抓 OpenGL 窗口帧、气泡与输入框抓 QWidget),
再自己拼到一张柔和渐变背景上。原理上不可能拍到桌面,而且随时能重跑。

产物写到 ``docs/screenshots/``:

  - ``pet.png``      桌宠本体(透明背景,可直接放到 README 顶部)
  - ``chat.png``     对话气泡 + 天蓝输入框 + 桌宠 拼版
  - ``settings.png`` 设置面板

用法::

    .venv\\Scripts\\python.exe tools\\make_screenshots.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "screenshots"

#: 拼版背景:上下渐变的深海色(桌宠主体偏浅色,深底更清楚)
BG_TOP = (30, 47, 72)
BG_BOTTOM = (12, 20, 34)


def _pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("需要 pillow(只用于生成截图): .venv\\Scripts\\pip install pillow")
        return 2

    import live2d.v3 as live2d
    from PySide6.QtGui import QImage, QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from pet import config as config_mod
    from pet.actions import load_actions
    from pet.window import PetWindow

    def to_pil(qimage: QImage) -> "Image.Image":
        """QImage → PIL(走 RGBA8888,避免行对齐/字节序差异把图弄花)。"""
        converted = qimage.convertToFormat(QImage.Format_RGBA8888)
        return Image.frombytes("RGBA", (converted.width(), converted.height()),
                               converted.constBits().tobytes())

    def gradient(width: int, height: int) -> "Image.Image":
        image = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(image)
        for y in range(height):
            t = y / max(1, height - 1)
            draw.line([(0, y), (width, y)],
                      fill=tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))
        return image

    cfg = config_mod.Config.load()
    cfg.window_x = 200
    cfg.window_y = 200
    cfg.gaze_follow = False
    cfg.idle_motion = False
    cfg.poke_reaction = False
    cfg.chat_hover = False
    cfg.idle_autonomy = False
    cfg.idle_llm_thoughts = False

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    live2d.init()
    app = QApplication(sys.argv[:1])
    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("本机没有模型,无法生成截图(先按 assets/README.md 放置模型)")
        return 2

    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir),
                       load_actions(model_dir))
    window.persist_config = False
    window.show()
    window.input_timer.stop()
    window._idle_action_timer.stop()
    window._idle_thought_timer.stop()
    window._poke_timer.stop()
    _pump(app, 4.0)

    OUT.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[str, tuple[int, int]]] = []

    def save(image, name: str) -> None:
        path = OUT / name
        image.save(path)
        saved.append((name, image.size))
        print(f"  docs/screenshots/{name}  {image.size[0]}x{image.size[1]}  "
              f"{path.stat().st_size // 1024} KB")

    # ------------------------------------------------ 1) 桌宠本体(带 alpha)
    pet_pil = to_pil(window.grab().toImage())
    save(pet_pil, "pet.png")

    # ------------------------------------------------ 2) 对话界面拼版
    reply = "怎么啦？我陪你歇会儿嘛～别太累了,记得喝口水哦"
    window.bubble.show_text(reply, window.frameGeometry(), 0)
    window.chat_input.show_passive(window.frameGeometry())
    _pump(app, 0.8)
    bubble_pil = to_pil(window.bubble.grab().toImage())
    input_pil = to_pil(window.chat_input.grab().toImage())

    margin = 36
    canvas_w = max(pet_pil.width, bubble_pil.width, input_pil.width) + margin * 2
    canvas_h = margin + bubble_pil.height + 16 + pet_pil.height + 2 + input_pil.height + margin
    canvas = gradient(canvas_w, canvas_h)
    y = margin
    canvas.paste(bubble_pil, ((canvas_w - bubble_pil.width) // 2, y), bubble_pil)
    y += bubble_pil.height + 16
    canvas.paste(pet_pil, ((canvas_w - pet_pil.width) // 2, y), pet_pil)
    y += pet_pil.height + 2
    canvas.paste(input_pil, ((canvas_w - input_pil.width) // 2, y), input_pil)
    save(canvas, "chat.png")
    window.bubble.hide()
    window.chat_input.hide()

    # ------------------------------------------------ 3) 设置面板(抓对话框自己)
    dialog = window.make_settings_dialog()
    dialog.show()
    _pump(app, 1.2)
    save(to_pil(dialog.grab().toImage()).convert("RGB"), "settings.png")
    dialog.close()

    print("\n生成完毕。这些图里只含本程序自己的控件,不含桌面内容。")
    window.hide()
    window.close()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
