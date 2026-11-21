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

a = Analysis(
    [str(Path(SPECPATH) / 'game_player.py')],
    pathex=[SPECPATH],
    binaries=native_binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='steve-game-player',
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='steve-game-player',
)
