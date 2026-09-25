# 第三方组件与许可声明

本项目**自己的代码**以 [GPL-3.0-only](LICENSE) 发布(`LICENSE` 里是 GPLv3 全文)。

下面这些是运行时依赖或随发布包一起分发的东西,**它们各有各的许可**,不受本项目的 GPL 覆盖,
也不因为放进本仓库或压缩包就被重新授权。分发打包版(免安装 zip / 安装包)时请保留本文件
以及 `build/share/` 下的许可文本。

---

## 1. Live2D 模型「DS鲸鱼娘」

- **版权与条款归模型作者所有**,本项目只是使用它。
- 作者: **B站 @氵六青(UID 11272072)**,交流群 645169617
- 原始条款(完整原文见 `assets/许可/模型使用须知.txt`):

  ```
  商用直播√
  自印物料√
  禁止任何形式的盗用以及出售,此模型为无偿分享
  ```

- ⚠️ **模型文件不包含在本仓库内**(见 `assets/README.md`),只随作者许可的
  无偿分享渠道 —— 也就是本项目 GitHub Release 里的免安装压缩包 —— 一起提供。
- ⚠️ **任何情况下都不得出售本程序或其压缩包**(包括"付费下载""打包进付费商品"),
  这与模型作者"禁止出售"的条款冲突。
- 二次创作、改模、商用直播请直接联系模型作者。

## 2. Live2D Cubism SDK / Cubism Core

- `live2d-py` 的预编译 wheel 内含 **Live2D Cubism Core** 原生库,
  其权利归 **Live2D Inc.**,适用 Live2D 的专有许可(不是开源许可)。
- 本项目**没有**把 SDK 源码或原生库放进仓库;仓库里只是 `pip install live2d-py`。
- 打包发布 exe/zip 时,Cubism Core 会随包分发 —— 如果你要把本程序用于**商业用途**,
  请自行确认符合 Live2D 的发布许可(通常需要发布许可或付费授权),并遵守其品牌使用条款。
- 官方条款:<https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_cn.html>

## 3. 运行依赖

| 组件 | 版本示例 | 许可 | 说明 |
|---|---|---|---|
| [live2d-py](https://github.com/Arkueid/live2d-py) | 0.8.0.9 | MIT | Live2D 的 Python 绑定(内含 Cubism Core,见上) |
| [PySide6 / shiboken6](https://www.qt.io/) | 6.11.2 | **LGPL-3.0-only** 或 GPL-2.0/3.0 或商业许可 | 本项目按 LGPL-3.0 使用;分发打包版时须附 LGPL 全文(`build/share/LGPL-3.0.txt`)并允许替换该库 |
| [PyOpenGL](https://github.com/mcfletch/pyopengl) | 3.1.10 | BSD-3-Clause | OpenGL 绑定 |
| [numpy](https://numpy.org/) | 2.x | BSD-3-Clause | 仅若干开发工具用到,运行时不依赖 |
| [PyInstaller](https://pyinstaller.org/) | 6.x | GPL-2.0-or-later,附"可用于打包非自由程序"的例外条款 | 仅打包时使用,不影响本项目的发布许可 |
| Python | 3.11+ | PSF License | — |

### PySide6(LGPL-3.0)分发注意事项

用 LGPL 组件做**打包分发**时,常见的合规做法是:

1. 附上 LGPL-3.0 全文(本仓库 `build/share/LGPL-3.0.txt`,打包脚本会自动放进分享包);
2. 说明该库来源与版本,并允许使用者用自己编译的版本替换它
   (PyInstaller 的 `--onedir` 目录式打包天然满足:替换 `_internal/PySide6/*` 即可);
3. 不要修改 Qt 本身;若修改过,需公开修改后的源码。

本项目采用目录式(`onedir`)打包,`dist/DSWhalePet/_internal/` 下的 Qt 动态库可直接替换,
符合第 2 条。

## 4. 字体 / 图标

界面字体使用系统默认字体(微软雅黑等),不随包分发字体文件。托盘图标由程序运行时绘制,
不含第三方素材。
