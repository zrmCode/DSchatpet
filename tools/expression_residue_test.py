"""表情切换后的"永久残留"检测 —— 直接读参数值,比像素对比更可靠。

背景(用户反馈):切换表情后,眼睛区域出现**永久残影**。

机制推测:这个模型每个表情只把某个"美术开关参数"(如 ``ParamCheek16``)设成 1.0;
切到别的表情时若该参数没被恢复成默认值,那一层美术就会一直显示 —— 就是残影。

⚠️ 之前用像素对比测过"是否有残留",结论是"没有",但那个设计有缺陷:
   它比较「先 A 后 B」与「归位后 B」,若两种情况下 A 的残留都存在,像素自然一样,
   于是漏判。**参数值不会骗人**,所以这里直接读全部参数。

流程:对每个表情 E
    1. 归位 -> 记录 baseline
    2. 施加 E -> 快照 A(确认 E 确实改了参数)
    3. 施加中性表情 F(「撤回」,只动 chehui)-> 快照 B
    4. 残留 = (A ≠ baseline) 且 (B 仍 ≠ baseline) 的参数
最后再做一次归位,检查是否全部回到 baseline(用于判断"永久"到底是不是真的)。

用法::

    .venv\\Scripts\\python.exe tools\\expression_residue_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live2d.v3 as live2d
from PySide6.QtCore import QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication

from pet import config as config_mod
from pet.actions import KIND_EXPRESSION, load_actions

NEUTRAL = "撤回"        # 中性中转表情(只动 chehui 一个参数)
TOL = 0.01             # 判定阈值(浮点噪声在 1e-3 量级,这里放宽到 0.01)
#: 每步等待时间:表情淡入淡出约 1 秒,采样太短会把"淡入中"误判成残留
STEP_MS = 1200
RESET_WAIT_MS = 3000

S = {
    "model": None, "ids": [], "defaults": [], "actions": [], "queue": [],
    "step": 0, "baseline": [], "snap_a": [], "results": [], "after_reset": None,
    "physics_outputs": set(),
}


def load_physics_outputs(model_dir: Path) -> set[str]:
    """物理仿真驱动的参数(physics3.json 的 Output)。

    这些参数是**仿真结果**,本来就长期偏离默认值(头发/尾巴/翅膀在晃),
    归位也不会回到 0 —— 必须排除,否则会把正常物理当"残留"误报(踩过这个坑)。
    """
    outputs: set[str] = set()
    for physics in model_dir.glob("*.physics3.json"):
        try:
            data = json.loads(physics.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for setting in data.get("PhysicsSettings", []):
            for entry in setting.get("Output", []):
                param_id = (entry.get("Destination") or {}).get("Id")
                if param_id:
                    outputs.add(param_id)
    return outputs


class Probe(QOpenGLWidget):
    def initializeGL(self) -> None:
        live2d.glInit()
        model = live2d.LAppModel()
        model.LoadModelJson(str(S["model_json"]), maskBufferCount=2)
        model.SetAutoBlinkEnable(False)
        model.SetAutoBreathEnable(False)
        ids, defaults = [], []
        for index in range(model.GetParameterCount()):
            param = model.GetParameter(index)
            ids.append(param.id)
            defaults.append(float(param.default))
        S["ids"], S["defaults"] = ids, defaults
        for action in S["actions"]:
            model.LoadExtraExpression(action.name, str(action.file))
        S["model"] = model

        # 必须持续 Update(),表情的参数值才会随淡入生效(没有渲染循环的话读到的永远是旧值)
        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self.update)
        self._render_timer.start(16)

    def paintGL(self) -> None:
        live2d.clearBuffer()
        S["model"].Update()
        S["model"].Draw()


def snapshot() -> list[float]:
    model = S["model"]
    return [float(model.GetParameterValue(i)) for i in range(len(S["ids"]))]


def changed_ids(values: list[float], reference: list[float]) -> list[int]:
    """忽略物理输出参数:它们由仿真驱动,长期偏离默认值属于正常现象。"""
    outputs = S["physics_outputs"]
    return [i for i, value in enumerate(values)
            if abs(value - reference[i]) > TOL and S["ids"][i] not in outputs]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("找不到模型目录")
        return 2
    S["model_json"] = config_mod.find_model_json(model_dir)
    S["physics_outputs"] = load_physics_outputs(model_dir)
    all_actions = load_actions(model_dir)
    S["actions"] = [a for a in all_actions if a.kind == KIND_EXPRESSION]
    names = [a.name for a in S["actions"]]
    if NEUTRAL not in names:
        print(f"模型里没有中性表情「{NEUTRAL}」,无法测试")
        return 2
    S["queue"] = [n for n in names if n != NEUTRAL]
    print(f"待测表情 {len(S['queue'])} 个(中性中转:{NEUTRAL})")
    print(f"已排除 {len(S['physics_outputs'])} 个物理输出参数(仿真驱动,不参与残留判定)")

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    live2d.init()
    app = QApplication(sys.argv[:1])
    widget = Probe()
    widget.resize(240, 240)
    widget.show()

    def finish() -> None:
        S["model"].ResetExpressions()

        def check_reset() -> None:
            after = snapshot()
            # 只关心"明显偏离默认值"的参数,忽略 -0.000 这类浮点噪声
            S["after_reset"] = [i for i in changed_ids(after, S["baseline"])
                                if abs(after[i] - S["defaults"][i]) > TOL]
            report()

        QTimer.singleShot(RESET_WAIT_MS, check_reset)

    def report() -> None:
        ids = S["ids"]
        print("\n=== 结果 ===")
        bad = [r for r in S["results"] if r[2]]
        if not bad:
            print("  [OK]   没有任何表情在切走后留下参数残留")
        else:
            print(f"  [BAD]  {len(bad)} 个表情切走后参数没被恢复(残影来源):")
            for name, changed, residual, detail in bad:
                print(f"\n  【{name}】改动 {len(changed)} 个参数,残留 {len(residual)} 个:")
                for param_id, after_e, after_neutral, default in detail:
                    print(f"      {param_id:<30} 施加后={after_e:>8.3f} "
                          f"切走后={after_neutral:>8.3f} 默认={default:>8.3f}")

        leftover = S["after_reset"]
        print("\n=== 归位后仍未回到默认的参数 ===")
        if not leftover:
            print("  [OK]   归位后全部回到默认 —— 可以用归位清掉残影")
        else:
            now = snapshot()
            print(f"  [BAD]  仍有 {len(leftover)} 个参数没回去(真·永久残留):")
            for i in leftover[:25]:
                print(f"      {ids[i]:<30} 当前={now[i]:>8.3f} 默认={S['defaults'][i]:>8.3f}")

        app.quit()

    def tick() -> None:
        model = S["model"]
        if model is None:
            return
        step = S["step"]
        S["step"] += 1

        if step == 0:
            model.ResetExpressions()
            return
        if step == 1:
            S["baseline"] = snapshot()
            print("baseline 已记录(归位后的全部参数值)\n")
            return

        index, sub = divmod(step - 2, 4)
        if index >= len(S["queue"]):
            finish()
            return
        name = S["queue"][index]

        if sub == 0:
            model.ResetExpressions()
        elif sub == 1:
            model.SetExpression(name)
        elif sub == 2:
            S["snap_a"] = snapshot()
            model.SetExpression(NEUTRAL)
        else:
            snap_b = snapshot()
            changed = changed_ids(S["snap_a"], S["baseline"])
            if changed:
                # 残留 = 切走后仍明显偏离默认值的参数(才可能是残影的成因)
                residual = [i for i in changed
                            if abs(snap_b[i] - S["baseline"][i]) > TOL
                            and abs(snap_b[i] - S["defaults"][i]) > TOL]
                detail = [(S["ids"][i], S["snap_a"][i], snap_b[i], S["defaults"][i])
                          for i in residual]
                S["results"].append((name, changed, residual, detail))
                mark = "有残留" if residual else "干净"
                print(f"  {name:<14} 改动 {len(changed):>2} 个参数 → 切走后残留 "
                      f"{len(residual):>2} 个  [{mark}]")

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(STEP_MS)
    QTimer.singleShot(600_000, app.quit)

    app.exec()
    widget.close()
    live2d.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
