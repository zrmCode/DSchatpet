# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置:把桌宠打成单目录的 exe。

用法(在项目根目录)::

    .venv\\Scripts\\pyinstaller.exe build\\pet.spec --noconfirm

要点:
- ``live2d.v3`` 是 C 扩展,PyInstaller 不会自动收集它的子模块(官方 README 也提示
  ``params`` 需要 hidden import),这里显式声明。
- **必须带上 live2d 包里的 GLSL 着色器**(``v3/FrameworkShaders/*.frag|*.vert``):
  渲染器运行时按 ``Init Shader Dir`` 读这些文件,漏掉会导致启动即崩或挂死。
  靠 ``collect_data_files("live2d")`` 收集 —— 这是踩过的坑。
- 模型素材随包分发到 ``assets/model``;运行时通过 ``sys._MEIPASS`` 找到(见 pet/config.py)。
- 窗口程序:``console=False``,所以自检信息同时会写进 exe 同目录的 pet.log。
"""

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 - SPECPATH 由 PyInstaller 注入

#: 设 PET_CONSOLE=1 可打一个带控制台的诊断版(能直接看到 Qt/原生层的报错)
CONSOLE_BUILD = os.environ.get("PET_CONSOLE") == "1"

hiddenimports = [
    "live2d.v3",
    "live2d.v3.params",          # live2d-py 官方 README 点名需要手动声明
    "live2d.utils.lipsync",
]
hiddenimports += collect_submodules("live2d.v3")

# live2d 的原生库(live2dcubismcore 等)
binaries = collect_dynamic_libs("live2d")

datas = collect_data_files("live2d") + [
    (str(ROOT / "assets" / "model"), "assets/model"),
]

a = Analysis(                                    # noqa: F821
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 这些是体积大头;默认排除用不到的 Qt 模块。
    # ⚠️ 但排除 PySide6 子模块可能影响 PySide6 hook 的插件收集,
    #    设 PET_NO_EXCLUDES=1 可打一个不过滤的版本用于对照排查。
    excludes=[] if os.environ.get("PET_NO_EXCLUDES") == "1" else [
        "tkinter", "matplotlib", "scipy", "pandas", "IPython",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
        "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtQuick3D",
        "PySide6.QtMultimediaWidgets",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)                                # noqa: F821

exe = EXE(                                       # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    # ⚠️ exe 文件名必须是纯 ASCII!实测:PyInstaller 打出的 PySide6 应用,若 exe 文件名含中文,
    #    一创建窗口就崩(0xC0000409 / abort);目录名含中文则没事。
    #    中文只用在窗口标题、托盘提示与快捷方式名里。
    name="DSWhalePet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE_BUILD,  # 默认无控制台;诊断时用 PET_CONSOLE=1 打出带控制台的版本
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "model" / "icon.png") if (ROOT / "assets" / "model" / "icon.png").is_file() else None,
)

coll = COLLECT(                                  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DSWhalePet",
)
