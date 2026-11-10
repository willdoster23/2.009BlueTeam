@echo off
rem Windows version of command.zsh. Usage: command.bat start ^| stop ^| status
setlocal
set "BRICK_CONTROL_DIR=%~dp0"
set "BRICK_ACTION=%~1"
set "BRICK_RESULT=1"
set "BRICK_EXTRA="
if "%BRICK_MAP_NO_BROWSER%"=="1" set "BRICK_EXTRA=--no-browser"
set "BRICK_PYTHON="
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 9))" >nul 2>&1 && set "BRICK_PYTHON=py -3"
if not defined BRICK_PYTHON python -c "import sys; raise SystemExit(sys.version_info < (3, 9))" >nul 2>&1 && set "BRICK_PYTHON=python"
if not defined BRICK_PYTHON python3 -c "import sys; raise SystemExit(sys.version_info < (3, 9))" >nul 2>&1 && set "BRICK_PYTHON=python3"
if not defined BRICK_PYTHON goto nopython
%BRICK_PYTHON% "%BRICK_CONTROL_DIR%control.py" %BRICK_ACTION% %BRICK_EXTRA%
set "BRICK_RESULT=%ERRORLEVEL%"
goto finish
:nopython
echo Python 3.9 or newer was not found.
:finish
if not "%BRICK_RESULT%"=="0" echo If Python was not found, install Python 3.9 or newer from python.org and tick "Add python.exe to PATH".
if not defined BRICK_NO_PAUSE pause
exit /b %BRICK_RESULT%
