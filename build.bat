@echo off
rem Builds dist\ytmp4.exe, a single portable executable (Python, yt-dlp, ffmpeg and Deno included).
setlocal
set "HERE=%~dp0"
if not exist "%HERE%.venv\Scripts\python.exe" (
    python -m venv "%HERE%.venv" || exit /b 1
)
"%HERE%.venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -U -r "%HERE%requirements.txt" pyinstaller || exit /b 1
"%HERE%.venv\Scripts\python.exe" "%HERE%build.py"
