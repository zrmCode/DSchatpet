"""DS鲸鱼娘桌宠 —— 模块包。

结构:
    config.py   配置存取(模型路径、窗口位置、行为开关)
    actions.py  从模型的 vtube.json 提取动作/表情目录
    model.py     Live2D 模型封装(加载、表情、动作、视线、口型)
    win32.py     Windows 窗口技巧(透明区域点击穿透、置顶)
    window.py    桌宠主窗口(Qt + OpenGL 渲染 + 交互)
"""

__all__ = ["config", "actions", "model", "win32", "window"]
