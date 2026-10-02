"""设置面板:把 ``config.json`` 的所有字段做成可视化编辑,保存即生效。

设计取舍:
- 直接编辑传进来的 ``Config`` 对象(而不是自己复制一份),保存后由窗口 ``apply_config()``
  把改动即时应用到渲染、快捷键与注册表,不需要重启。
- 「测试对话」用**面板里当前的值**去调一次接口,方便先验证 Key/中转地址再保存。
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .chat import PROVIDER_LABELS, PROVIDER_PRESETS, ChatClient, ChatSettings
from .config import Config

#: 下拉里的顺序;最后一个是"自定义"
PROVIDER_ORDER = ["deepseek", "openai", "ollama", "custom"]


def _fixed_hint(text: str) -> QLabel:
    """灰色小字提示:说明某项参数为什么不给改(免得用户到处找)。"""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #888; font-size: 11px;")
    return label


class _ProbeWorker(QThread):
    """后台跑一次真实对话,避免把设置面板卡住。"""

    done = Signal(bool, str)

    def __init__(self, client: ChatClient, parent=None) -> None:
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        try:
            self.done.emit(True, self._client.ask("用一句话跟我打个招呼"))
        except Exception as exc:
            self.done.emit(False, str(exc))


class SettingsDialog(QDialog):
    """设置对话框。保存成功后发 ``applied`` 信号。"""

    applied = Signal()

    def __init__(self, cfg: Config, parent=None, owner=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        #: 桌宠窗口(用它读写养成档案与记忆库);测试里没有也能跑,只是记忆页禁用
        self.owner = owner
        self._worker: _ProbeWorker | None = None
        self.setWindowTitle("DS鲸鱼娘 桌宠 · 设置")
        self.setMinimumWidth(520)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)   # 别被桌宠挡住
        self._build_ui()
        self._load_values()
        self._refresh_memory_page()

    # ---------------------------------------------------------------- 构建

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        tabs = QTabWidget()

        # ---------- 页 1:外观与交互 ----------
        page_look = QWidget()
        page_look_layout = QVBoxLayout(page_look)

        # --- 外观 ---
        #: ⚠️ 窗口高度 / 模型缩放 / 帧率上限**故意不放进面板**:这三个是实测调好的值
        #: (缩放 1.0 会把模型右边缘切掉;帧率调高只会发热掉帧),让用户改只会改坏自己。
        #: 想微调就直接改 config.json 里的 window_height / scale / fps。
        look = QGroupBox("外观")
        look_form = QFormLayout(look)
        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(0.2, 1.0)
        self.opacity.setSingleStep(0.05)
        self.opacity.setDecimals(2)
        self.always_on_top = QCheckBox("窗口置顶")
        look_form.addRow("不透明度", self.opacity)
        look_form.addRow("", self.always_on_top)
        look_form.addRow("", _fixed_hint("尺寸、缩放、帧率由程序按模型实测决定(不进面板)"))
        page_look_layout.addWidget(look)

        # --- 交互 ---
        #: 视线跟随的两个调参(跟随幅度 / 平滑系数)同样不进面板:调过头会"眼睛乱飘"
        #: 或看起来像卡住,开关本身留着就够了。
        interact = QGroupBox("交互")
        interact_form = QFormLayout(interact)
        self.click_through = QCheckBox("透明区域点击穿透(不挡桌面操作)")
        self.gaze_follow = QCheckBox("视线跟随鼠标")
        self.idle_motion = QCheckBox("待机动画循环")
        self.poke_reaction = QCheckBox("点一下时让 AI 回应(说话 + 表情)")
        self.poke_reaction.setToolTip(
            "单击桌宠时,把「被戳了」这件事告诉模型,由它自己决定说什么、配什么表情。\n"
            "需要配好对话后端;没配后端时点击不做任何事(不打扰你)。\n"
            "原来的「点一下就随机变表情」已取消。"
        )
        interact_form.addRow("", self.click_through)
        interact_form.addRow("", self.gaze_follow)
        interact_form.addRow("", self.idle_motion)
        interact_form.addRow("", self.poke_reaction)
        interact_form.addRow("", _fixed_hint("视线跟随的幅度/平滑由程序调好,只留开关"))
        page_look_layout.addWidget(interact)

        # --- 对话 ---
        chat = QGroupBox("对话")
        chat_form = QFormLayout(chat)
        self.chat_enabled = QCheckBox("启用对话(鼠标靠近桌宠 / 双击 / 快捷键)")
        self.chat_hover = QCheckBox("鼠标靠近时自动弹出输入框")
        self.provider = QComboBox()
        for key in PROVIDER_ORDER:
            if key == "custom":
                self.provider.addItem("自定义(自己填地址与模型名)", "custom")
            else:
                self.provider.addItem(PROVIDER_LABELS.get(key, key), key)
        self.provider.currentIndexChanged.connect(self._on_provider_changed)
        self.provider_hint = QLabel(
            "DeepSeek / OpenAI 需要 API Key;Ollama 等本机服务不需要 Key。"
            "分享给别人时,**每台机器各自填自己的 Key**,或改用本地模型。"
        )
        self.provider_hint.setWordWrap(True)
        self.provider_hint.setStyleSheet("color: #888;")
        self.chat_base_url = QLineEdit()
        self.chat_model = QLineEdit()
        self.chat_api_key = QLineEdit()
        self.chat_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.chat_api_key.setPlaceholderText("留空则用环境变量或 DSH 凭据")
        self.chat_api_key_env = QLineEdit()
        self.chat_use_dsh_credentials = QCheckBox("允许复用 DSH 凭据文件里的 Key")
        self.chat_history = QSpinBox()
        self.chat_history.setRange(0, 50)
        self.chat_history.setSuffix(" 轮")
        self.chat_timeout = QDoubleSpinBox()
        self.chat_timeout.setRange(5.0, 120.0)
        self.chat_timeout.setSuffix(" 秒")
        self.chat_bubble_seconds = QSpinBox()
        self.chat_bubble_seconds.setRange(0, 120)
        self.chat_bubble_seconds.setSuffix(" 秒(0=不自动消失)")
        self.chat_persona = QPlainTextEdit()
        self.chat_persona.setPlaceholderText("留空 = 使用内置人设(深海鲸鱼娘,回复短、纯文本)")
        self.chat_persona.setFixedHeight(70)

        chat_form.addRow("", self.chat_enabled)
        chat_form.addRow("", self.chat_hover)
        chat_form.addRow("服务商", self.provider)
        chat_form.addRow("", self.provider_hint)
        chat_form.addRow("接口地址", self.chat_base_url)
        chat_form.addRow("模型名", self.chat_model)
        chat_form.addRow("API Key", self.chat_api_key)
        chat_form.addRow("Key 环境变量", self.chat_api_key_env)
        chat_form.addRow("", self.chat_use_dsh_credentials)
        chat_form.addRow("记忆轮数", self.chat_history)
        chat_form.addRow("请求超时", self.chat_timeout)
        chat_form.addRow("气泡停留", self.chat_bubble_seconds)
        chat_form.addRow("自定义人设", self.chat_persona)

        # ---------- 页 2:对话 ----------
        page_chat = QWidget()
        page_chat_layout = QVBoxLayout(page_chat)
        page_chat_layout.addWidget(chat)

        # --- 待机与自主行为 ---
        #: 待机节奏的两个间隔(随机行为间隔 / 思考间隔)同样不进面板:太密会显得吵、
        #: 「想事情」太密还会白花钱。要调就改 config.json 的
        #: idle_action_interval / idle_thought_interval。
        idle = QGroupBox("待机与自主行为")
        idle_form = QFormLayout(idle)
        self.chat_model_actions = QCheckBox("让模型自己选表情/动作(配合语气)")
        self.chat_use_tools = QCheckBox("优先用工具调用(接口不支持时自动改用文字指令)")
        self.idle_autonomy = QCheckBox("待机时自己换表情 / 做小动作(免费、无需 Key)")
        self.idle_llm_thoughts = QCheckBox("待机时让模型自己「想事情」—— 会消耗少量 API 费用")
        self.idle_thought_bubble = QCheckBox("把内心独白显示在气泡里(关掉就只做动作)")
        idle_hint = QLabel("「想事情」需要可用的对话后端(Key 或本地模型),没有时会自动跳过,不会报错。\n"
                           "待机节奏(多久动一次、多久想一次)由程序控制,不进面板。")
        idle_hint.setWordWrap(True)
        idle_hint.setStyleSheet("color: #888;")
        idle_form.addRow("", self.chat_model_actions)
        idle_form.addRow("", self.chat_use_tools)
        idle_form.addRow("", self.idle_autonomy)
        idle_form.addRow("", self.idle_llm_thoughts)
        idle_form.addRow("", self.idle_thought_bubble)
        idle_form.addRow("", idle_hint)

        # ---------- 页 3:待机与自主行为 ----------
        page_idle = QWidget()
        page_idle_layout = QVBoxLayout(page_idle)
        page_idle_layout.addWidget(idle)

        test_row = QHBoxLayout()
        self.test_button = QPushButton("测试对话")
        self.test_button.clicked.connect(self._on_test_chat)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        page_idle_layout.addLayout(test_row)

        # --- 系统 ---
        system = QGroupBox("系统")
        system_form = QFormLayout(system)
        self.hotkey_toggle_visible = QLineEdit()
        self.hotkey_open_chat = QLineEdit()
        self.autostart = QCheckBox("开机自启")
        system_form.addRow("显示/隐藏快捷键", self.hotkey_toggle_visible)
        system_form.addRow("聊天快捷键", self.hotkey_open_chat)
        system_form.addRow("", self.autostart)
        page_idle_layout.addWidget(system)

        hint = QLabel("快捷键写法:ctrl+alt+W、shift+win+F5;留空表示不注册。")
        hint.setStyleSheet("color: #888;")
        page_idle_layout.addWidget(hint)

        # ---------- 页 4:记忆与养成 ----------
        page_memory = QWidget()
        page_memory_layout = QVBoxLayout(page_memory)
        page_memory_layout.addWidget(self._build_memory_page())

        tabs.addTab(page_look, "外观与交互")
        tabs.addTab(page_chat, "对话")
        tabs.addTab(page_idle, "待机与自主行为")
        tabs.addTab(page_memory, "记忆与养成")
        root.addWidget(tabs)

        # --- 按钮 ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存并生效")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        reset = QPushButton("恢复默认")
        reset.clicked.connect(self._on_reset_defaults)
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        root.addWidget(buttons)

    # ---------------------------------------------------------------- 记忆页

    def _build_memory_page(self) -> QGroupBox:
        """「记忆与养成」页:开关 + 档案信息 + 记忆列表(增删/清空/导出导入)。"""
        box = QGroupBox("记忆与养成")
        layout = QVBoxLayout(box)

        form = QFormLayout()
        self.memory_enabled = QCheckBox("启用记忆与养成(记忆存在本地,换模型也不丢)")
        self.memory_extract_every = QSpinBox()
        self.memory_extract_every.setRange(0, 100)
        self.memory_extract_every.setSuffix(" 轮(0 = 不自动抽取)")
        self.memory_inject_items = QSpinBox()
        self.memory_inject_items.setRange(0, 30)
        self.memory_inject_items.setSuffix(" 条")
        self.memory_inject_chars = QSpinBox()
        self.memory_inject_chars.setRange(200, 4000)
        self.memory_inject_chars.setSuffix(" 字")
        self.pet_name = QLineEdit()
        self.pet_name.setPlaceholderText("它的名字")
        self.user_title = QLineEdit()
        self.user_title.setPlaceholderText("它怎么称呼你(如:主人 / 你 / 名字)")

        form.addRow("", self.memory_enabled)
        form.addRow("自动抽取频率", self.memory_extract_every)
        form.addRow("每次注入", self.memory_inject_items)
        form.addRow("注入上限", self.memory_inject_chars)
        form.addRow("它的名字", self.pet_name)
        form.addRow("怎么称呼你", self.user_title)
        layout.addLayout(form)

        self.memory_stats = QLabel("")
        self.memory_stats.setWordWrap(True)
        layout.addWidget(self.memory_stats)

        tip = QLabel("教它记忆:直接对它说「记住:我喜欢喝美式」。"
                     "记忆与档案只存本机;自动抽取时会把最近对话发给所配置的模型服务。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #888;")
        layout.addWidget(tip)

        self.memory_list = QListWidget()
        self.memory_list.setMinimumHeight(160)
        layout.addWidget(self.memory_list)

        buttons = QHBoxLayout()
        for text, slot in (("删除选中", self._on_delete_memory),
                           ("清空全部", self._on_clear_memories),
                           ("导出档案…", self._on_export_memories),
                           ("导入档案…", self._on_import_memories)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.memory_feedback = QLabel("")
        self.memory_feedback.setWordWrap(True)
        layout.addWidget(self.memory_feedback)
        return box

    def _refresh_memory_page(self) -> None:
        """把档案信息与记忆列表刷成当前值。"""
        available = self.owner is not None and hasattr(self.owner, "memories")
        for widget in (self.memory_list, self.memory_stats):
            widget.setEnabled(available)
        if not available:
            self.memory_stats.setText("(没连上桌宠,记忆管理不可用)")
            return

        profile = self.owner.profile
        store = self.owner.memories
        self.memory_stats.setText(
            f"📅 相处 {profile.days_together} 天 · 💬 对话 {profile.chat_count} 句 · "
            f"💗 亲密度 {profile.affinity}({profile.level}) · 🧠 记忆 {len(store)} 条"
            + (f" · 🏅 {'、'.join(profile.milestones[-3:])}" if profile.milestones else "")
        )
        self.memory_list.clear()
        for memory in reversed(store.all()):        # 新的在上面
            source = "手动" if memory.source == "manual" else "自动"
            item = QListWidgetItem(
                f"[{memory.kind_label}·{source}·{memory.importance}] {memory.text}"
            )
            item.setData(Qt.ItemDataRole.UserRole, memory.id)
            item.setToolTip(f"{memory.created_at}\n记忆 id: {memory.id}")
            self.memory_list.addItem(item)

    def _memory_store(self):
        return getattr(self.owner, "memories", None) if self.owner is not None else None

    def _on_delete_memory(self) -> None:
        store = self._memory_store()
        item = self.memory_list.currentItem()
        if store is None or item is None:
            self.memory_feedback.setText("先在上面选中一条记忆")
            return
        memory_id = item.data(Qt.ItemDataRole.UserRole)
        if store.remove(str(memory_id)):
            store.save()
            self.memory_feedback.setText("已删除这条记忆")
            self._refresh_memory_page()

    def _on_clear_memories(self) -> None:
        store = self._memory_store()
        if store is None:
            return
        answer = QMessageBox.question(self, "清空记忆",
                                      f"确定要清空全部 {len(store)} 条记忆吗?此操作不可撤销。")
        if answer != QMessageBox.StandardButton.Yes:
            return
        store.clear()
        store.save()
        self.memory_feedback.setText("记忆已清空(档案里的相处天数/亲密度保留)")
        self._refresh_memory_page()

    def export_memories_to(self, path: str) -> str:
        """导出养成档案到指定文件(弹窗与测试共用这条路径)。"""
        if self.owner is None:
            return "没连上桌宠,无法导出"
        from .memory import export_bundle

        export_bundle(self.owner.profile, self.owner.memories.all(), Path(path))
        return f"已导出到 {path}"

    def import_memories_from(self, path: str) -> str:
        """从文件导入养成档案(覆盖当前档案与记忆)。"""
        if self.owner is None:
            return "没连上桌宠,无法导入"
        from .memory import import_bundle

        try:
            profile, memories = import_bundle(Path(path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return f"导入失败:{exc}"
        self.owner.profile.__dict__.update(profile.__dict__)
        self.owner.memories.clear()
        for memory in memories:
            self.owner.memories.add(memory)
        self.owner._save_profile()
        self.owner.memories.save()
        self._refresh_memory_page()
        return f"已导入 {len(memories)} 条记忆(相处天数 {profile.days_together} 天)"

    def _on_export_memories(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出养成档案", "deep-whale-profile.json",
                                              "JSON 文件 (*.json)")
        if path:
            self.memory_feedback.setText(self.export_memories_to(path))

    def _on_import_memories(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入养成档案", "",
                                              "JSON 文件 (*.json)")
        if path:
            self.memory_feedback.setText(self.import_memories_from(path))

    # ---------------------------------------------------------------- 取值

    def _detect_provider(self) -> str:
        """按当前地址/模型猜是哪个预设,猜不出就是自定义。"""
        url = self.chat_base_url.text().strip().rstrip("/")
        model = self.chat_model.text().strip()
        for key, (preset_url, preset_model) in PROVIDER_PRESETS.items():
            if url == preset_url.rstrip("/") and model == preset_model:
                return key
        return "custom"

    def _on_provider_changed(self) -> None:
        """选中预设时一键填好接口地址与模型名(仍可手动改)。"""
        key = self.provider.currentData()
        if key in PROVIDER_PRESETS:
            url, model = PROVIDER_PRESETS[key]
            self.chat_base_url.setText(url)
            self.chat_model.setText(model)
            if key == "ollama":
                self.chat_api_key.clear()
                self.chat_api_key.setPlaceholderText("本机服务无需 Key")
            else:
                self.chat_api_key.setPlaceholderText("留空则用环境变量或 DSH 凭据")

    def _load_values(self) -> None:
        """把配置回填到面板。

        ⚠️ 面板只回填**可改**的项。像窗口高度/缩放/帧率、视线跟随调参、待机节奏这些
        已经不进面板了,这里也**不要**再去碰对应控件(以前是有的,删控件时必须一起清)。
        """
        cfg = self.cfg
        self.opacity.setValue(cfg.opacity)
        self.always_on_top.setChecked(cfg.always_on_top)

        self.click_through.setChecked(cfg.click_through)
        self.gaze_follow.setChecked(cfg.gaze_follow)
        self.idle_motion.setChecked(cfg.idle_motion)
        self.poke_reaction.setChecked(cfg.poke_reaction)

        self.chat_enabled.setChecked(cfg.chat_enabled)
        self.chat_base_url.setText(cfg.chat_base_url)
        self.chat_model.setText(cfg.chat_model)
        self.chat_api_key.setText(cfg.chat_api_key)
        self.chat_api_key_env.setText(cfg.chat_api_key_env)
        self.chat_use_dsh_credentials.setChecked(cfg.chat_use_dsh_credentials)
        #: ⚠️ 这里**只能回填已存在的那个复选框**。以前这一行是
        #: ``self.chat_hover = QCheckBox(...)`` —— 又新建了一个**没进任何布局**的控件,
        #: 于是用户在「对话」页看到的那个(建在 ``_build_ui`` 里、已 addRow 的)
        #: 状态永远不更新,而保存时读的是这个影子控件:勾了没用、显示也不对。
        self.chat_hover.setChecked(cfg.chat_hover)
        self.chat_history.setValue(cfg.chat_history)
        self.chat_timeout.setValue(cfg.chat_timeout)
        self.chat_bubble_seconds.setValue(cfg.chat_bubble_seconds)
        self.chat_persona.setPlainText(cfg.chat_persona)

        self.chat_model_actions.setChecked(cfg.chat_model_actions)
        self.chat_use_tools.setChecked(cfg.chat_use_tools)
        self.idle_autonomy.setChecked(cfg.idle_autonomy)

        self.idle_llm_thoughts.setChecked(cfg.idle_llm_thoughts)

        self.idle_thought_bubble.setChecked(cfg.idle_thought_bubble)

        self.memory_enabled.setChecked(cfg.memory_enabled)
        self.memory_extract_every.setValue(cfg.memory_extract_every)
        self.memory_inject_items.setValue(cfg.memory_inject_items)
        self.memory_inject_chars.setValue(cfg.memory_inject_chars)
        if self.owner is not None and hasattr(self.owner, "profile"):
            self.pet_name.setText(self.owner.profile.name)
            self.user_title.setText(self.owner.profile.user_title)

        # 服务商下拉按当前配置回填(猜不出来就是"自定义")
        index = self.provider.findData(self._detect_provider())
        self.provider.blockSignals(True)
        self.provider.setCurrentIndex(max(0, index))
        self.provider.blockSignals(False)

        self.hotkey_toggle_visible.setText(cfg.hotkey_toggle_visible)
        self.hotkey_open_chat.setText(cfg.hotkey_open_chat)
        self.autostart.setChecked(cfg.autostart)

    def _collect(self) -> None:
        """把控件里的值写回 cfg(不落盘)。

        ⚠️ 只写**面板上真的能改**的项:布局尺寸/缩放/帧率、视线跟随调参、待机节奏
        已经从面板移除,这里就**不要**再去覆盖它们 —— 否则一按保存就会把用户
        (或程序)手调过的值重置成默认值。
        """
        cfg = self.cfg
        cfg.opacity = self.opacity.value()
        cfg.always_on_top = self.always_on_top.isChecked()

        cfg.click_through = self.click_through.isChecked()
        cfg.gaze_follow = self.gaze_follow.isChecked()
        cfg.idle_motion = self.idle_motion.isChecked()
        cfg.poke_reaction = self.poke_reaction.isChecked()

        cfg.chat_enabled = self.chat_enabled.isChecked()
        cfg.chat_hover = self.chat_hover.isChecked()
        cfg.chat_base_url = self.chat_base_url.text().strip()
        cfg.chat_model = self.chat_model.text().strip()
        cfg.chat_api_key = self.chat_api_key.text().strip()
        cfg.chat_api_key_env = self.chat_api_key_env.text().strip()
        cfg.chat_use_dsh_credentials = self.chat_use_dsh_credentials.isChecked()
        cfg.chat_history = self.chat_history.value()
        cfg.chat_timeout = self.chat_timeout.value()
        cfg.chat_bubble_seconds = self.chat_bubble_seconds.value()
        cfg.chat_persona = self.chat_persona.toPlainText().strip()

        cfg.chat_model_actions = self.chat_model_actions.isChecked()
        cfg.chat_use_tools = self.chat_use_tools.isChecked()
        cfg.idle_autonomy = self.idle_autonomy.isChecked()
        cfg.idle_llm_thoughts = self.idle_llm_thoughts.isChecked()
        cfg.idle_thought_bubble = self.idle_thought_bubble.isChecked()

        cfg.memory_enabled = self.memory_enabled.isChecked()
        cfg.memory_extract_every = self.memory_extract_every.value()
        cfg.memory_inject_items = self.memory_inject_items.value()
        cfg.memory_inject_chars = self.memory_inject_chars.value()
        if self.owner is not None and hasattr(self.owner, "profile"):
            self.owner.profile.name = self.pet_name.text().strip() or self.owner.profile.name
            self.owner.profile.user_title = self.user_title.text().strip() or "你"

        cfg.hotkey_toggle_visible = self.hotkey_toggle_visible.text().strip()
        cfg.hotkey_open_chat = self.hotkey_open_chat.text().strip()
        cfg.autostart = self.autostart.isChecked()
        cfg.clamp()

    # ---------------------------------------------------------------- 动作

    def _on_save(self) -> None:
        self._collect()
        #: ⚠️ 写入路径由桌宠窗口提供(``window.config_path``):
        #: 生产环境是项目目录的 config.json,测试/工具里被重定向到 .tmp 沙盒。
        #: 以前这里无条件 ``self.cfg.save()`` 写死真实文件 —— 一个临时脚本调了本方法
        #: 就把测试配置(chat_enabled=false 等)覆盖进用户的 config.json,
        #: 之后所有依赖对话的功能都"莫名不可用"(实测踩过)。
        path = getattr(self.owner, "config_path", None)
        try:
            if path is not None:
                self.cfg.save(path)
            else:
                self.cfg.save()
        except OSError as exc:
            self.test_result.setText(f"写入配置失败:{exc}")
            return
        self.applied.emit()
        self.accept()

    def _on_reset_defaults(self) -> None:
        defaults = Config()
        self.cfg.__dict__.update(defaults.__dict__)
        self._load_values()
        self.test_result.setText("已载入默认值(还需点「保存并生效」才会写入)")

    def _client_from_form(self) -> ChatClient:
        self._collect()
        return ChatClient(ChatSettings(
            base_url=self.cfg.chat_base_url,
            model=self.cfg.chat_model,
            api_key=self.cfg.chat_api_key,
            api_key_env=self.cfg.chat_api_key_env,
            use_dsh_credentials=self.cfg.chat_use_dsh_credentials,
            persona=self.cfg.chat_persona,
            history_turns=self.cfg.chat_history,
            timeout=self.cfg.chat_timeout,
        ))

    def _on_test_chat(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        client = self._client_from_form()
        self.test_button.setEnabled(False)
        self.test_result.setText(f"正在测试(Key 来源:{client.describe_key_source()})…")

        worker = _ProbeWorker(client, self)
        worker.done.connect(self._on_test_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_test_done(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_result.setText(("✅ " if ok else "❌ ") + message[:200])
