#!/bin/sh
# Builds dist/ytmp4, a single portable executable (Python, yt-dlp, ffmpeg and Deno included).
# Needs Python 3.10+ with tkinter (Debian/Ubuntu: sudo apt install python3-venv python3-tk).
set -e
here="$(cd "$(dirname "$0")" && pwd)"
[ -x "$here/.venv/bin/python" ] || python3 -m venv "$here/.venv"
"$here/.venv/bin/python" -m pip install -q --disable-pip-version-check -U -r "$here/requirements.txt" pyinstaller
"$here/.venv/bin/python" "$here/build.py"
