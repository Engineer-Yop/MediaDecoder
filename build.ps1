# Builds dist\MediaDecoder\MediaDecoder.exe with PyInstaller, then (if Inno Setup 6 is
# installed) installer\MediaDecoder-Setup-1.1.0.exe.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1
#   powershell -ExecutionPolicy Bypass -File build.ps1 -Python C:\path\to\venv\Scripts\python.exe
param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$DistDir = "dist",
    [string]$WorkDir = "build",
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment .venv ..."
    py -3.12 -m venv .venv
    if (-not $?) { python -m venv .venv }
    $Python = ".\.venv\Scripts\python.exe"
}
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

& $Python assets\make_icon.py

& $Python -m PyInstaller main.py --noconfirm --clean --windowed `
    --name MediaDecoder --icon assets\icon.ico `
    --distpath $DistDir --workpath $WorkDir `
    --collect-all faster_whisper --collect-all ctranslate2 --collect-all imageio_ffmpeg `
    --collect-binaries onnxruntime --collect-data onnxruntime `
    --exclude-module tkinter --exclude-module matplotlib
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built $DistDir\MediaDecoder\MediaDecoder.exe"

if ($SkipInstaller) { exit 0 }
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc "/DSourceDir=$((Resolve-Path "$DistDir\MediaDecoder").Path)" installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "Installer written to installer\MediaDecoder-Setup-1.1.0.exe"
} else {
    Write-Host "Inno Setup 6 not found - skipped the installer. Install it with:  winget install JRSoftware.InnoSetup"
}
