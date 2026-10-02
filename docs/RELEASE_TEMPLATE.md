# Release 说明模板(复制到 GitHub Release 里,按需改版本号)

> 用法:GitHub → Releases → Draft a new release → Tag 填 `v0.2.0` → 把下面内容(
> 从「## 下载」开始)粘进说明框 → 上传附件 `dist/DSchatpet-v0.2.0-windows-x64.zip` → 发布。

---

## 下载

| 文件 | 说明 |
|---|---|
| `DSchatpet-v0.2.0-windows-x64.zip` | **免安装**:解压后双击 `DSWhalePet.exe` 即可,模型已在内 |

- 系统:Windows 10 / 11(64 位)。**不需要装 Python**。
- 免安装、无需管理员权限;想开机自启在右键菜单里勾一下(写 HKCU,可随时取消)。
- 解压后如果 Windows 提示"未知发布者",那是因为没有代码签名,选"仍要运行"即可。

## 这个版本有什么

- 透明无边框置顶桌宠,不挡桌面操作(透明区域点击穿透),视线跟随鼠标,60 FPS
- **点它一下 = 由模型自己决定说什么、配什么表情**(不是随机变脸;没配对话后端时点击静默)
- 对话:接任意 OpenAI 兼容接口(DeepSeek / OpenAI / 本地 Ollama),模型自选表情与动作
- **本地养成**:个性档案、长期记忆、亲密度,存在本机 `memory/`,换模型也不丢
- 全局快捷键(`Ctrl+Alt+W` 显隐 / `Ctrl+Alt+E` 聊天)、托盘菜单、设置面板、单实例保护

## ⚠️ 模型版权(请务必阅读)

模型「DS鲸鱼娘」的版权归 **B站 @氵六青(UID 11272072)** 所有,作者条款:

> 商用直播 ✅ / 自印物料 ✅ / **禁止任何形式的盗用以及出售** / 此模型为**无偿分享**

本压缩包属于作者许可的**无偿分享**,所以:

- ✅ 可以自由下载、自己用、直播用、分享给朋友;
- ❌ **不得出售**(包括"付费下载""打包进付费商品""充值才能拿"等任何形式);
- ❌ 不要删除署名、不要声称模型是自己做的;
- 二次创作 / 改模 / 商用,请联系作者(交流群 645169617)。

完整条款见压缩包内的 `模型使用须知.txt`。

## 代码许可

- 本项目代码:**GPL-3.0-only** —— 衍生发布同样需要开源。
- 第三方组件:`live2d-py`(MIT,内含 **Live2D Cubism Core**,受 Live2D 官方许可约束)、
  **PySide6**(LGPL-3.0,包内已附 `LGPL-3.0.txt`)、PyOpenGL、numpy。
  详见仓库里的 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

## 怎么让对话能用(程序不附带任何 API Key)

| 方式 | 做法 | 谁付钱 |
|---|---|---|
| 填自己的 Key | 右键桌宠 →「设置…」→「对话」→ 选服务商 → 填 Key → 保存 | 你自己 |
| 本地模型 | 装 [Ollama](https://ollama.com/) 拉个模型,设置里服务商选「Ollama 本地」(地址是 `127.0.0.1`,**免 Key**) | 电费 |
| 不配 | 只当桌宠用:表情/动画/拖动/穿透/快捷键/托盘全都正常,不报错、不打扰 | 无人 |

> 对话是**按用量付费**的服务(DeepSeek / OpenAI 都收费),费用由填 Key 的人承担。
> 记忆自动抽取会把「最近的对话」发给所配置的服务商;所有记录只存在本机 `memory/`。

## 文件校验(可选)

```
SHA256  DSchatpet-v0.2.0-windows-x64.zip
235472091750F6EB171164DCF57AF3316520DF93C631CB8025D08034EF3975FE
```

PowerShell 自查:`Get-FileHash .\DSchatpet-v0.2.0-windows-x64.zip -Algorithm SHA256`

## 从源码跑

见仓库 [README](../README.md):`py -3.11 -m venv .venv` → 装 `requirements.txt` →
把模型放进 `assets/model/` → `run.bat`。

> 仓库**不含**模型文件(版权原因),从源码跑需要自备:下载本 Release 的 zip,
> 把 `_internal/assets/model/` 拷到仓库的 `assets/model/` 即可。
