@echo off
rem Double-click to status Brick Mapper on Windows.
setlocal
set "BRICK_CMD="
if exist "%~dp0command.bat" set "BRICK_CMD=%~dp0command.bat"
if not defined BRICK_CMD for /d %%D in ("%~dp0*") do if exist "%%~fD\command.bat" if exist "%%~fD\control.py" set "BRICK_CMD=%%~fD\command.bat"
if not defined BRICK_CMD goto missing
call "%BRICK_CMD%" status
exit /b %ERRORLEVEL%
:missing
echo Could not find command.bat and control.py next to this file or in a subfolder.
pause
exit /b 1
