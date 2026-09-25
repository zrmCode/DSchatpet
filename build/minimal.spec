# -*- mode: python ; coding: utf-8 -*-
"""最小复现用的打包配置(只打 Qt + 可选 PyOpenGL/live2d,用来隔离崩溃原因)。

    .venv\\Scripts\\pyinstaller.exe build\\minimal.spec --noconfirm --distpath dist-probe --workpath build\\work-probe
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821

datas = collect_data_files("live2d")
binaries = collect_dynamic_libs("live2d")
hiddenimports = ["live2d.v3", "live2d.v3.params"] + collect_submodules("live2d.v3")
#: 让 "oured" 模式能导入桌宠自己的包
hiddenimports += ["pet", "pet.config", "pet.actions", "pet.applog", "pet.win32",
                  "pet.chat", "pet.bubble", "pet.hotkey", "pet.model", "pet.window"]

a = Analysis(                                    # noqa: F821
    [str(ROOT / "build" / "minimal_probe.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)                                # noqa: F821

exe = EXE(                                       # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="minimal_probe",
    debug=False,
    strip=False,
    upx=False,
    console=True,           # 带控制台,方便直接看
    disable_windowed_traceback=False,
)

COLLECT(exe, a.binaries, a.datas, name="minimal_probe")   # noqa: F821
