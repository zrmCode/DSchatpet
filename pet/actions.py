"""从模型的 VTube Studio 配置里提取动作 / 表情目录。

模型作者把这套皮肤做成 VTS 模型分发:44 个表情、7 个动画都通过 VTS 热键绑定
(见模型目录里的「按键表.txt」),权威来源是 ``*.vtube.json``。
这里把它转成桌宠可直接调用的动作目录 —— 不手写清单,模型更新后菜单自动跟着变。

注意:该模型的 ``.model3.json`` 里并没有声明 Motions / Expressions(只有 Moc、
Textures、Physics、DisplayInfo),所以这些动作与表情必须靠 live2d-py 的
``LoadExtraMotion`` / ``LoadExtraExpression`` 在运行时注册。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

KIND_EXPRESSION = "expression"
KIND_MOTION = "motion"
KIND_RESET = "reset"

#: 给模型看的"人话解释"。这个模型里有不少表情的名字看不出是干什么的
#: (挤 / 橡皮 / 画笔 / 巴菲 / 魔爪…),不解释的话模型会乱选或不敢选。
ACTION_HINTS: dict[str, str] = {
    "点菜按下": "按下桌上的点菜铃",
    "撤回": "把刚才的东西收回去",
    "橡皮": "拿橡皮擦东西",
    "画笔": "拿画笔涂鸦",
    "蛋包饭": "桌上出现蛋包饭",
    "MoeMoeQ~": "在蛋包饭上挤番茄酱的可爱动作",
    "冒爱心动画": "周围冒出爱心",
    "喵喵手~喵~动画": "比出猫爪手势",
    "双手比耶": "双手比耶",
    "兔兔贴纸": "脸上贴兔兔贴纸",
    "吐魂": "灵魂出窍(累瘫/无语)",
    "呆呆眼": "眼神呆滞放空",
    "哭": "大哭",
    "圆眼镜": "戴上圆眼镜",
    "墨镜": "戴上墨镜(闭眼)",
    "开心兴奋": "开心兴奋",
    "心跳": "周围冒出心跳",
    "悲伤": "悲伤难过",
    "情绪花花": "周围冒出情绪小花",
    "感叹号": "头上冒感叹号(惊讶)",
    "方眼镜": "戴上方眼镜",
    "星星眼": "眼睛变成星星(崇拜/超兴奋)",
    "晕晕": "晕头转向",
    "椭圆眼镜": "戴上椭圆眼镜",
    "流汗": "冒冷汗(尴尬/为难)",
    "爱心眼": "眼睛变成爱心(喜欢)",
    "猫猫贴纸": "脸上贴猫猫贴纸",
    "生气": "生气鼓腮",
    "脸红": "脸红害羞",
    "蝴蝶结贴纸": "脸上贴蝴蝶结",
    "调皮": "调皮地眨眼吐舌",
    "闭眼口水": "闭眼流口水(睡着/发呆)",
    "问号": "头上冒问号(疑惑)",
    "阴暗": "脸色阴暗(郁闷/黑化)",
    "使巴菲消失": "桌上的芭菲甜点消失",
    "魔爪": "伸出魔爪",
    "魔爪换色": "魔爪换个颜色",
    "头箍": "摘掉头箍",
    "吐舌": "吐舌头(略~)",
    "手机换色": "给手机换个颜色",
    "深色桌布": "把桌布换成深色",
    "鲸鱼": "小鲸鱼冒出来",
    "单边马尾": "换成单边马尾发型",
    "鲸鱼放桌上": "把小鲸鱼放到桌面上",
}

MOTION_HINTS: dict[str, str] = {
    "挤番茄酱动画": "在蛋包饭上挤番茄酱",
    "自拍手机": "拿出手机准备自拍",
    "快速自拍": "快速自拍一张",
    "自拍动画": "举起手机自拍",
    "重锤出击": "抡起锤子敲一下",
    "泡泡糖": "吹泡泡糖",
    "鲸鱼喷水": "被鲸鱼喷水(会打断其他动画)",
}


def describe_catalog(expressions: list[str], motions: list[str]) -> str:
    """把可用表情/动作渲染成给大模型的清单(带人话解释)。"""
    def render(names: list[str], hints: dict[str, str]) -> str:
        parts = []
        for name in names:
            hint = hints.get(name)
            parts.append(f"{name}({hint})" if hint else name)
        return "、".join(parts)

    lines = []
    if expressions:
        lines.append(f"【可用表情】{render(expressions, ACTION_HINTS)}")
    if motions:
        lines.append(f"【可用动作(会打断当前动作,别频繁用)】{render(motions, MOTION_HINTS)}")
    return "\n".join(lines)


@dataclass(frozen=True)
class Action:
    """一条可执行的动作。"""

    kind: str                  # expression / motion / reset
    name: str                  # 显示名(模型作者起的中文名)
    file: Path | None          # 表情 / 动画文件;reset 为 None
    raw_action: str            # vtube.json 里的原始 Action 字段

    @property
    def is_expression(self) -> bool:
        return self.kind == KIND_EXPRESSION

    @property
    def is_motion(self) -> bool:
        return self.kind == KIND_MOTION


def _resolve(model_dir: Path, file_name: str) -> Path | None:
    """动作文件可能在模型根目录,也可能在 motions/ 子目录。"""
    for cand in (model_dir / file_name, model_dir / "motions" / file_name):
        if cand.is_file():
            return cand
    return None


def load_actions(model_dir: Path) -> list[Action]:
    """读取模型目录下所有 *.vtube.json,返回按热键定义顺序排列的动作列表。"""
    actions: list[Action] = []
    seen: set[str] = set()

    for vtube in sorted(model_dir.glob("*.vtube.json")):
        try:
            data = json.loads(vtube.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        # Hotkeys 在 vtube.json 里是数组(旧版本可能是对象),两种都兼容
        hotkeys = data.get("Hotkeys") or []
        entries = hotkeys.values() if isinstance(hotkeys, dict) else hotkeys

        for hotkey in entries:
            if not isinstance(hotkey, dict):
                continue
            raw = str(hotkey.get("Action") or "")
            name = str(hotkey.get("Name") or "").strip()
            file_name = str(hotkey.get("File") or "").strip()

            if raw in ("ToggleExpression", "TriggerAnimation") and file_name:
                path = _resolve(model_dir, file_name)
                if path is None:
                    continue
                kind = KIND_EXPRESSION if raw == "ToggleExpression" else KIND_MOTION
                key = f"{kind}:{path.name}"
                if key in seen:
                    continue
                seen.add(key)
                actions.append(Action(kind, name or path.stem, path, raw))

            elif raw == "RemoveAllExpressions":
                key = "reset"
                if key not in seen:
                    seen.add(key)
                    actions.append(Action(KIND_RESET, name or "归位", None, raw))

    return actions
