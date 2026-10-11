#!/bin/sh
# Runs ytmp4.py from source. For the standalone build, see build.sh.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$here/.venv/bin/python" ]; then
    echo "Setting up, one time only..."
    python3 -m venv "$here/.venv"
    "$here/.venv/bin/python" -m pip install -q -r "$here/requirements.txt"
fi
exec "$here/.venv/bin/python" "$here/ytmp4.py" "$@"
