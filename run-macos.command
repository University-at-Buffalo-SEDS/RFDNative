#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo 'Run setup-macos.command first.' >&2
    exit 1
fi
exec .venv/bin/python app.py
