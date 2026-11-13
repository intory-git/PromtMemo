# -*- mode: python ; coding: utf-8 -*-
"""Сборка Prompt Manager.

Windows/macOS/Linux:

    python -m pip install -r requirements-dev.txt
    python -m PyInstaller promptmanager.spec --noconfirm

Результат — каталог dist/PromptManager с исполняемым файлом и интерфейсом
внутри _internal/ui (pywebview находит его через sys._MEIPASS).
"""

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("ui", "ui")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "tools", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PromptManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name="PromptManager",
)
