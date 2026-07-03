# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Proxy Manager"""

from pathlib import Path
from PyInstaller.building.build_main import Analysis, COLLECT, EXE, PYZ
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH)

datas = []
datas += collect_data_files("customtkinter")
datas += collect_data_files("pproxy")

assets_dir = ROOT / "assets"
if assets_dir.exists():
    datas += [(str(assets_dir), "assets")]

hidden = [
    *collect_submodules("proxy_manager"),
    "PIL._tkinter_finder",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageTk",
    "pproxy",
    "pproxy.proto",
    "pproxy.server",
    "pproxy.cipher",
    "pproxy.plugin",
    "psutil",
    "psutil._pslinux",
    "configparser",
    "tarfile",
    "urllib.request",
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "pystray",
    # setuptools/pkg_resources (runtime hook pyi_rth_pkgres)
    "platformdirs",
    "jaraco.text",
    "jaraco.functools",
    "jaraco.context",
    "more_itertools",
    "packaging",
    "packaging.version",
    "packaging.requirements",
    "packaging.specifiers",
    "packaging.markers",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "IPython",
        "notebook",
        "pytest",
        "sphinx",
    ],
    noarchive=False,
)

a_worker = Analysis(
    [str(ROOT / "proxy_manager" / "pproxy_worker.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=collect_data_files("pproxy"),
    hiddenimports=[
        "pproxy",
        "pproxy.proto",
        "pproxy.server",
        "pproxy.cipher",
        "pproxy.plugin",
        "platformdirs",
        "jaraco.text",
        "jaraco.functools",
        "jaraco.context",
        "more_itertools",
        "packaging",
        "packaging.version",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "customtkinter", "PIL", "pystray"],
    noarchive=False,
)

MERGE((a, "main", "proxy-manager"), (a_worker, "worker", "pproxy-worker"))

pyz = PYZ(a.pure)
pyz_worker = PYZ(a_worker.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="proxy-manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

exe_worker = EXE(
    pyz_worker,
    a_worker.scripts,
    [],
    exclude_binaries=True,
    name="pproxy-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    exe_worker,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="proxy-manager",
)
