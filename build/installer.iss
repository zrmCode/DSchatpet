; DS鲸鱼娘桌宠 —— Inno Setup 6 安装包脚本
;
; 编译方式(需先装 Inno Setup 6,再用命令行编译):
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
; 或在 Inno Setup Compiler 里直接打开本文件按 F9。
;
; 前置条件:已经跑过 PyInstaller,存在 ..\dist\DS鲸鱼娘桌宠\ 目录。
;
; 注意:Inno Setup 默认**不含**简体中文语言包(ChineseSimplified.isl 属于非官方翻译),
; 这里用默认英文界面;想用中文界面可下载该 isl 放进 Inno 的 Languages 目录后取消下面注释。

#define AppName "DS鲸鱼娘桌宠"
#define AppNameEn "DS Whale Pet"
#define AppVersion "1.0.0"
#define AppPublisher "Small-tailqwq / 氵六青"
; ⚠️ exe 文件名必须纯 ASCII:实测 exe 名含中文时,冻结后的 PySide6 应用一创建窗口就崩
;    (目录名含中文没事,所以 DefaultDirName 用中文也行;这里统一用 ASCII 更稳)
#define AppExeName "DSWhalePet.exe"
#define SourceDir "..\dist\DSWhalePet"

[Setup]
AppId={{8C2F1B44-4E5B-4E20-9B3C-1D5A7F6E0A21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=DSWhalePet-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 装到用户目录,不需要管理员权限
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}

; 想用中文界面时,把 ChineseSimplified.isl 放到 Inno 的 Languages 目录,然后取消注释:
; [Languages]
; Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked
Name: "autostart"; Description: "开机自动启动桌宠"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
[Registry]
; 勾选"开机自启"时才写 HKCU 的 Run 键;程序内菜单也能随时开关
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "DSWhalePet"; ValueData: """{app}\{#AppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清程序目录;用户配置(config.json)留在原地,便于重装后保留设置
Type: files; Name: "{app}\pet.log"
