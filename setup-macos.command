#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec "${RFD_PYTHON:-python3}" setup.py --macos-app
