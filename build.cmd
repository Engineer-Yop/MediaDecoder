@echo off
rem Builds dist\MediaDecoder\MediaDecoder.exe (PyInstaller) and, if Inno Setup 6 is installed,
rem installer\MediaDecoder-Setup-1.1.0.exe. Double-click it, or run:  build.cmd
rem (A .cmd file is not affected by PowerShell's script execution policy.)
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo Creating virtual environment .venv ...
    py -3.12 -m venv .venv 2>nul
    if not exist "%PY%" python -m venv .venv
)
if not exist "%PY%" (
    echo Could not create .venv - install Python 3.12:  winget install Python.Python.3.12
    exit /b 1
)

"%PY%" -m pip install --upgrade pip || exit /b 1
"%PY%" -m pip install -r requirements.txt pyinstaller || exit /b 1
"%PY%" assets\make_icon.py || exit /b 1

"%PY%" -m PyInstaller main.py --noconfirm --clean --windowed --name MediaDecoder ^
    --icon assets\icon.ico ^
    --collect-all faster_whisper --collect-all ctranslate2 --collect-all imageio_ffmpeg ^
    --collect-binaries onnxruntime --collect-data onnxruntime ^
    --exclude-module tkinter --exclude-module matplotlib || exit /b 1
echo Built dist\MediaDecoder\MediaDecoder.exe

set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo Inno Setup 6 not found - skipped the installer. Install it with:  winget install JRSoftware.InnoSetup
    exit /b 0
)
"%ISCC%" /Q "/DSourceDir=%CD%\dist\MediaDecoder" installer.iss || exit /b 1
echo Installer written to installer\MediaDecoder-Setup-1.1.0.exe
