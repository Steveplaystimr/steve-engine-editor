# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

native_binaries = [
    (str(path), 'engine_editor')
    for path in (Path(SPECPATH) / 'engine_editor').glob('*native.*')
    if path.suffix.lower() in {'.dll', '.so', '.dylib'}
]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=native_binaries,
    datas=[('icon.ico', '.'), ('sounds/snap.wav', 'sounds')],
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
    a.binaries,
    a.datas,
    [],
    name='engine-editor-fixed',
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
    icon=['icon.ico'],
)
