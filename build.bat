@echo off
setlocal
pushd "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python 3.12 was not found on PATH.
    goto :fail
)

set "PYTHON_ABI="
for /f "usebackq delims=" %%V in (`python -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))"`) do set "PYTHON_ABI=%%V"
if not defined PYTHON_ABI (
    echo ERROR: Could not determine the Python version.
    goto :fail
)
if not "%PYTHON_ABI%"=="3.12" (
    echo ERROR: Python 3.12 is required, but 'python' reports:
    python --version
    goto :fail
)

where npm.cmd >nul 2>nul
if errorlevel 1 (
    echo ERROR: npm.cmd was not found on PATH.
    goto :fail
)
where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo ERROR: powershell.exe was not found on PATH.
    goto :fail
)
where git.exe >nul 2>nul
if errorlevel 1 (
    echo ERROR: git.exe was not found on PATH.
    goto :fail
)

echo Installing Electron dependencies...
call npm.cmd --prefix desktop ci
if errorlevel 1 goto :fail

echo Building the standalone Python runtime...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "desktop\scripts\build-runtime.ps1"
if errorlevel 1 goto :fail

echo Building the bundled Git runtime...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "desktop\scripts\build-git.ps1"
if errorlevel 1 goto :fail

echo Staging the packaged backend source...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "desktop\scripts\prepare-source.ps1"
if errorlevel 1 goto :fail

echo Compiling Python sources...
python -m compileall -q .
if errorlevel 1 goto :fail

echo Building the Electron application...
call npm.cmd --prefix desktop run build:win
if errorlevel 1 goto :fail

echo Verifying the packaged Python runtime...
"desktop\dist\win-unpacked\resources\python\python.exe" -c "import psutil, pywebio, requests, winpty"
if errorlevel 1 goto :fail

echo Creating the portable archive...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "desktop\scripts\archive-release.ps1"
if errorlevel 1 goto :fail

echo Build complete: desktop\dist\Palsitter-win-x64.7z
popd
exit /b 0

:fail
set "BUILD_EXIT=%ERRORLEVEL%"
if "%BUILD_EXIT%"=="0" set "BUILD_EXIT=1"
echo Build failed.
popd
exit /b %BUILD_EXIT%
