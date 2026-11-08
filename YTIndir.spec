# GitHub Actions builds this spec on each target OS.
import os
import shutil
import sys
from pathlib import Path

root = Path(SPECPATH)
binaries = []
for tool in ('ffmpeg', 'ffprobe'):
    executable = shutil.which(tool)
    if not executable:
        raise RuntimeError(f'{tool} must be installed on the GitHub build runner')
    binaries.append((executable, 'resources/bin'))

analysis = Analysis(
    [str(root / 'youtube_indir.py')], pathex=[str(root)], binaries=binaries, datas=[],
    hiddenimports=['pgpy', 'imghdr'],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml',
              'PySide6.QtQuick', 'PySide6.QtPdf', 'tkinter'],
)
pyz = PYZ(analysis.pure)
if sys.platform == 'win32':
    # One executable contains Python, Qt, cryptography, FFmpeg and ffprobe.
    exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas,
              name='YTIndir', console=True, hide_console='hide-early',
              debug=False, strip=False, upx=False)
else:
    exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='YTIndir',
              console=False, debug=False, strip=False, upx=False)
    collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='YTIndir')
    if sys.platform == 'darwin':
        app = BUNDLE(collection, name='YT İndir.app', bundle_identifier='local.ytindir.desktop',
                     info_plist={'CFBundleDisplayName': 'YT İndir', 'NSHighResolutionCapable': True})
