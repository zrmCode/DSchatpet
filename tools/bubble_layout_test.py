"""气泡(回复框)排版验证:文字必须**完整显示**,不能被裁,也不能虚高。

用户反馈过两次裁字,这里把踩过的坑都固化成回归:

  1. ``QFontMetrics.boundingRect`` 估高度时**中文会少算一行** → 最后一行被裁;
  2. 用"QSS padding 常量 + QTextDocument 高度"估高度:文字实际起点还包含行框上方留白
     (实测该文本是 16.5 逻辑像素,而代码假设 11),每边少算约 5.5 像素,
     于是"2 行回答只有一行高的对话框" —— 最后一行底部被切掉。

⚠️ **本文件的判据刻意不依赖被测代码的算法**:
把同一段文字、同一宽度放进一个"超高画布"的同款 label 里渲染,量出文字**墨迹的实际范围**
(亮像素的行区间)当标尺;再量气泡真实渲染出来的墨迹范围。
框太矮时最后一行会被切,墨迹范围必然变小 —— 这是渲染结果与渲染结果比,
不是用自己的公式验自己的公式(上一版就是这么"通过"的,所以漏掉了这个 bug)。

用法::

    .venv\\Scripts\\python.exe tools\\bubble_layout_test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFontMetrics, QGuiApplication, QSurfaceFormat
from PySide6.QtWidgets import QApplication, QLabel

from pet.bubble import BUBBLE_FONT_PIXEL_SIZE, BUBBLE_MAX_WIDTH, BUBBLE_MIN_WIDTH, Bubble

_results = {"pass": 0, "fail": 0}

LONG = ("今天天气真好呀,阳光晒得我尾巴都暖乎乎的~你有没有出去晒晒太阳呀?"
        "要是没有的话,我们就一起在窗边待着吧,我可以给你讲个关于深海的小故事,"
        "虽然我记性不太好,可能会讲着讲着就忘了哈哈哈(ﾉ>ω<)ﾉ")

#: 用户报的正是这种:两行中文回复
TWO_LINES = "怎么啦？我陪你歇会儿嘛～别太累了,记得喝口水哦"


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


# ------------------------------------------------------------------ 独立标尺

def _ink_rows(image) -> list[int]:
    """找出"有文字墨迹"的行号(文字是亮色,背景是深蓝)。"""
    rows: list[int] = []
    for y in range(image.height()):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            if color.red() > 150 and color.green() > 170 and color.blue() > 190:
                rows.append(y)
                break
    return rows


def _segments(rows: list[int]) -> list[tuple[int, int]]:
    """把行号切成"文字行"段(行间空白超过 2 像素就算换行)。"""
    if not rows:
        return []
    segments: list[tuple[int, int]] = []
    start = rows[0]
    for current, nxt in zip(rows, rows[1:] + [None]):
        if nxt is None or nxt - current > 2:
            segments.append((start, current))
            start = nxt  # type: ignore[assignment]
    return segments


def _pump(app: QApplication, times: int = 4) -> None:
    for _ in range(times):
        app.processEvents()
        time.sleep(0.02)


class RenderProbe:
    """超高画布的标尺:同字体、同宽度、顶端对齐、零内边距,给足高度看文字**本该**占多高。"""

    #: 画布高度(逻辑像素),远大于任何一行文本所需
    TALL = 1200

    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.label = QLabel()
        self.label.setWordWrap(True)
        font = self.label.font()
        font.setPixelSize(BUBBLE_FONT_PIXEL_SIZE)
        self.label.setFont(font)
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        #: 必须与气泡里的 label 同模式(纯文本):AutoText 会把 `&`/`<b>` 当富文本解析,
        #: 两边排版就不一样了,标尺也就失去意义
        self.label.setTextFormat(Qt.PlainText)
        #: 只是标尺的衬底:背景与气泡同色,亮像素判据才一致(不留内边距)
        self.label.setStyleSheet(
            "QLabel { background-color: rgb(16, 32, 52); color: #e8f2ff; }"
        )
        self.label.show()

    def measure(self, text: str, width: int) -> tuple[list[tuple[int, int]], int]:
        """返回(文字行段, 墨迹总高度)—— 单位为设备像素。"""
        self.label.setFixedSize(int(width), self.TALL)
        self.label.setText(text)
        _pump(self.app)
        rows = _ink_rows(self.label.grab().toImage())
        segments = _segments(rows)
        span = (rows[-1] - rows[0] + 1) if rows else 0
        return segments, span

    def close(self) -> None:
        self.label.hide()


def bubble_render(app: QApplication, bubble: Bubble, text: str, anchor: QRect):
    """返回气泡**真实渲染**结果:(窗口高, 文字行段, 墨迹高度, 设备像素比)。"""
    bubble.show_text(text, anchor, 0)
    _pump(app)
    image = bubble.grab().toImage()
    rows = _ink_rows(image)
    segments = _segments(rows)
    span = (rows[-1] - rows[0] + 1) if rows else 0
    dpr = image.height() / max(1, bubble.height())
    return image.height(), segments, span, dpr, rows


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv[:1])

    bubble = Bubble()
    bubble.show()
    probe = RenderProbe(app)
    _pump(app)

    screen = QGuiApplication.primaryScreen().availableGeometry()
    middle = QRect(400, 300, 373, 373)

    print("\n[1] 渲染墨迹范围 vs 超高画布标尺(裁字会让墨迹变少)")
    cases = [
        ("7 字(1 行)", "嗨呀~你来啦!"),
        ("两行中文(用户报的)", TWO_LINES),
        ("40 字", "怎么啦？我陪你歇会儿嘛～别太累了,记得喝口水哦,我一直都在这里陪着你"),
        ("48 字", "今天天气真好呀,阳光晒得我尾巴都暖乎乎的,你要不要也来晒晒太阳?我一直陪着你哦,不用一个人扛着啦"),
        ("73 字", "今天天气真好呀,阳光晒得我尾巴都暖乎乎的,你要不要也来晒晒太阳?我一直陪着你哦,不用一个人扛着啦,"
                  "累了就靠过来休息一会儿吧,我会安静地待在你身边的"),
        ("首行换行", "好的呀!\n我这就陪着你,慢慢来不用急的"),
        ("含 emoji", "好耶(๑•̀ㅂ•́)و✧ 那我们一起去吧,路上小心哦"),
        #: ⚠️ 模糊测试复现出来的元凶:制表符 + `&` + `<b>`。
        #: QTextDocument 会把 tab 折算得更窄 → 少算行数 → 框只有一行高(真值需要 3 行)。
        ("含制表符与 &", "Y_c&。Z\t得X&"),
        ("含尖括号(别当富文本)", "这段是<b>加粗</b>的文字,应该原样显示才对不要被吃掉"),
        ("长文本", LONG),
        ("极长(2 倍)", LONG * 2),
    ]
    for label, text in cases:
        content = bubble_content_width(bubble, text)
        want_segments, want_span = probe.measure(text, content)
        window_h, got_segments, got_span, dpr, rows = bubble_render(app, bubble, text, middle)
        tolerance = 2 * dpr                       # 抗锯齿/取整容许 2 逻辑像素
        check(f"{label}:墨迹完整({got_span} ≥ {want_span - tolerance:.0f} 设备像素)",
              got_span >= want_span - tolerance,
              f"实际 {got_span} vs 标尺 {want_span} (框高 {window_h} 设备像素)")
        check(f"{label}:行数不被吞({len(got_segments)} 行 == 标尺 {len(want_segments)} 行)",
              len(got_segments) == len(want_segments),
              f"{got_segments} vs {want_segments}")

    print("\n[2] 用户报的那一条:两行回复的框必须不是「一行高」")
    content = bubble_content_width(bubble, TWO_LINES)
    want_segments, want_span = probe.measure(TWO_LINES, content)
    window_h, got_segments, got_span, dpr, rows = bubble_render(app, bubble, TWO_LINES, middle)
    check("标尺确认这段文字确实是两行", len(want_segments) == 2, str(want_segments))
    check(f"框高能装下两行(高度 {window_h / dpr:.0f} ≥ {want_span / dpr:.0f} 逻辑像素)",
          window_h >= want_span, f"{window_h} vs {want_span} 设备像素")
    check("气泡里也渲染出了两行", len(got_segments) == 2, str(got_segments))
    check("最后一行没贴到底边(底部还有留白)",
          rows and (window_h - rows[-1]) > 4,
          f"末行 {rows[-1] if rows else '-'} / 框高 {window_h}")

    print("\n[3] 短文本不该虚高(曾经 7 个字给 120 像素高)")
    bubble.show_text("嗨呀~你来啦!", middle, 0)
    _pump(app)
    check("单行文本高度合理(< 70 逻辑像素)", bubble.label.height() < 70, str(bubble.label.height()))

    print("\n[4] 宽度仍夹在 [最小, 最大] 之间")
    for label, text in (("很短", "嗨"), ("很长", LONG), ("两行", TWO_LINES)):
        bubble.show_text(text, middle, 0)
        _pump(app)
        width = bubble.label.width()
        check(f"{label}:文字区宽度 {width} 在 [{BUBBLE_MIN_WIDTH}, {BUBBLE_MAX_WIDTH}]",
              BUBBLE_MIN_WIDTH <= width <= BUBBLE_MAX_WIDTH, str(width))

    print("\n[5] 屏幕四角都要完整可见且不裁字")
    anchors = {
        "顶部": QRect(screen.left() + 100, screen.top() + 2, 373, 373),
        "底部": QRect(screen.left() + 100, screen.bottom() - 372, 373, 373),
        "左上角": QRect(screen.left() + 2, screen.top() + 2, 373, 373),
        "右下角": QRect(screen.right() - 372, screen.bottom() - 372, 373, 373),
    }
    content = bubble_content_width(bubble, LONG)
    _, want_span = probe.measure(LONG, content)
    for label, anchor in anchors.items():
        window_h, _, got_span, dpr, _ = bubble_render(app, bubble, LONG, anchor)
        rect = bubble.frameGeometry()
        check(f"{label}:气泡完全在屏内且不裁字",
              screen.contains(rect) and got_span >= want_span - 2 * dpr,
              f"气泡 {rect.width()}x{rect.height()} @({rect.x()},{rect.y()}) 屏幕 {screen}")

    print("\n[6] 尺寸可复现(同一文本两次显示结果一致)")
    bubble.show_text(LONG, middle, 0)
    _pump(app)
    first = (bubble.label.width(), bubble.label.height())
    bubble.hide()
    bubble.show_text(LONG, middle, 0)
    _pump(app)
    second = (bubble.label.width(), bubble.label.height())
    check(f"两次尺寸一致 {first} == {second}", first == second, f"{first} vs {second}")

    print("\n[7] 字体是代码里指定的(量高与渲染同源)")
    metrics = QFontMetrics(bubble.label.font())
    check("气泡字体高度合理(11~20 像素)", 11 <= metrics.height() <= 20, str(metrics.height()))

    print("\n[8] 模糊测试:随机文本形态都不许裁字(固定种子,可复现)")
    import random

    pool = list("怎么啦我陪你歇会儿嘛别太累了记得喝口水哦今天天气真好阳光晒得尾巴都暖乎乎")
    pool += list("abcXYZ .,!?~～、。,!?:;()()[]{}<>*#-_=+/\\|@$%^&'\"")
    pool += ["…", "～", "❤", "(๑•̀ㅂ•́)و✧", "(ﾉ>ω<)ﾉ", "😊", "🐳", "\n", "\t",
             "**粗体**", "<b>x</b>", "&amp;"]
    rng = random.Random(20260925)
    bad: list[str] = []
    tested = 0
    for _ in range(60):
        text = "".join(rng.choice(pool) for _ in range(rng.randint(1, 90))).strip()
        if not text:
            continue
        tested += 1
        content = bubble_content_width(bubble, text)
        _, want_span = probe.measure(text, content)
        window_h, _, got_span, dpr, _ = bubble_render(app, bubble, text, middle)
        if got_span + 2 * dpr < want_span:
            bad.append(f"{text[:30]!r}(墨迹 {got_span} < {want_span})")
    check(f"随机 {tested} 条文本都没裁字", not bad, "; ".join(bad[:3]))

    bubble.hide()
    probe.close()
    print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    app.quit()
    return 1 if _results["fail"] else 0


def bubble_content_width(bubble: Bubble, text: str) -> int:
    """气泡会给这段文字分配多宽的文字区(与 ``_fit_width`` 的宽度规则一致)。"""
    metrics = QFontMetrics(bubble.label.font())
    lines = text.splitlines() or [""]
    longest = max(metrics.horizontalAdvance(line) for line in lines)
    return int(max(BUBBLE_MIN_WIDTH, min(BUBBLE_MAX_WIDTH, longest)))


if __name__ == "__main__":
    raise SystemExit(main())
