# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

native_suffix = (
    '.dll' if sys.platform == 'win32'
    else '.dylib' if sys.platform == 'darwin'
    else '.so'
)

native_binaries = [
    (str(path), 'engine_editor')
    for path in (Path(SPECPATH) / 'engine_editor').glob('*native.*')
    if path.suffix.lower() == native_suffix
]

# Analysis discovers imports and code modules starting at main.py.
a = Analysis(
    [str(Path(SPECPATH) / 'main.py')],
    pathex=[SPECPATH],
    binaries=native_binaries,
    # Include the window icon in the EXE's temporary resource directory.
    datas=[
        (str(Path(SPECPATH) / 'icon.ico'), '.'),
        (str(Path(SPECPATH) / 'sounds' / 'snap.wav'), 'sounds'),
        (str(Path(SPECPATH) / 'assets'), 'assets'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Keep the executable separate from the Python runtime and native DLLs.
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='engine-editor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# Collect the executable, DLLs, and data into an installable application folder.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='engine-editor',
)
