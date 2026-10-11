@echo off
rem Runs ytmp4.py from source. For the standalone build, see build.bat.
setlocal
set "HERE=%~dp0"
if not exist "%HERE%.venv\Scripts\python.exe" (
    echo Setting up, one time only...
    python -m venv "%HERE%.venv" || exit /b 1
    "%HERE%.venv\Scripts\python.exe" -m pip install -q -r "%HERE%requirements.txt" || exit /b 1
)
"%HERE%.venv\Scripts\python.exe" "%HERE%ytmp4.py" %*
