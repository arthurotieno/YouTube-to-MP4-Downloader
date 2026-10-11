"""Build a single-file ytmp4 executable for the platform this runs on (PyInstaller can't cross-compile).

Run it through build.bat (Windows) or build.sh (macOS, Linux), which set up the virtual environment first.
The result is dist/ytmp4.exe on Windows and dist/ytmp4 on macOS and Linux.
"""
import os
import sys

import PyInstaller.__main__

here = os.path.dirname(os.path.abspath(__file__))
windows = os.name == "nt"
deno = os.path.join(os.path.dirname(sys.executable), "deno.exe" if windows else "deno")

args = [
    os.path.join(here, "ytmp4.py"),
    "--onefile", "--name", "ytmp4", "--noconfirm", "--clean",
    "--collect-all", "imageio_ffmpeg",   # bundles ffmpeg
    "--collect-all", "yt_dlp_ejs",       # yt-dlp's YouTube challenge solver scripts
    "--add-binary", f"{deno}{os.pathsep}.",
    "--add-data", f"{os.path.join(here, 'icon.ico')}{os.pathsep}.",
    "--add-data", f"{os.path.join(here, 'icon.png')}{os.pathsep}.",
    "--distpath", os.path.join(here, "dist"),
    "--workpath", os.path.join(here, "build"),
    "--specpath", os.path.join(here, "build"),
]
if windows:
    # A console program, so the command line works; the console is hidden when double-clicked.
    args += ["--console", "--hide-console", "hide-early", "--icon", os.path.join(here, "icon.ico")]

PyInstaller.__main__.run(args)
