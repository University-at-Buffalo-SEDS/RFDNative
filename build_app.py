#!/usr/bin/env python3
"""Install a local Finder-launchable bundle using the installed Python/Tk."""
from pathlib import Path
import argparse
import plistlib
import shutil
import sys
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--regenerate-icon', action='store_true', help='requires Swift and macOS image tools')
args = parser.parse_args()
if sys.platform != 'darwin':
    parser.error('This builds a macOS bundle; use your platform run script instead.')

source = Path(__file__).resolve().parent
assets = source / 'assets'
assets.mkdir(exist_ok=True)
icon = assets / 'AppIcon.png'
if args.regenerate_icon or not icon.exists() or not (assets / 'AppIcon.icns').exists():
    subprocess.run(['swift', str(source / 'make_icon.swift'), str(icon)], check=True)
    iconset = assets / 'AppIcon.iconset'
    iconset.mkdir(exist_ok=True)
    for pixels in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f'icon_{pixels}x{pixels}' + ('@2x' if scale == 2 else '') + '.png'
            subprocess.run(['sips', '-z', str(pixels * scale), str(pixels * scale), str(icon),
                            '--out', str(iconset / name)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(assets / 'AppIcon.icns')], check=True)
bundle = Path.home() / 'Applications' / 'RFD Native.app'
contents = bundle / 'Contents'
resources = contents / 'Resources' / 'app'
executables = contents / 'MacOS'
resources.mkdir(parents=True, exist_ok=True)
executables.mkdir(parents=True, exist_ok=True)
shutil.copy2(assets / 'AppIcon.icns', contents / 'Resources' / 'AppIcon.icns')
(resources / 'assets').mkdir(exist_ok=True)
shutil.copy2(icon, resources / 'assets' / 'AppIcon.png')
shutil.copy2(source / 'app.py', resources / 'app.py')
shutil.copytree(source / 'rfd_native', resources / 'rfd_native', dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('__pycache__'))
launcher = executables / 'RFDNative'
launcher.write_text(f'''#!{sys.executable}
from pathlib import Path
import runpy, sys, traceback
base = Path(__file__).resolve().parents[1] / 'Resources' / 'app'
logdir = Path.home() / 'Library' / 'Logs' / 'RFD Native'
logdir.mkdir(parents=True, exist_ok=True)
sys.stdout = sys.stderr = (logdir / 'launcher.log').open('a', buffering=1)
sys.path.insert(0, str(base))
try:
    runpy.run_path(str(base / 'app.py'), run_name='__main__')
except BaseException:
    traceback.print_exc()
    raise
''')
launcher.chmod(0o755)
with (contents / 'Info.plist').open('wb') as fp:
    plistlib.dump({'CFBundleName': 'RFD Native', 'CFBundleDisplayName': 'RFD Native',
                  'CFBundleIdentifier': 'local.rylan.rfdnative', 'CFBundleExecutable': 'RFDNative',
                  'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '1.0.0',
                  'CFBundleIconFile': 'AppIcon.icns',
                  'CFBundleVersion': '1', 'NSHighResolutionCapable': True,
                  'LSMinimumSystemVersion': '12.0'}, fp)
bundle.touch()
print(bundle)
