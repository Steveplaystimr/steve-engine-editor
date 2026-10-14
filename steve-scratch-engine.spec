# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

# Analysis discovers imports and code modules starting at main.py.
a = Analysis(
    ['C:\\Users\\vlady\\Desktop\\recent-projects\\python\\mulitplayer\\main.py'],
    pathex=[],
    binaries=[],
    # Include the window icon in the EXE's temporary resource directory.
    datas=[(str(Path(SPECPATH) / 'icon.ico'), '.')],
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
    name='steve-scratch-engine',
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
    name='steve-scratch-engine',
)
