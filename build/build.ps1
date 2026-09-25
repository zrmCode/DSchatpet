<#
.SYNOPSIS
    一键打包 DS鲸鱼娘桌宠:PyInstaller 出 exe,若有 Inno Setup 再出安装包。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build\build.ps1        # Windows PowerShell 5.1
    pwsh -File build\build.ps1 -SkipInstaller                      # 装了 PowerShell 7 也行
#>
param(
    [switch]$SkipInstaller,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

# 控制台按 UTF-8 输出,否则 Windows PowerShell 5.1 下中文会显示成乱码
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
    chcp 65001 > $null
} catch { }

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
$pyinstaller = Join-Path $root '.venv\Scripts\pyinstaller.exe'
if (-not (Test-Path $python)) { throw "找不到虚拟环境: $python" }
if (-not (Test-Path $pyinstaller)) {
    throw "没装 PyInstaller。先运行: .venv\Scripts\python.exe -m pip install pyinstaller"
}

# PyInstaller 需要在临时目录解包大量文件,放到项目内更稳(也避开某些沙箱限制)
$env:TEMP = Join-Path $root '.tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null

if ($Clean) {
    Write-Host '清理旧的构建产物…'
    Remove-Item -Recurse -Force (Join-Path $root 'build\work'), (Join-Path $root 'dist') -ErrorAction SilentlyContinue
}

Write-Host '=== 1/5 检查打包素材 ==='
$modelDir = Join-Path $root 'assets\model'
if (-not (Test-Path (Join-Path $modelDir '*.model3.json'))) {
    Write-Warning "assets\model 里没有模型文件,打包后需要用户手动指定 --model-dir"
} else {
    $size = [math]::Round((Get-ChildItem $modelDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 2)
    Write-Host ("  模型素材 OK({0} MB)" -f $size)
}

Write-Host '=== 2/5 PyInstaller 打包 ==='
& $pyinstaller 'build\pet.spec' --noconfirm --distpath dist --workpath 'build\work'
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败(退出码 $LASTEXITCODE)" }

$exe = Join-Path $root 'dist\DSWhalePet\DSWhalePet.exe'
if (-not (Test-Path $exe)) { throw "没有生成 exe: $exe" }
$exeSize = [math]::Round((Get-Item $exe).Length / 1MB, 2)
$distSize = [math]::Round((Get-ChildItem (Join-Path $root 'dist\DSWhalePet') -Recurse -File |
    Measure-Object Length -Sum).Sum / 1MB, 2)
Write-Host ("  exe: {0}({1} MB),整个目录 {2} MB" -f $exe, $exeSize, $distSize)

Write-Host '=== 3/5 自检 exe ==='
$log = Join-Path (Split-Path $exe) 'pet.log'
Remove-Item $log -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath $exe -ArgumentList '--selftest' -PassThru
if (-not $proc.WaitForExit(60000)) { $proc.Kill(); throw '自检超时(60 秒)' }
Start-Sleep -Milliseconds 500
if (Test-Path $log) {
    Write-Host '  自检日志:'
    Get-Content $log -Encoding UTF8 | ForEach-Object { "    $_" }
} else {
    Write-Warning "  没有生成 pet.log,自检可能没跑起来"
}

Write-Host '=== 4/5 组装分享包(清个人数据 + 放许可与说明)==='

$distDir = Join-Path $root 'dist\DSWhalePet'

# 1) 清掉运行时产物:个人配置(可能含 Key)、日志、养成档案(个性与记忆属个人数据)
foreach ($name in @('config.json', 'pet.log', 'pet-fatal.log', 'pet.lock', 'pet.lock.pid', 'memory')) {
    $p = Join-Path $distDir $name
    if (Test-Path $p) { Remove-Item $p -Recurse -Force; Write-Host "  已清理个人数据: $name" }
}

# 2) 放入分享说明与许可(模型署名链 + Qt LGPL 全文 + 使用须知/按键表)
$shareDir = Join-Path $root 'build\share'
if (Test-Path $shareDir) {
    Copy-Item (Join-Path $shareDir '*') $distDir -Force -Recurse
    Write-Host "  已放入分享说明与许可: $((Get-ChildItem $shareDir).Count) 个文件"
} else {
    Write-Warning "  找不到 $shareDir,分享说明没放进包里"
}
$licenseDir = Join-Path $root 'assets\许可'
if (Test-Path $licenseDir) {
    Copy-Item (Join-Path $licenseDir '*') $distDir -Force -Recurse
    Write-Host "  已放入模型作者的说明文件: $((Get-ChildItem $licenseDir | Select-Object -ExpandProperty Name) -join '、')"
} else {
    Write-Warning "  找不到 $licenseDir(模型使用须知/按键表)"
}

# 3) 安全校验:包里不能出现任何形式的 API Key
$leak = Get-ChildItem $distDir -Recurse -File -Include *.json, *.txt, *.yml, *.yaml -ErrorAction SilentlyContinue |
    Select-String -Pattern 'sk-[A-Za-z0-9]{10,}' -List -ErrorAction SilentlyContinue
if ($leak) {
    Write-Warning "  ⚠️ 包内疑似存在 API Key,请检查: $($leak.Path -join ', ')"
} else {
    Write-Host '  ✅ 安全检查:包内未发现任何 API Key'
}

if ($SkipInstaller) { Write-Host '=== 已跳过安装包步骤 ==='; exit 0 }

Write-Host '=== 5/5 生成安装包(需要 Inno Setup)=='
$iscc = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Warning '未找到 Inno Setup(ISCC.exe),跳过安装包。装好后重新运行本脚本即可生成安装包。'
    Write-Host ''
    Write-Host "分享包已就绪:$distDir"
    Write-Host '  直接把该目录压成 zip 发给别人即可(免安装,双击 DSWhalePet.exe 运行)'
    exit 0
}
Write-Host "用 $iscc 生成安装包…"
& $iscc (Join-Path $root 'build\installer.iss')
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 编译失败(退出码 $LASTEXITCODE)" }
Get-ChildItem (Join-Path $root 'dist\*.exe') | ForEach-Object {
    Write-Host ("  安装包: {0}({1} MB)" -f $_.Name, [math]::Round($_.Length / 1MB, 2))
}
exit 0
