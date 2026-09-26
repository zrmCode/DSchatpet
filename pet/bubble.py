"""对话 UI:说话气泡 + 输入框。

两者都是无边框、置顶的小窗口,位置跟随桌宠;气泡不吃焦点(不打断你当前的输入),
输入框才吃焦点。样式走 QSS,深色半透明圆角,和深海主题一致。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPalette,
    QPen,
    QTextDocument,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

#: 气泡配色(底图由 Bubble 自己画,见 ``paintEvent``)
BUBBLE_BG = QColor(16, 32, 52, 222)
BUBBLE_BORDER = QColor(120, 190, 255, 90)
BUBBLE_RADIUS = 14.0

INPUT_QSS = """
QLineEdit {
    background-color: rgba(126, 200, 246, 246);
    color: #06283d;
    border: 1px solid rgba(255, 255, 255, 190);
    border-radius: 12px;
    padding: 8px 12px;
    font-size: 14px;
    selection-background-color: #ffffff;
    selection-color: #06283d;
}
"""

#: 文字颜色(底图自己画,label 只负责文字)
BUBBLE_QSS = "QLabel { background: transparent; color: #e8f2ff; }"

#: 气泡文字大小(像素)。**在代码里设置而不是只写 QSS**:
#: 量高度时用的字体必须与渲染字体一致,否则会少算行数导致裁字。
BUBBLE_FONT_PIXEL_SIZE = 14

#: 输入框里"聊天…"提示文字的颜色(天蓝底上用深一点的蓝灰)
INPUT_PLACEHOLDER_COLOR = "#2d6b93"

#: 气泡宽度范围(内容宽度,不含内边距)
BUBBLE_MAX_WIDTH = 320
BUBBLE_MIN_WIDTH = 120

#: 文字四周留白(像素)。用 **布局 margins** 实现,不用 QSS padding:
#: QSS padding 不进 ``heightForWidth`` 的账,只能靠猜;而猜错就会裁掉最后一行
#: (用户反馈"2 行回答只有一行高的对话框"就是这么来的)。
_BUBBLE_PAD_H = 14
_BUBBLE_PAD_V = 10

#: 兼容旧名字:总的水平/垂直"外框"尺寸 = 两侧留白之和
_BUBBLE_CHROME = _BUBBLE_PAD_H * 2
_BUBBLE_CHROME_V = _BUBBLE_PAD_V * 2


def _screen_geometry(anchor: QRect) -> QRect | None:
    screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen else None


class Bubble(QWidget):
    """桌宠的说话气泡。

    ``parent`` 传桌宠窗口:这样它就是桌宠的**附属窗口**(Windows 上是 owned window),
    层级与桌宠一致、随桌宠一起显示/隐藏,而不是自己单独置顶一层。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        #: 不吃焦点,避免打断用户正在别处的输入
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self.label = QLabel(self)
        self.label.setWordWrap(True)
        font = self.label.font()
        font.setPixelSize(BUBBLE_FONT_PIXEL_SIZE)
        self.label.setFont(font)        # 字体写死在代码里,保证量高与渲染一致
        self.label.setStyleSheet(BUBBLE_QSS)
        self.label.setTextInteractionFlags(Qt.NoTextInteraction)
        #: ⚠️ **必须锁定纯文本**。QLabel 默认是 AutoText:文本里只要出现 `&`、`<b>x</b>`
        #: 这类内容就会被当富文本/实体解析,排版结果与量高用的 ``setPlainText`` 不一致,
        #: 于是框按"少一行"算出来 —— 用户报的"2 行回答只有一行高的对话框"就是这么来的
        #: (模糊测试复现:`'Y_c&。Z\t得X&'` 量出 1 行、实际渲染 3 行)。
        self.label.setTextFormat(Qt.PlainText)
        #: 文字顶端对齐:量高时才有确定的参照,不会被垂直居中"摊平"
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        layout = QVBoxLayout(self)
        #: 留白用布局 margins 表达 —— label 的矩形就是文字矩形,量高不用猜 padding
        layout.setContentsMargins(
            _BUBBLE_PAD_H, _BUBBLE_PAD_V, _BUBBLE_PAD_H, _BUBBLE_PAD_V
        )
        layout.addWidget(self.label)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def paintEvent(self, event) -> None:      # noqa: N802 - Qt 命名
        """自己画圆角底图 + 描边(QSS 放在 label 上会干扰高度计算)。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(BUBBLE_BORDER, 1.0))
        painter.setBrush(BUBBLE_BG)
        painter.drawRoundedRect(rect, BUBBLE_RADIUS, BUBBLE_RADIUS)

    def show_text(self, text: str, anchor: QRect, seconds: int = 12) -> None:
        """在 ``anchor``(通常是桌宠窗口矩形)上方显示一段话。"""
        self.label.setText(text)
        self._fit_width(text)
        self.adjustSize()
        self.place(anchor)
        self.show()
        self.raise_()
        if seconds > 0:
            self._hide_timer.start(seconds * 1000)
        else:
            self._hide_timer.stop()

    def _fit_width(self, text: str) -> None:
        """算出气泡的精确尺寸并固定。

        宽度按最长一行估(夹在 [MIN, MAX] 之间);高度取**两种量法里较大的一个**:

        1. ``QLabel.heightForWidth`` —— 与真正渲染同一个排版引擎;
        2. ``QTextDocument`` 按文字区宽度排版后量高 —— 控件状态无关,结果稳定。

        ⚠️ 顺序很重要:**先解除高度约束再问引擎**。带着上一次的固定高度去问,
        ``heightForWidth`` 会返回离谱的值(单行文本曾返回 148,换行文本虚高 76);
        解除约束后实测它与真值一致(见下表),虚高问题消失。

        两法取大的理由(模糊测试实测,260 条随机文本 + 真值二分标定):

        | 文本 | 真值 | QTextDocument | heightForWidth |
        |---|---|---|---|
        | ``Y_c&。Z\\t得X&`` | 33 | **18(少算)** | 36 |
        | 其余样本 | 15/51/33/35 | 18/54/36/37 | 18/54/36/37 |

        含制表符时 ``QTextDocument`` 会把 tab 折算得更窄 → 少算行数 → 框只有一行高
        (用户报的"2 行回答只有一行高的对话框"的第一手复现就是这个)。

        ⚠️ 留白由布局 margins 精确表达(见 ``__init__``),不猜 QSS padding ——
        上一版就是猜错 padding,每边少算约 5.5 像素。
        """
        metrics = QFontMetrics(self.label.font())
        lines = text.splitlines() or [""]
        longest = max(metrics.horizontalAdvance(line) for line in lines)
        content = int(max(BUBBLE_MIN_WIDTH, min(BUBBLE_MAX_WIDTH, longest)))

        self.label.setMinimumHeight(0)          # 解除上一次的固定高度,否则引擎值会漂
        self.label.setMaximumHeight(16777215)
        self.label.setFixedWidth(content)
        engine = float(self.label.heightForWidth(content))
        document = self._document_height(text, content)
        needed = max(engine, document, float(metrics.height()))
        self.label.setFixedHeight(int(needed + 0.999) + 2)     # +2:取整余量

        #: 留一条诊断:万一还有"看不全"的情况,pet.log 里就有原文与两组量值可比对
        from .applog import log

        log(f"气泡: 「{text[:24]}{'…' if len(text) > 24 else ''}」{len(text)} 字 → "
            f"宽 {content} 高 {self.label.height()}(引擎 {engine:.0f} / 文档 {document:.0f})")

    def _document_height(self, text: str, text_width: int) -> float:
        """在给定文本宽度下量出换行后的高度(不含内边距)。

        ⚠️ 必须显式设置字体:不设会用更小的默认字体 → 行数算少 → 文本被裁。
        """
        document = QTextDocument()
        document.setDefaultFont(self.label.font())
        document.setDocumentMargin(0)
        document.setPlainText(text)
        document.setTextWidth(text_width)
        return document.size().height()

    def place(self, anchor: QRect) -> None:
        """贴着桌宠上方居中显示;上方放不下就改放下面。"""
        geo = _screen_geometry(anchor)
        x = anchor.center().x() - self.width() // 2
        y = anchor.top() - self.height() - 10
        if geo is not None:
            if y < geo.top():
                y = anchor.bottom() + 10
            x = max(geo.left() + 4, min(x, geo.right() - self.width() - 4))
            y = max(geo.top() + 4, min(y, geo.bottom() - self.height() - 4))
        self.move(int(x), int(y))

    def stick_to(self, anchor: QRect) -> None:
        """桌宠移动时跟随(有内容才跟)。"""
        if self.isVisible() and self.label.text():
            self.place(anchor)


class ChatInput(QWidget):
    """单行输入框:锚定在桌宠**下方不远处**,跟随桌宠拖动。

    两种出现方式:
    - ``show_passive()``:鼠标靠近时自动出现,**不抢焦点**(不打断你在别处打字)
    - ``open_at()``:双击桌宠 / 菜单打开时出现,并**聚焦**好让你直接输入

    回车发送,Esc 关闭。
    """

    submitted = Signal(str)

    #: 与桌宠底边的间距(像素)——"下方不远处";构造后可由配置覆盖
    GAP_BELOW = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        #: 提交回调:由桌宠窗口提供,返回 True 表示"接收方收下了这句话"。
        #: 设了它就由它负责发送(不再走 ``submitted`` 信号),见 ``_on_return``。
        self.on_submit: Callable[[str], bool] | None = None
        #: ``parent`` 传桌宠窗口:成为其附属窗口(owned window),
        #: **层级与桌宠一致**(不再自己单独置顶一层),并随桌宠一起显隐。
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        #: 被动显示时不激活窗口 → 不会抢走你正在别处输入的焦点
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        #: 与桌宠底边的间距,由窗口按配置设置
        self.gap_below = self.GAP_BELOW

        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("聊天…")
        self.edit.setToolTip("回车发送,Esc 关闭\n(把鼠标移到桌宠旁边就会自动出现)")
        self.edit.setMinimumWidth(240)
        self.edit.setStyleSheet(INPUT_QSS)
        palette = self.edit.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(INPUT_PLACEHOLDER_COLOR))
        self.edit.setPalette(palette)
        self.edit.returnPressed.connect(self._on_return)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)

    # ---------------------------------------------------------------- 状态

    def is_engaged(self) -> bool:
        """是否"正在使用":有焦点或已经输入了内容 —— 此时不该被自动隐藏。"""
        return self.edit.hasFocus() or bool(self.edit.text().strip())

    # ---------------------------------------------------------------- 显示

    def _place_below(self, anchor: QRect) -> None:
        """放在桌宠正下方(用户要求:「固定在下方不远处」),并跟随拖动。

        ⚠️ 下方空间不够时**不翻到桌宠上方**,而是贴住屏幕可用区的底边 ——
        翻上去会让人一眼找不到输入框(实测用户当前窗口位置 y=454、任务栏占掉底部时
        就会翻到上方,间距变成 -414 像素)。
        允许与桌宠底部轻微重叠:模型在窗口里是居中偏上的,底部这条基本是空白。
        """
        self.adjustSize()
        geo = _screen_geometry(anchor)
        x = anchor.center().x() - self.width() // 2
        y = anchor.bottom() + self.gap_below
        if geo is not None:
            x = max(geo.left() + 4, min(x, geo.right() - self.width() - 4))
            y = min(y, geo.bottom() - self.height() - 4)     # 贴住底边
            y = max(geo.top() + 4, y)
        self.move(int(x), int(y))

    def show_passive(self, anchor: QRect) -> None:
        """鼠标靠近时出现:不抢焦点。"""
        self._place_below(anchor)
        self.show()
        self.raise_()

    def open_at(self, anchor: QRect) -> None:
        """双击/菜单打开:出现并聚焦(方便直接打字)。"""
        self._place_below(anchor)
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus(Qt.PopupFocusReason)

    def follow(self, anchor: QRect) -> None:
        """桌宠被拖动时跟随。"""
        if self.isVisible():
            self._place_below(anchor)

    # ---------------------------------------------------------------- 交互

    def _on_return(self) -> None:
        """回车提交。

        ⚠️ 顺序很关键:以前是"**先**清空输入框并隐藏,**再** emit" —— 于是接收方因为
        "上一句还在飞"或"没有可用后端"直接 return 时,用户刚打的字就被**静默丢弃**了
        (没发出去、没进历史、也没还回输入框,只能重打)。现在先问接收方收不收,
        **收下了才**清空收起;没收下就把文字留在框里、框也留着。
        """
        text = self.edit.text().strip()
        if not text:
            return

        if self.on_submit is not None:
            if not self.on_submit(text):
                return                      # 没被接收 → 文字与输入框都保留
        else:
            self.submitted.emit(text)

        self.edit.clear()
        self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)
