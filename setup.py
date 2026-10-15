#!/usr/bin/env python3
"""Create a local runtime; never install packages into the system Python."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--macos-app', action='store_true', help='also install the Finder app')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error('Python 3.10 or newer is required (Python 3.12 recommended).')
    if args.macos_app and sys.platform != 'darwin':
        parser.error('--macos-app is only available on macOS')
    try:
        import tkinter
        tkinter.Tcl()
    except (ImportError, RuntimeError) as exc:
        parser.error(f'Tkinter is required: {exc}. See README.md for platform prerequisites.')
    root = Path(__file__).resolve().parent
    environment = root / '.venv'
    if not environment.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    subprocess.run([str(python), '-c', 'import tkinter; tkinter.Tcl()'], check=True)
    if sys.platform == 'win32':
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root / 'requirements.txt')], check=True)
    if args.macos_app:
        subprocess.run([str(python), str(root / 'build_app.py')], check=True)
    print('Setup complete. Start with run-windows.cmd, run-macos.command, or run-linux.sh.')


if __name__ == '__main__':
    main()
