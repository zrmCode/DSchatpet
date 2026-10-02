# DS鲸鱼娘 桌宠

把 **DS鲸鱼娘** 的 Live2D Cubism 模型做成一只常驻桌面的桌宠:透明无边框、置顶、
不挡桌面操作;会眨眼呼吸、视线跟随鼠标;点它一下,**由模型自己决定**说什么话、配什么表情;
能接任意 OpenAI 兼容接口(DeepSeek / OpenAI / 本地 Ollama)聊天,并在**本地**养成个性与长期记忆
(换模型也不丢)。纯 Python + PySide6 + live2d-py,**不做语音与口型**。

![桌宠](docs/screenshots/pet.png)

![对话界面](docs/screenshots/chat.png)

> ⚠️ **模型文件不在本仓库里。** Live2D 模型「DS鲸鱼娘」版权归原作者
> [B站 @氵六青](https://space.bilibili.com/11272072)(UID 11272072)所有,条款是
> **无偿分享、禁止出售**。所以仓库里只有代码,模型随 **Release 免安装压缩包** 提供。
> 从源码跑之前,请按 [`assets/README.md`](assets/README.md) 把模型放进 `assets/model/`
> (放错了也不会闪退:程序会弹窗告诉你该放哪儿,并能直接打开那个目录)。

| | |
|---|---|
| **代码许可** | [GPL-3.0-only](LICENSE)(衍生发布同样要开源) |
| **模型** | 版权归原作者,无偿分享、**禁止出售**、保留署名 —— 见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) |
| **依赖** | live2d-py(MIT)、PySide6(LGPL-3.0)、PyOpenGL、numpy —— 同上 |
| **平台** | Windows 10/11,Python **3.11+** |

---

## 快速开始

**方式 A:直接用(推荐给只是想用的朋友)**
下载 Release 里的免安装 zip,解压后双击 `DSWhalePet.exe` —— 模型已在内,开箱即用。
别的机器一样能跑:每台机器各自配自己的对话后端(见 [对话](#对话))。

**方式 B:从源码跑(想改代码的走这条)**

```powershell
git clone https://github.com/zrmCode/DSchatpet
cd DSchatpet

py -3.11 -m venv .venv                      # live2d-py 只有 abi3 wheel,Python 必须 >= 3.11
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 把模型放到 assets\model\(怎么拿见 assets\README.md),然后:
run.bat                                     # 双击也行:无控制台启动
run-debug.bat                               # 带控制台,方便看日志
```

首次启动会自动把桌宠放到屏幕右下角;之后记住你拖到的位置。

### 操作

| 操作 | 效果 |
|---|---|
| 鼠标移动 | 视线跟随(头、眼睛、身体轻微跟随) |
| **鼠标靠近桌宠** | **自动弹出聊天输入框**(固定在其正下方,不抢焦点);离开一会儿后自动收起 |
| 左键拖拽 | 把鲸鱼娘拖到任意位置(位置自动保存;输入框与气泡都跟着走) |
| 左键点一下 | **戳它一下**:这件事会告诉模型,由它自己决定说一句什么、配什么表情/动作(需要配好对话后端;没配后端时点击不做任何事) |
| **双击模型** | 打开聊天输入框**并聚焦**(回车发送,Esc 关闭) |
| 右键 | 完整动作菜单:聊天入口 + 44 表情 + 7 动画 + 归位 + 置顶/穿透/自启/**设置** |
| **Ctrl+Alt+W** | 全局快捷键:显示 / 隐藏桌宠 |
| **Ctrl+Alt+E** | 全局快捷键:打开聊天输入框 |
| 托盘图标 | 显示/隐藏、同一套动作菜单、退出 |
| 双击托盘图标 | 显示 / 隐藏 |

透明区域默认**点击穿透**:鼠标不在模型身上时,点击会直接落到桌面或下层窗口,
不会挡住你干活。鼠标移到模型上时自动恢复可交互。

---

## 对话

把鼠标移到鲸鱼娘旁边(默认 60px 内),聊天输入框会自动出现在它**正下方、紧贴 4px**;
鼠标离开一会儿(默认 0.5 秒)自动收起。也可以**双击桌宠**或按 `Ctrl+Alt+E` 打开
——这两种方式会直接聚焦,方便立刻打字。输入框是天蓝色,提示文字就是「聊天…」。

输入框**跟随桌宠拖动**:拖到哪儿它就在正下方跟着走。回复显示在头顶的气泡里,
气泡同样跟随;气泡不吃焦点,不会打断你正在做的事。

**层级**:输入框与气泡都是桌宠窗口的**附属窗口**,和桌宠同一层级(不各自单独置顶),
桌宠隐藏时它们一起隐藏 —— 不会出现"聊天框浮在所有窗口最上层"的情况。

### 点它一下,由它自己反应

单击桌宠**不是**"随机换个表情"那种机器反射,而是把「被戳了一下」这件事**告诉模型**:

- 会连**戳到哪儿**一起告诉它(按窗口纵向位置粗判:头顶 / 脸 / 身上 / 尾巴和裙摆 —— 该模型没有
  HitAreas,只能这么判,代码里写明了),它自己决定说一句什么、配哪个表情或动作;
- 单击后延迟 250ms 才发请求,所以**双击开聊天框不会顺带触发一次"被戳"**;拖拽不算点击;
- 上一句还在想的时候,点击被跳过(不打断正在进行的那句话);
- **没有配对话后端时,点击什么都不做**(只当桌宠陪你,不弹提示、不打扰),开关见「设置 → 外观与交互」。

### 让模型自己用表情和动作

桌宠会把**全部 44 个表情 + 7 个动作的名字与含义**告诉模型(像 `星星眼(眼睛变成星星,崇拜/超兴奋)`、
`闭眼口水(闭眼流口水,睡着/发呆)` 这样),并允许它在回复时自己挑:

- **首选工具调用**(`set_expression` / `play_motion`,候选用 enum 限定,基本不会乱选);
- 接口**不支持工具调用**时(很多本地小模型、部分中转站),自动改用**文字指令**:
  模型在回复末尾写 `[表情:星星眼] [动作:自拍动画]`,程序解析后执行并从气泡文字里去掉。
  被拒一次后会自动记住,不再反复试错。
- **名字必须真实存在**:不认识的表情/动作会被**直接丢弃**(只记日志),绝不会把状态改坏。
- 模型没给动作时,仍用关键词规则兜底(问号/感叹号/爱心眼…),保证不会"面瘫"。

### 待机时的自主行为

| 档 | 行为 | 成本 |
|---|---|---|
| **A 本地随机**(默认开) | 每 45 秒上下(0.6~1.6 倍随机)自己换个表情,偶尔做个小动作;拖动中/正在聊天/你正在输入时自动让路 | 免费、离线可用 |
| **B 模型"想事情"**(默认关) | 每隔一段时间问模型"你在想什么/想做什么",它回一句 ≤20 字的内心独白 + 表情/动作,气泡显示并执行;刚聊过 60 秒内不打扰,没有可用后端时静默跳过 | 消耗少量 API 费用 |

两档可在「设置…」里单独开关。想省心就只开 A;想让它"有脑子"再开 B。

**表情保持期**:每次回应你之后的 **8 秒**内,待机的随机行为会**让位**——
否则刚选好的表情会在 1 秒内被随机的顶掉,你根本看不到它的反应。

### 养成:记忆与个性(存在本地,换模型不丢)

**核心思路:人格和记忆都在本地文件里,模型只是"演员"** —— 换模型 / 换中转站 / 换 Key,
档案文件不变,同一段人格提示会发给新模型,所以养成的个性原样保留。

```
memory/                      ← 全部只在本机
├── profile.json      个性档案:名字、怎么称呼你、性格、喜好、亲密度、相处天数、对话次数、里程碑
├── memories.jsonl    长期记忆:每条带时间/类型/重要度/来源(自动 or 手动)
└── history.jsonl     最近对话(跨重启连续)
```

**它怎么"记住"**

- **你手动教**:直接说「记住:我住在杭州,喜欢喝美式」→ 立刻入库(标为"手动",优先保留)
- **自动抽取**:每 N 轮对话(默认 5)让模型从最近的对话里挑出值得长期记住的事,
  输出 JSON 存进记忆库;去重(高重合不再记)、限量(默认 200 条,超了淘汰低分)
- 实测真实 DeepSeek 抽取效果:

  ```
  原始返回: [{"text":"用户叫小林","kind":"fact","importance":5},
            {"text":"小林在做 Live2D 桌宠项目","kind":"fact","importance":4},
            {"text":"小林不喝咖啡,只喝茶","kind":"preference","importance":3}]
  ```

**它怎么"用上"记忆**

每次请求前,程序按当前话题挑出最相关的几条记忆(相关度 + 重要度 + 新鲜度打分,
零依赖)+ 档案摘要拼进系统提示(有字符预算,默认 800 字),并要求它
"把记忆当成自己经历过的事,别说'根据记忆',没记住就坦白说不记得"。

**养成数值**(档位会影响语气)

| 亲密度 | 档位 | 语气 |
|---|---|---|
| 0 | 陌生 | 礼貌但拘谨 |
| 30 | 熟悉 | 自然放松,会主动接话 |
| 120 | 亲近 | 会开玩笑、撒娇,偶尔提前聊过的事 |
| 400 | 挚友 | 随意亲昵,会关心你近况 |

里程碑(第一次聊天、聊满 10/50/200/1000 句)也会记进档案,并由提示词交给模型。

**管理**:右键 →「设置…」→「记忆与养成」页:开关、抽取频率、注入条数与上限、
改名字与称呼、查看记忆列表(删除单条 / 清空全部)、**导出 / 导入档案**
(换电脑、备份、分享养成结果都靠它)。

**隐私**:档案与记忆只存本机;只有"自动抽取"那一步会把**最近的对话**发给所配置的
模型服务(用本地 Ollama 时不出机器)。不想留痕就把 `memory_enabled` 关掉,或清空记忆。

### 气泡(回复框)排版

回复气泡的尺寸是**算出来的**:宽度按最长一行估(120~320px 内容宽),
高度用 `QTextDocument` 按实际字体与可用文本宽度**确定性**量出,并保证:

- 长回复**完整显示**,最后一行不会被裁;
- 短回复不会虚高(不会出现"7 个字配 120px 高框");
- 无论桌宠在屏幕哪个角落,气泡都**完整落在屏幕内**(上方放不下就改放下方);
- 同一段文字两次显示的尺寸完全一致。

> 这里踩过两个坑,已写进 `tools/bubble_layout_test.py` 做回归:
> ① 用 `QFontMetrics.boundingRect` 估高度时**中文会少算一行** → 末行被裁;
> ② `QLabel.heightForWidth` 受"控件是否已布局"影响,第一次显示长文本会按旧宽度算 → 仍然裁字。

- **说话时换表情**:思考中会切「呆呆眼」;回复带情绪时自动换表情
  (问号 / 感叹号 / 爱心眼 / 脸红 / 开心兴奋 / 悲伤 / 生气 / 调皮 / 晕晕 / 流汗),
  表情名取自模型自身,不存在就跳过。规则是"具体情绪词优先、`?`与`!`这类标点信号最后",
  否则「我也喜欢你!」会被标点抢走判成感叹号。
- **人设**内置在 `pet/chat.py`(`DEFAULT_PERSONA`),强调"回复要短、纯文本",
  因为气泡只有 320px 宽。想改人设就在 `config.json` 里填 `chat_persona`。

### 配置 API Key(三种方式,任选其一)

1. **复用 DSH 的凭据**(默认已开启):直接读 `~/.dsh/.credentials.yaml` 里的
   `refs.DEEPSEEK_API_KEY` —— 你的 DSH 已经配好 key 的话,桌宠开箱即用,无需任何设置。
2. **环境变量**:设置 `DEEPSEEK_API_KEY`(变量名可用 `chat_api_key_env` 改)。
3. **直接填**:`config.json` 的 `chat_api_key`(明文存在本文件,注意别外传)。

接口是 OpenAI 兼容协议,所以也能接中转站、Ollama 等:改 `chat_base_url` 与 `chat_model`
即可(如 `http://127.0.0.1:11434/v1` + `qwen2.5:7b`)。

自检命令(只打印 Key 来源与长度,**不会打印 Key 内容**):

```powershell
.venv\Scripts\python.exe tools\chat_live_check.py
```

---

## 设置面板

右键桌宠 →「设置…」,所有 `config.json` 字段都能可视化编辑,**保存后立即生效,不用重启**:

- 外观:窗口高度、模型缩放、不透明度、帧率、置顶
- 交互:点击穿透、视线跟随(幅度/平滑)、待机动画、点击让 AI 回应
- 对话:启用开关、接口地址、模型名、API Key、环境变量名、是否复用 DSH 凭据、
  记忆轮数、超时、气泡停留、自定义人设 —— 附带「测试对话」按钮,可先验证再保存
- 系统:两个全局快捷键的写法、开机自启开关

---

## 打包成软件

一条命令搞定(会依次做:检查素材 → PyInstaller 打包 → 跑 exe 自检 → 有 Inno Setup 就出安装包):

```powershell
powershell -ExecutionPolicy Bypass -File build\build.ps1   # Windows PowerShell 5.1 即可
# 装了 PowerShell 7 也可以:pwsh -File build\build.ps1
# 或分步:
.venv\Scripts\pyinstaller.exe build\pet.spec --noconfirm --distpath dist --workpath build\work
```

产物:`dist\DSWhalePet\`(约 160 MB,含模型;exe 本身约 5 MB)。
安装包:装好 [Inno Setup 6](https://jrsoftware.org/isdl.php) 后运行 `build\installer.iss`,
或直接让 `build.ps1` 自动编译;安装包默认装到用户目录、无需管理员权限。
详细打包记录与排查工具见 `build\NOTES.md`。

**打包踩过的坑(已修,别再踩)**:

1. ⚠️ **exe 文件名必须纯 ASCII**。实测:PyInstaller 冻结的 PySide6 应用,若 exe 文件名含中文,
   **一创建窗口就崩**(`0xC0000409` / abort),连最普通的 `QWidget.show()` 都过不去;
   **目录名含中文则完全没问题**。所以 exe 名叫 `DSWhalePet.exe`,中文只用在窗口标题、
   托盘提示与快捷方式名里。自己改名分发时务必保持 ASCII。
2. **必须带上 live2d 的 GLSL 着色器**。`live2d/v3/FrameworkShaders/*.frag|*.vert` 是渲染器运行时
   按路径读取的数据文件,漏掉会让 exe 启动即崩。spec 里用 `collect_data_files("live2d")` 收集。
3. `live2d.v3.params` 需要显式 hidden import(官方 README 也有说明)。
4. 打包后配置与日志写到 **exe 所在目录**,资源从 `sys._MEIPASS` 读 —— 见 `pet/config.py` 的
   `APP_DIR` / `BUNDLE_DIR`;开机自启命令在打包模式下直接指向 exe 自身。
5. 窗口程序没有控制台,所以**关键日志与自检结果都落盘**(`pet.log` / `pet-fatal.log`),
   排查时先看它们;`--qt-probe` 可做分步探针。

**打包版验证结果**:模型加载 247 参数 / 44 表情 / 7 动画、实测 60 FPS、快捷键注册成功、
对话可用(Key 取自 DSH 凭据)、无原生崩溃。

---

## 目录结构

```
DS鲸鱼娘桌宠/
├── main.py                 入口:装配窗口 + 托盘 + 生命周期(含缺模型时的引导)
├── pet/
│   ├── config.py           配置(项目内 config.json,可整体拷走;兼容打包后的路径)
│   ├── actions.py          从 vtube.json 提取动作/表情目录
│   ├── model.py            Live2D 封装(表情、动画、视线、口型)
│   ├── chat.py             LLM 客户端(OpenAI 兼容)+ 情绪→表情映射
│   ├── memory.py           养成档案 / 长期记忆 / 对话历史(全在本地)
│   ├── bubble.py           说话气泡 + 聊天输入框
│   ├── settings_dialog.py  设置面板(保存后即时生效)
│   ├── hotkey.py           全局快捷键(RegisterHotKey,零依赖)
│   ├── single_instance.py  单实例保护(文件锁,避免两只桌宠叠在一起)
│   ├── win32.py            Windows 技巧(点击穿透、置顶、隐藏出 Alt+Tab、开机自启、原生弹窗)
│   └── window.py           桌宠窗口:渲染循环 + 交互 + 对话 + 设置
├── assets/
│   ├── model/              ← 模型放这里(**不进仓库**,见 assets/README.md)
│   └── 许可/                作者条款与按键表(随仓库保留,署名用)
├── build/                  打包:pet.spec / build.ps1 / installer.iss / share/(许可与说明)
├── docs/screenshots/       README 用的界面截图(只抓控件生成,不含桌面内容)
├── tools/                  诊断、测试与发布自检工具(见下)
├── LICENSE                 GPL-3.0-only
├── THIRD_PARTY_NOTICES.md  第三方组件与模型许可
├── requirements.txt
├── run.bat / run-debug.bat
└── .venv/                  项目内虚拟环境(不进仓库)
```

模型**不**进仓库(版权归原作者,见 [`assets/README.md`](assets/README.md));
程序默认按 `assets/model/` → 可写目录 → 工作区原始模型目录的顺序自动探测,
也可以用 `--model-dir <目录>` 指定别处。

---

## 配置(`config.json`)

> **设置面板里改不到的项**:以下几项是程序按模型实测调好的值,故意**不放进面板**
> (放进去只会把自己弄坏),要微调就直接编辑这个文件 ——
> `window_height`、`scale`(缩放 1.0 会把模型右边缘切掉)、`fps`、
> `gaze_strength`、`gaze_smoothing`(视线跟随调参)、
> `idle_action_interval`、`idle_thought_interval`(待机节奏)。

| 字段 | 默认 | 说明 |
|---|---|---|
| `window_height` | 373 | 窗口高度(像素),宽度按模型画布比例推得。373 ≈ 原先 560 的 **2/3**(实测原先有点大、挡屏幕) **(面板不可改)** |
| `scale` | 0.85 | 模型缩放。**该模型内容偏右且贴边,1.0 会在右边缘被裁切**,0.85 实测四周留白正常 **(面板不可改)** |
| `opacity` | 1.0 | 整体不透明度 |
| `always_on_top` | true | 置顶 |
| `click_through` | true | 透明区域点击穿透 |
| `gaze_follow` | true | 视线跟随鼠标 |
| `gaze_strength` | 1.0 | 跟随幅度 **(面板不可改)** |
| `gaze_smoothing` | 0.18 | 平滑系数,越小越"迟钝" **(面板不可改)** |
| `idle_motion` | true | 待机动画循环 |
| `poke_reaction` | true | 点一下时让 AI 理解并回应(说话 + 表情)。原「点一下随机变表情」已取消 |
| `fps` | 60 | 帧率上限 **(面板不可改)** |
| `model_dir` | "" | 空 = 自动探测 |
| `window_x` / `window_y` | null | 窗口位置,null = 首次启动贴右下角 |
| `chat_enabled` | true | 是否启用对话 |
| `chat_base_url` | https://api.deepseek.com | OpenAI 兼容接口地址 |
| `chat_model` | deepseek-chat | 模型名 |
| `chat_api_key` | "" | 直接填 Key(明文,注意别外传) |
| `chat_api_key_env` | DEEPSEEK_API_KEY | 从哪个环境变量读 Key |
| `chat_use_dsh_credentials` | true | 允许复用 DSH 凭据文件里的 Key |
| `chat_persona` | "" | 空 = 用内置人设 |
| `chat_history` | 10 | 记住最近几轮对话 |
| `chat_timeout` | 30.0 | 请求超时(秒) |
| `chat_bubble_seconds` | 12 | 气泡自动消失秒数,0 = 不消失 |
| `chat_hover` | true | 鼠标靠近时自动弹出聊天输入框 |
| `chat_hover_distance` | 60 | "靠近"的判定距离(像素) |
| `chat_input_gap` | 4 | 输入框与桌宠底边的间距(像素),越小越贴近 |
| `chat_model_actions` | true | 让模型自己选表情/动作(与语气配合) |
| `chat_use_tools` | true | 优先用工具调用;接口不支持时自动改用文字指令 |
| `idle_autonomy` | true | 待机时自己换表情 / 做小动作(免费、无需 Key) |
| `idle_action_interval` | 45 | 随机行为的平均间隔(秒),实际按 0.6~1.6 倍随机 **(面板不可改)** |
| `idle_llm_thoughts` | false | 待机时让模型自己"想事情"(**会消耗少量 API 费用**,默认关) |
| `idle_thought_interval` | 300 | "想事情"的最短间隔(秒) **(面板不可改)** |
| `idle_thought_bubble` | true | 把内心独白显示在气泡里(关掉就只做动作) |
| `memory_enabled` | true | 记忆与养成总开关(档案只存本机) |
| `memory_extract_every` | 5 | 每 N 轮对话自动抽取长期记忆(0 = 不自动抽) |
| `memory_max_items` | 200 | 记忆库容量上限,超了淘汰低分记忆 |
| `memory_inject_items` | 6 | 每次请求注入多少条相关记忆 |
| `memory_inject_chars` | 800 | 注入记忆的字符预算 |
| `hotkey_toggle_visible` | ctrl+alt+W | 显示/隐藏的全局快捷键 |
| `hotkey_open_chat` | ctrl+alt+E | 打开聊天的全局快捷键 |
| `autostart` | false | 开机自启(写在 HKCU\...\Run,可在菜单里开关) |

---

## 技术要点(踩过的坑)

1. **模型没声明动作与表情**。`c_0120.model3.json` 只有 `Moc / Textures / Physics /
   DisplayInfo`,44 个表情和 7 个动画是作者通过 VTube Studio 热键分发的。
   因此动作目录直接从 `c_0120.vtube.json` 的 `Hotkeys` 生成(52 条),
   运行时用 live2d-py 的 `LoadExtraExpression` / `LoadExtraMotion` 注册,不改模型文件。
2. **`LoadExtraMotion` 的返回值是"新动作在组内的 0 基索引"**,而不是文档里写的
   "加载数量"(文档与实现对不上,已用 `tools/probe.py` 实测确认)。
3. **待机动画会被手动动画打断,且打断时结束回调不触发** —— 只靠回调会让桌宠永久静止。
   现在用 `IsMotionFinished()` 每 0.5s 兜底把待机接上(`PetModel.ensure_idle`)。
4. **动作结束回调签名是 `(group, no)`**,少写参数会抛
   `SystemError: returned a result with an exception set`(排查起来很费劲的一个坑)。
5. **透明度判定用逐像素 alpha**:每帧在 `paintGL` 末尾读 1×1 像素(比整屏回读便宜),
   据此切换 Win32 的 `WS_EX_TRANSPARENT`,实现"透明处穿透、模型上可交互"。
6. **`model3.json` 的 LipSync 组是空的**,阶段三做口型时需要手动驱动
   `ParamMouthOpenY`(配合 `ParamMouthForm`)。

---

## 诊断工具

需要桌面环境(要真实 OpenGL 上下文):

```powershell
# 打印模型结构,实测 LoadExtraMotion 语义,试播全部动画与表情
.venv\Scripts\python.exe tools\probe.py

# 截图当前画面(含 alpha 通道),用于确认渲染
.venv\Scripts\python.exe tools\shot.py shots --expression 星星眼 --gaze -1,0

# 检查渲染:包围盒、占比、是否被裁切;并可与另一张图对比差异
.venv\Scripts\python.exe tools\check_render.py shots\shot.png --compare shots\default.png

# 自检:显示 6 秒后自动退出并打印诊断
.venv\Scripts\python.exe main.py --selftest

# 对话链路测试(本地假服务器,不消耗额度、不需要 Key)
.venv\Scripts\python.exe tools\chat_test.py
.venv\Scripts\python.exe tools\chat_test.py --ui    # 额外跑界面端到端 + 截图

# 真实对话连通性(会真实调用一次,只打印 Key 来源与长度)
.venv\Scripts\python.exe tools\chat_live_check.py

# 全局快捷键与开机自启(含注册表读写,测试后自动清理)
.venv\Scripts\python.exe tools\hotkey_test.py

# 排查渲染类问题:表情残留 / 遮罩 / 参数越界 / 图片结构
.venv\Scripts\python.exe tools\expression_residue_test.py     # 切表情后参数是否有残留(最准)
.venv\Scripts\python.exe tools\expression_state_test.py       # 像素级:表情是否残留
.venv\Scripts\python.exe tools\expression_blend_test.py       # 快速切换是否混合卡住
.venv\Scripts\python.exe tools\expression_ghost_pixel_test.py # 切表情后像素残影 + 遮罩对比
.venv\Scripts\python.exe tools\mask_test.py                   # 裁剪遮罩是否够用
.venv\Scripts\python.exe tools\expression_range_test.py       # 表情是否把参数推出范围
.venv\Scripts\python.exe tools\param_dump.py --filter Eye     # 参数真实范围
.venv\Scripts\python.exe tools\single_instance_test.py        # 单实例保护
.venv\Scripts\python.exe tools\ascii_preview.py 图.png --mode sat   # 字符画"看"图
```

**开源/发布相关:**

```powershell
# 发布前自检:按 git 的视角审计"将要公开的文件",查 Key/个人数据/模型/超大文件
.venv\Scripts\python.exe tools\preflight_check.py
.venv\Scripts\python.exe tools\preflight_check.py --all    # 连未 add 的新文件一起审

# 生成 README 用的界面截图(只抓控件,不会拍到桌面)
.venv\Scripts\python.exe tools\make_screenshots.py

# 缺模型时的启动引导(退出码、弹窗文案、目标路径)
.venv\Scripts\python.exe tools\model_missing_test.py

# 点一下 → 模型理解并回应(戳哪儿、说了什么、配了什么表情)
.venv\Scripts\python.exe tools\poke_test.py
```

**回归测试一览**(全部用本地假服务器,不消耗额度、不需要 Key):

| 工具 | 覆盖 |
|---|---|
| `chat_test.py` | 对话链路:请求形状、历史轮数、返回结构、错误分支、凭据解析 |
| `autonomy_test.py` | 模型选表情/动作(工具调用 + 文字兜底)、工具不受支持时自动回退、待机 A/B 两档 |
| `memory_test.py` | 记忆去重/检索、亲密度与里程碑、导入导出、首次见面 |
| `bubble_layout_test.py` | 气泡排版**不裁字**(含模糊测试:随机文本形态 vs 超高画布标尺) |
| `poke_test.py` | 点击 = AI 回应;拖拽 ≠ 点击;双击不重复触发;无后端时静默 |
| `chat_hover_test.py` | 输入框跟随、贴边不翻到上方、悬停不抢焦点 |
| `settings_test.py` | 设置面板读写与即时生效 |
| `hotkey_test.py` | 全局快捷键注册 + 真实按键触发 + 开机自启注册表 |
| `single_instance_test.py` | 单实例保护 |
| `share_mode_test.py` | 分享模式:无 Key 也能用、包内无 Key |
| `model_missing_test.py` | 缺模型时的引导,不静默闪退 |
| `preflight_check.py` | 发布前审计(不是测试,是发布闸门) |

已验证的结果(可作为回归基线):

- 加载 247 个参数 / 44 表情 / 7 动画,稳定 60 FPS
- 模型占画面约 27%(scale 0.85),四周留白,未被裁切
- 切换表情影响约 7% 像素;视线看左↔看右影响约 12% 像素 —— 都说明真的动了
- 对话链路 29 项检查全通过(请求形状 / 历史轮数 / 返回结构兼容 / 错误分支 /
  情绪表情映射 / 凭据解析 / 界面端到端)
- 真实调用 DeepSeek 成功,Key 从 DSH 凭据文件自动解析
- 打包版自检:60 FPS、44 表情 / 7 动画、快捷键注册成功、包内无任何 Key

---

## 模型来源与许可(重要)

- 模型:**DS鲸鱼娘**,作者 B 站 [@氵六青](https://space.bilibili.com/11272072)(11272072)
- 原始说明(`assets/许可/模型使用须知.txt`):**商用直播 √、自印物料 √、禁止任何形式的盗用以及出售、此模型为无偿分享**
- 因此:**模型文件不进本仓库**,只随 Release 的免安装压缩包一起提供(那正是"无偿分享");
  **不得出售**本程序或其压缩包,也不要把模型打包进收费产品。
  拿不准的话,建议在作者交流群里问一句(群号 645169617)。
- 使用问题与定制桌宠:上述交流群。
- 代码许可是 [GPL-3.0-only](LICENSE);模型、Live2D Cubism、PySide6 等第三方组件的许可
  **各自独立**,与 GPL 不冲突,逐条列在 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

---

## 已知问题与排查

### 「眼睛出现永久残影/重影」

**首查:是不是同时跑了两只桌宠。** 桌宠是"透明置顶 + 位置持久化"的窗口,一旦启动两次,
两只窗口会**完全重叠**;此时点表情只有前景那只变了,后面那只停在旧表情上 ——
看上去就是"眼睛永久重影",而且怎么切都不消失。

- 程序现在有**单实例保护**:重复启动会被挡下并提示(日志里能看到"已有实例在运行")。
- 手动检查:`Get-Process pythonw,DSWhalePet -ErrorAction SilentlyContinue` 看有没有多个。
- 已用控制实验排除的原因(都有数据):
  - 表情参数残留 —— 43 个表情切走后参数**全部干净恢复**(`tools/expression_residue_test.py`)
  - 像素级残留 —— 受控对比差异 **0.0000%**(`tools/expression_state_test.py`)
  - 裁剪遮罩不足 —— mask 2/8/16 差异在重载噪声范围内(`tools/mask_test.py`)
  - 参数越界、眼皮范围、快速切换卡住 —— 均排除

### 其它

- **exe 文件名必须纯 ASCII**(见打包章节),否则一启动就崩。
- 崩溃与关键日志都写在 exe/项目目录的 `pet.log`、`pet-fatal.log`。

---

## 分享给别人(免费分享 / 别人那台机器也能用)

### 一句话原则

**每台机器各自配自己的对话后端**。程序里**不含任何 API Key**,分享出去不会花你的钱;
别人没配也能正常当纯桌宠用(不报错、不打扰),首次启动会提示一次去哪里配。

### 分享的两条路

| 方式 | 适合 | 做法 |
|---|---|---|
| **GitHub Release 免安装 zip**(推荐) | 给所有想用的人 | 跑 `build\build.ps1`,把 `dist\DSWhalePet\` 压成 zip 作为 Release 附件上传。**模型就在这个 zip 里**,这是唯一随包提供模型的渠道 |
| **直接拷目录** | 给朋友 | 把 `dist\DSWhalePet\` 整个目录拷过去(或压缩),双击 `DSWhalePet.exe` |

### 打包时会自动准备好分享包

`build\build.ps1` 的第 4 步会自动:

1. 清掉 `config.json`、`pet.log`、`pet-fatal.log`、锁文件等**个人数据**;
2. 放入 `分享说明.txt`、`LGPL-3.0.txt`、`模型使用须知.txt`、`按键表.txt`;
3. 做一次**密钥安全校验**:扫描包内所有文本文件,发现 `sk-` 形式的 Key 就报警。

打包完直接把 `dist\DSWhalePet\` 整个目录压成 zip 发出去即可(免安装,双击 exe 就能跑)。

### 别人怎么让对话能用(三选一)

| 方式 | 说明 | 谁付钱 |
|---|---|---|
| **填自己的 Key** | 右键 →「设置…」→「对话」→ 服务商选 DeepSeek/OpenAI → 填 Key | 使用者自己 |
| **本地模型(Ollama)** | 装 Ollama 拉个模型,服务商选「Ollama 本地」(地址 `127.0.0.1` → **免 Key**) | 使用者(电费) |
| **不配** | 只当桌宠:表情/动画/拖动/穿透/快捷键/托盘全都正常 | 无人 |

设置面板里选「服务商」会自动填好接口地址与模型名,还有「测试对话」可以先验证再保存。
任何 OpenAI 兼容的中转/自建服务也能用(填地址 + 模型名即可)。

### 许可与署名(分享前请确认)

- **本项目代码**:[GPL-3.0-only](LICENSE) —— 你可以自由使用、修改、再分发,
  但**衍生作品也必须以 GPL 开源**并保留版权声明。
- **模型**:作者原话是「**此模型为无偿分享**;禁止任何形式的盗用以及出售;商用直播 √、自印物料 √」
  → **免费分享符合原意**,但不要出售、不要删署名(B站 @氵六青)、不要声称原创。
- **Live2D Cubism**:`live2d-py` 本身是 MIT,但内置的 Cubism Core 受 Live2D 官方许可约束
  (个人/小规模免费,有收入门槛)→ 商业分发前请自行核对。
- **Qt / PySide6**:LGPL-3.0 → 分享包与 Release 里已附 `LGPL-3.0.txt`,请勿删除。
- 完整清单:[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

---

## 开源、贡献与发布

### 许可

- **本仓库代码**:[GPL-3.0-only](LICENSE)
- **第三方组件与模型**:[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)
  (live2d-py = MIT,内含 Live2D Cubism Core 受 Live2D 官方许可约束;PySide6 = LGPL-3.0;
  模型版权归 B站 @氵六青,无偿分享、禁止出售)
- 分发打包版时请一并保留 `LGPL-3.0.txt`、`分享说明.txt`、`模型使用须知.txt`
  —— 打包脚本会自动放进分享包。

### 仓库里有什么 / 没有什么

**有:** 全部源码(`pet/`、`main.py`)、打包配置(`build/`)、开发与回归工具(`tools/`)、
作者条款与署名(`assets/许可/`)、界面截图(`docs/screenshots/`,都是**只抓控件**生成的,
不含桌面内容)。

**没有(故意不放):** Live2D 模型文件(见 [`assets/README.md`](assets/README.md))、
`config.json`、`pet.log`、`memory/`(养成档案是个人数据)、`shots/`(屏幕截图可能含桌面内容)、
`dist/`、`.venv/`。

### 贡献

1. fork → 改 → 跑通测试 → 提 PR;
2. **改完请跑**:

   ```powershell
   .venv\Scripts\python.exe tools\preflight_check.py   # 别把 Key/个人数据/模型提交上去
   .venv\Scripts\python.exe tools\bubble_layout_test.py
   .venv\Scripts\python.exe tools\poke_test.py
   .venv\Scripts\python.exe tools\chat_hover_test.py
   ```

   完整清单见上面「诊断工具」一节;所有测试都用本地假服务器,**不消耗 API 额度、不需要 Key**。
3. 涉及渲染/交互的改动,最好附一张截图 —— 用
   `tools\make_screenshots.py` 生成(它只抓控件,不会把你的桌面拍进去)。

### 发版清单

```powershell
# 1) 确认要公开的文件里没有 Key / 个人数据 / 模型
.venv\Scripts\python.exe tools\preflight_check.py        # 有 [FAIL] 就别发

# 2) 跑测试(见上)
# 3) 打包(会清个人数据 + 放许可 + 扫 Key)
powershell -ExecutionPolicy Bypass -File build\build.ps1

# 4) 把 dist\DSWhalePet\ 压成 zip,作为一个 Release 的附件上传
#    Release 说明里请写明:模型版权归 B站 @氵六青,无偿分享、禁止出售
```

### 版权

```
Copyright (C) 2026 zrmCode

本程序是自由软件:你可以依据自由软件基金会发布的 GNU 通用公共许可证
(第 3 版,或你选择的任何更新版本)条款重新发布和/或修改它。
本程序希望有用,但不提供任何担保。详见 LICENSE。
```

> 想署别的名字/邮箱,改上面这一行即可(GPL 要求保留版权声明)。

---

## 后续计划

- 阶段一~四均已完成(语音/口型按你的决定不做)
- 可选增强:多模型切换、自动更新、代码签名(免 SmartScreen 报警)

### 测试工具的注意事项

`tools/` 里的工具都会设置 `window.persist_config = False`,
**不会把测试用的假服务器地址或 test-key 写回你的 `config.json`**(这个坑踩过一次)。
