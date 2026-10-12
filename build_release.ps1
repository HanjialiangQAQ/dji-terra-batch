$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建构建环境失败' }
}
$buildPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $buildPython -m pip install --cache-dir .pip_cache -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw '安装构建依赖失败' }
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot '.pyinstaller_cache'
& $buildPython -m PyInstaller --noconfirm --windowed --onedir --name TerraBatch --distpath release --workpath build --specpath build --hidden-import pynput.mouse._win32 --hidden-import pynput.keyboard._win32 --hidden-import PIL.ImageTk --exclude-module matplotlib --exclude-module pandas --exclude-module scipy terra_entry.py
if ($LASTEXITCODE -ne 0) { throw '打包失败' }
$destination = Join-Path $PSScriptRoot 'release\TerraBatch'
Copy-Item -LiteralPath terra_profile.example.json -Destination (Join-Path $destination 'terra_profile.json')
Copy-Item -LiteralPath '使用说明.txt','快速开始.txt' -Destination $destination
$templatesDestination = Join-Path $destination 'templates'
New-Item -ItemType Directory -Path $templatesDestination -Force | Out-Null
Copy-Item -LiteralPath 'templates\completed_text.png','templates\completed_confirm.png' -Destination $templatesDestination
Write-Host "运行包：$destination"
