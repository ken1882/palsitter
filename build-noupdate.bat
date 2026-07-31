@echo off
setlocal
set "PALSITTER_BUILD_VARIANT=noupdate"
call "%~dp0build.bat"
exit /b %ERRORLEVEL%
