# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：打包前请生成 license.lic（见 CI 或 scripts/issue_license.py）。"""
from pathlib import Path

root = Path(SPECPATH)

datas = [
    (str(root / "config.yaml"), "."),
    (str(root / "frontend"), "frontend"),
]
lic = root / "license.lic"
if lic.is_file():
    datas.append((str(lic), "."))
key = root / ".license_key"
if key.is_file():
    datas.append((str(key), "."))
static = root / "static"
if static.is_dir():
    datas.append((str(static), "static"))

block_cipher = None

a = Analysis(
    [str(root / "backend" / "main.py")],
    pathex=[str(root / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="网优百宝箱",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)