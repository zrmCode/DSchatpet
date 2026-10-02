"""DS鲸鱼娘桌宠 —— 模块包。

结构:
    config.py   配置存取(模型路径、窗口位置、行为开关)
    actions.py  从模型的 vtube.json 提取动作/表情目录
    model.py     Live2D 模型封装(加载、表情、动作、视线、口型)
    win32.py     Windows 窗口技巧(透明区域点击穿透、置顶)
    window.py    桌宠主窗口(Qt + OpenGL 渲染 + 交互)
"""

__all__ = ["config", "actions", "model", "win32", "window"]

#: 版本号(唯一来源)。发 Release 时 tag 用 ``v`` + 这个值(如 v0.1.0);
#: build/installer.iss 的 AppVersion 也跟它保持一致。
#: 改动这里同时改 installer.iss —— tools/preflight_check.py 会核对两者是否一致。
__version__ = "0.2.0"
