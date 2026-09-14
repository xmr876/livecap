# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one onedir bundle holding both exes.

    livecap.exe      windowed GUI (double-click this)
    livecap-cli.exe  console build with the full command line

PyTorch and transformers are deliberately excluded: at runtime the app only
needs CTranslate2, which keeps the bundle ~1 GB instead of ~4 GB.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

PACKAGING = Path(SPECPATH).resolve()
PROJECT = PACKAGING.parent
SITE = Path(sys.prefix) / "Lib" / "site-packages"

datas: list = []
binaries: list = []
hiddenimports: list = [
    "livecap.gui", "livecap.main", "livecap.overlay", "livecap.settings",
    "cffi", "_cffi_backend", "soundcard.mediafoundation", "requests",
]

for package in ("faster_whisper", "ctranslate2", "onnxruntime", "soundcard", "tokenizers"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception as exc:  # pragma: no cover - build-time diagnostics
        print(f"[spec] collect_all({package}) failed: {exc}")
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# cuDNN / cuBLAS / nvrtc DLLs from the nvidia-*-cu12 wheels: CTranslate2 needs them
for name in ("cudnn", "cublas", "cuda_nvrtc"):
    source = SITE / "nvidia" / name / "bin"
    if source.is_dir():
        binaries += [(str(dll), f"nvidia/{name}/bin") for dll in source.glob("*.dll")]
        print(f"[spec] + {len(list(source.glob('*.dll')))} dlls from nvidia/{name}")

EXCLUDES = [
    "torch", "transformers", "tensorflow", "matplotlib", "scipy", "pandas",
    "tkinter", "IPython", "notebook", "pytest", "setuptools._distutils",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.Qt3DCore",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtDesigner", "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtHelp",
]

ICON = str(PACKAGING / "app.ico")
icon_arg = ICON if (PACKAGING / "app.ico").exists() else None


def analyse(entry: str):
    return Analysis(
        [str(PACKAGING / entry)],
        pathex=[str(PROJECT)],
        binaries=list(binaries),
        datas=list(datas),
        hiddenimports=list(hiddenimports),
        excludes=EXCLUDES,
        noarchive=False,
    )


a_gui = analyse("livecap_app.py")
a_cli = analyse("livecap_cli.py")
pyz_gui = PYZ(a_gui.pure)
pyz_cli = PYZ(a_cli.pure)

exe_gui = EXE(
    pyz_gui, a_gui.scripts, [],
    exclude_binaries=True,
    name="livecap",
    console=False,
    icon=icon_arg,
    version=None,
)
exe_cli = EXE(
    pyz_cli, a_cli.scripts, [],
    exclude_binaries=True,
    name="livecap-cli",
    console=True,
    icon=icon_arg,
)

# merge the two analyses; identical destinations collapse into one copy
seen = set()
merged_binaries = []
for entry in list(a_gui.binaries) + list(a_cli.binaries):
    if entry[0] not in seen:
        seen.add(entry[0])
        merged_binaries.append(entry)

# PyInstaller resolves DLLs through PATH, so an unrelated OpenSSL build (here:
# anaconda's Library\bin) can shadow Python's own libssl/libcrypto and break
# `import _ssl` inside the frozen app. Force Python's copies to win.
# TOC entries are (destination_name, source_path, typecode).
python_dll_dir = (Path(sys.base_prefix) / "DLLs").resolve()
python_dll_names = {p.name.lower() for p in python_dll_dir.glob("*") if p.is_file()}

filtered_binaries = []
dropped = []
for entry in merged_binaries:
    dest, src = entry[0], entry[1]
    if (Path(dest).parent == Path(".")
            and Path(dest).name.lower() in python_dll_names
            and Path(src).parent.resolve() != python_dll_dir):
        dropped.append(Path(src).name)
        continue
    filtered_binaries.append(entry)

present_root = {Path(e[0]).name.lower() for e in filtered_binaries if Path(e[0]).parent == Path(".")}
for dll in python_dll_dir.glob("*"):
    if dll.is_file() and dll.name.lower() not in present_root:
        filtered_binaries.append((dll.name, str(dll), "BINARY"))

if dropped:
    print(f"[spec] replaced shadowed DLLs with Python's own: {sorted(set(dropped))}")

seen_data = set()
merged_datas = []
for entry in list(a_gui.datas) + list(a_cli.datas):
    if entry[0] not in seen_data:
        seen_data.add(entry[0])
        merged_datas.append(entry)

COLLECT(
    exe_gui, exe_cli,
    filtered_binaries, merged_datas,
    strip=False, upx=False,
    name="livecap",
)
