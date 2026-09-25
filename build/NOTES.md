# 打包状态与踩坑记录

> PyInstaller 打包的现状、根因与验证方式。**打包已完成并验证通过。**

## 结论(已解决)

打包版 exe 现在完整可用:窗口、OpenGL、模型渲染、44 表情 / 7 动画、快捷键、对话
全部正常,实测 60 FPS。

### 根因:`exe 文件名含非 ASCII 字符`

实测对照(同一个探针 exe,只改放置位置/文件名):

| 情况 | 结果 |
|---|---|
| ASCII 目录 + ASCII 文件名 | ✅ 正常 |
| **ASCII 目录 + 中文文件名** | ❌ 一创建窗口就崩(`0xC0000409` / abort) |
| **中文目录** + ASCII 文件名 | ✅ 正常 |

所以:**PyInstaller 冻结的 PySide6 应用,只要 exe 文件名含中文就会崩;目录名含中文没事。**

- 现象:进程能起来、`QApplication` 能建、日志停在第一次 `window.show()`;
  连一个**最普通的 `QWidget.show()`** 都崩(所以与 OpenGL / live2d / 窗口参数都无关)。
- faulthandler 报 `Fatal Python error: Aborted`;窗口版退出码 `0xC0000409`,
  控制台版 `3`(SIGABRT);`QT_QPA_PLATFORM=offscreen` 下则完全正常。
- **修法**:`pet.spec` / `installer.iss` 里 exe 名统一用 `DSWhalePet.exe`,
  中文只用在窗口标题、托盘提示和快捷方式名上。

## 验证方式

```powershell
# 一键:打包 + 跑 exe 自检 + (装了 Inno Setup 就)出安装包
pwsh -File build\build.ps1

# 手动
.venv\Scripts\pyinstaller.exe build\pet.spec --noconfirm --distpath dist --workpath build\work
dist\DSWhalePet\DSWhalePet.exe --selftest      # 结果写进 dist\DSWhalePet\pet.log
```

自检日志里应能看到 `实测 FPS=60`、`参数数量=247`、`表情数量=44`、`动画数量=7`,
且不生成 `pet-fatal.log`。

## 排查用到的工具(都留在仓库里)

- `--qt-probe`(内置在 main.py):分步探针 —— 普通窗口 → 空 QOpenGLWidget → 完整桌宠。
- `build/minimal_probe.py` + `build/minimal.spec`:最小复现,可逐个引入
  Qt / PyOpenGL / live2d / 自己的模块,用 `oured` 模式二分定位。
  它也顺手证明了"非 ASCII exe 名"这个结论(把同一个 exe 拷到不同路径跑即可)。
- 环境变量:`PET_CONSOLE=1` 打带控制台的诊断版;`PET_NO_EXCLUDES=1` 打不过滤模块的版本。

## 其它已修好的打包坑

1. ⚠️ **`build.ps1` / `installer.iss` 必须存成「UTF-8 带 BOM」**。Windows PowerShell 5.1
   对**没有 BOM** 的 `.ps1` 会按系统 ANSI(中文区是 GBK)解码:脚本里的中文字符串变成乱码,
   轻则提示文字乱,**重则整个脚本语法报错、根本跑不起来**(实测踩过:一次编辑丢了 BOM,
   解析器直接报 `Unexpected token '}'`)。用 PS 7(pwsh)不受影响,但别人很可能用 5.1。
   `tools/preflight_check.py` 现在会自动检查这一点。
2. **live2d 的 GLSL 着色器必须随包**(`v3/FrameworkShaders/*.frag|*.vert`),
   用 `collect_data_files("live2d")`;漏掉会启动即崩。
3. `live2d.v3.params` 需要显式 hidden import。
4. 打包后配置/日志写 **exe 所在目录**,资源读 `sys._MEIPASS`
   (见 `pet/config.py` 的 `APP_DIR` / `BUNDLE_DIR`);开机自启命令在打包模式下直接指向 exe 自身。
5. 窗口程序没有控制台,所以关键日志与自检结果都落盘(`pet.log` / `pet-fatal.log`)。
6. ⚠️ **缺模型时打包脚本会直接拦住**(要求 `assets\model` 里有 `*.model3.json`)。
   仓库故意不含模型,而分享包是要发给别人的 —— 没有模型的包,别人双击根本跑不起来。
   确实只想验证流程时,用 `$env:PET_ALLOW_NO_MODEL = '1'` 放行。

