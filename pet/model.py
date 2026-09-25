"""Live2D 模型封装。

对外只暴露语义化操作:设置/切换表情、播放动画、驱动视线与口型。
所有 live2d-py 的细节(参数区间、运行时注册动作)都收在这里,
窗口层不需要知道 Cubism 的 API 长什么样。

调用约定:``load()`` 必须在有效的 OpenGL 上下文里执行(即 ``QOpenGLWidget.initializeGL``)。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import live2d.v3 as live2d

from .actions import KIND_EXPRESSION, KIND_MOTION, KIND_RESET, Action


@dataclass
class ParamRange:
    """参数的合法区间(从模型自身读出来,不写死)。"""

    minimum: float
    maximum: float

    def map_normalized(self, normalized: float) -> float:
        """把 [-1, 1] 映射到参数区间并夹紧。"""
        normalized = max(-1.0, min(1.0, normalized))
        if normalized < 0:
            return self.minimum * -normalized
        return self.maximum * normalized


class PetModel:
    """一只会眨眼、会呼吸、会看鼠标的鲸鱼娘。"""

    GROUP_ACTION = "Action"     # 7 个手动动画
    GROUP_IDLE = "Idle"         # 待机循环动画

    #: 驱动视线的参数(按 Cubism 通用约定:正值 = 观众视角的右 / 上)
    GAZE_PARAMS = (
        ("ParamAngleX", 1.0),
        ("ParamAngleY", 1.0),
        ("ParamAngleZ", 0.18),
        ("ParamEyeBallX", 1.0),
        ("ParamEyeBallY", 1.0),
        ("ParamBodyAngleX", 0.45),
        ("ParamBodyAngleY", 0.25),
    )

    #: 吃 Y 轴输入的参数(其余吃 X 轴)
    _Y_AXIS_PARAMS = frozenset({"ParamAngleY", "ParamEyeBallY", "ParamBodyAngleY"})

    def __init__(
        self,
        model_dir: Path,
        model_json: Path,
        actions: list[Action],
        mask_buffer_count: int = 2,
    ) -> None:
        self.model_dir = model_dir
        self.model_json = model_json
        self.actions = actions
        #: 裁剪遮罩缓冲数量。复杂模型(眼睛/头发多层裁剪)不足时会出现绘制错位
        self.mask_buffer_count = mask_buffer_count

        self.model: live2d.LAppModel | None = None
        self._ranges: dict[str, ParamRange] = {}
        self._expressions: list[str] = []
        self._motions: dict[str, int] = {}      # 显示名 -> 组内序号
        self._group_counts: dict[str, int] = {}  # 组名 -> 已加载动作数
        self._current_expression: str | None = None
        self._idle_finished = False             # 由回调线程置位,渲染线程消费
        self._has_idle = False                  # 是否成功注册了待机动画
        self._gaze = (0.0, 0.0)                 # 平滑后的视线 [-1,1]

    # ------------------------------------------------------------ 加载

    def load(self, mask_buffer_count: int | None = None) -> None:
        """加载模型并注册全部表情 / 动作。必须在 GL 上下文里调用。

        重复调用会重建模型(诊断时改遮罩缓冲数需要这样做),注册表会清空重来。
        """
        if mask_buffer_count is not None:
            self.mask_buffer_count = int(mask_buffer_count)

        self._ranges.clear()
        self._expressions.clear()
        self._motions.clear()
        self._group_counts.clear()
        self._current_expression = None
        self._has_idle = False
        self._idle_finished = False
        self._gaze = (0.0, 0.0)

        self.model = live2d.LAppModel()
        self.model.LoadModelJson(str(self.model_json), maskBufferCount=self.mask_buffer_count)

        # 模型自带 EyeBlink 参数组,开箱即用的眨眼与呼吸
        self.model.SetAutoBlinkEnable(True)
        self.model.SetAutoBreathEnable(True)

        self._collect_ranges()
        self._register_actions()
        self._register_idle()

    def _collect_ranges(self) -> None:
        """一次性读出所有参数的区间,后面映射视线时直接查表。"""
        assert self.model is not None
        for index in range(self.model.GetParameterCount()):
            param = self.model.GetParameter(index)
            if param.id:
                self._ranges[param.id] = ParamRange(float(param.min), float(param.max))

    def _register_actions(self) -> None:
        """把 44 个表情 + 7 个动画注册进运行时(模型的 model3.json 没有声明它们)。"""
        assert self.model is not None
        for action in self.actions:
            if action.kind == KIND_EXPRESSION and action.file:
                self.model.LoadExtraExpression(action.name, str(action.file))
                self._expressions.append(action.name)
        self._register_motions()

    def _register_motions(self) -> None:
        """注册 7 个手动动画。

        注意 ``LoadExtraMotion`` 的返回值是"本组已加载数量"而不是新动作的索引
        (见 live2d-py 的 ``_v3cpp.pyi`` 文档),所以索引由我们自己按组计数维护,
        前提是额外加载的动作会追加到组末尾 —— 这一点由 ``tools/probe.py`` 实测确认。
        """
        assert self.model is not None
        for action in self.actions:
            if action.kind == KIND_MOTION and action.file:
                self.model.LoadExtraMotion(self.GROUP_ACTION, str(action.file))
                index = self._group_counts.get(self.GROUP_ACTION, 0)
                self._group_counts[self.GROUP_ACTION] = index + 1
                self._motions[action.name] = index

    def _register_idle(self) -> None:
        """待机动画单独放一组并循环播放(该文件 Meta.Loop = true)。"""
        assert self.model is not None
        idle = self.model_dir / "motions" / "idle.motion3.json"
        if not idle.is_file():
            return
        self.model.LoadExtraMotion(self.GROUP_IDLE, str(idle))
        self._has_idle = True
        self.model.StartMotion(
            self.GROUP_IDLE, 0, live2d.MotionPriority.IDLE,
            onFinishMotionHandler=self._on_idle_finish,
        )

    def ensure_idle(self) -> bool:
        """自愈:没有动画在播时把待机动画接上。

        手动动画(FORCE)会打断待机;被打断时待机的结束回调**不会**触发,
        所以只靠回调会让桌宠永久静止 —— 这里按 ``IsMotionFinished()`` 兜底
        (语义:True = 当前没有动画在播)。
        """
        if self.model is None or not self._has_idle:
            return False
        try:
            if not self.model.IsMotionFinished():
                return False
        except Exception:
            return False
        self.model.StartMotion(self.GROUP_IDLE, 0, live2d.MotionPriority.IDLE)
        return True

    def _on_idle_finish(self, group: str = "", no: int = 0) -> None:
        """动作播完的回调(带 group/no 两个参数,可能来自原生线程),只置标志。"""
        self._idle_finished = True

    # ------------------------------------------------------------ 每帧

    def update(self) -> None:
        assert self.model is not None
        self.model.Update()

    def draw(self) -> None:
        assert self.model is not None
        self.model.Draw()

    def consume_idle_finished(self) -> bool:
        if self._idle_finished:
            self._idle_finished = False
            return True
        return False

    def restart_idle(self) -> None:
        assert self.model is not None
        self.model.StartMotion(self.GROUP_IDLE, 0, live2d.MotionPriority.IDLE)

    def stop_all_motions(self) -> None:
        """停掉所有动画(诊断/截图时用来把模型"冻住")。"""
        if self.model is not None:
            self.model.StopAllMotions()

    def freeze_automatics(self, freeze: bool = True) -> None:
        """关掉自动眨眼与呼吸,便于做像素级对比实验。"""
        if self.model is None:
            return
        self.model.SetAutoBlinkEnable(not freeze)
        self.model.SetAutoBreathEnable(not freeze)

    # ------------------------------------------------------------ 视线

    def set_gaze(self, nx: float, ny: float) -> None:
        """设置视线目标,``nx``/``ny`` 为 [-1, 1](右 / 上为正)。"""
        self._gaze = (max(-1.0, min(1.0, nx)), max(-1.0, min(1.0, ny)))

    def smooth_gaze(self, target_x: float, target_y: float, factor: float) -> None:
        """指数平滑地靠近目标,避免眼睛瞬移。"""
        cur_x, cur_y = self._gaze
        self.set_gaze(
            cur_x + (target_x - cur_x) * factor,
            cur_y + (target_y - cur_y) * factor,
        )

    def apply_gaze(self, strength: float = 1.0) -> None:
        """把当前视线写进模型参数。应在 ``update()`` 之前调用。"""
        assert self.model is not None
        nx, ny = self._gaze
        for param_id, weight in self.GAZE_PARAMS:
            rng = self._ranges.get(param_id)
            if rng is None:
                continue
            # Y 轴参数吃 ny,X 轴参数吃 nx;weight 决定这个部位跟多少
            axis = ny if param_id in self._Y_AXIS_PARAMS else nx
            scaled = max(-1.0, min(1.0, axis * weight * strength))
            self.model.SetParameterValue(param_id, rng.map_normalized(scaled), 1.0)

    # ------------------------------------------------------------ 表情

    @property
    def expressions(self) -> list[str]:
        return list(self._expressions)

    @property
    def motion_names(self) -> list[str]:
        return list(self._motions)

    @property
    def current_expression(self) -> str | None:
        return self._current_expression

    def set_expression(self, name: str) -> bool:
        """切换表情(同名重复调用无效)。"""
        if self.model is None or name not in self._expressions:
            return False
        if self._current_expression == name:
            return True
        self.model.SetExpression(name)
        self._current_expression = name
        return True

    def toggle_expression(self, name: str) -> bool:
        """VTS 的 ToggleExpression 语义:再点一次取消(回默认表情)。"""
        if self._current_expression == name:
            self.reset_expressions()
            return True
        return self.set_expression(name)

    def reset_expressions(self) -> None:
        if self.model is None:
            return
        self.model.ResetExpressions()
        self._current_expression = None

    def random_expression(self) -> str | None:
        if not self._expressions:
            return None
        name = random.choice(self._expressions)
        self.set_expression(name)
        return name

    # ------------------------------------------------------------ 动画

    def play_motion(self, name: str) -> bool:
        """播放一个手动动画(会打断当前动画,优先级 FORCE)。"""
        if self.model is None or name not in self._motions:
            return False
        self.model.StartMotion(
            self.GROUP_ACTION, self._motions[name], live2d.MotionPriority.FORCE,
        )
        return True

    def random_motion(self) -> str | None:
        if not self._motions:
            return None
        name = random.choice(list(self._motions))
        self.play_motion(name)
        return name

    # ------------------------------------------------------------ 口型(阶段三预留)

    def set_mouth(self, value: float) -> None:
        """``value`` 为 0~1 的开口度,供语音口型同步调用。"""
        if self.model is None:
            return
        rng = self._ranges.get("ParamMouthOpenY")
        if rng is None:
            return
        self.model.SetParameterValue(
            "ParamMouthOpenY", rng.map_normalized(max(0.0, min(1.0, value))), 1.0,
        )

    # ------------------------------------------------------------ 诊断

    def canvas_size(self) -> tuple[float, float]:
        if self.model is None:
            return (1.0, 1.0)
        size = self.model.GetCanvasSize()
        try:
            return (float(size[0]), float(size[1]))
        except (TypeError, IndexError):
            return (1.0, 1.0)

    def stats(self) -> dict:
        return {
            "模型文件": self.model_json.name,
            "参数数量": len(self._ranges),
            "表情数量": len(self._expressions),
            "动画数量": len(self._motions),
            "画布尺寸": self.canvas_size(),
        }

    def set_scale(self, scale: float) -> None:
        if self.model is not None:
            self.model.SetScale(scale)
