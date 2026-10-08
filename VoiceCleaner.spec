# PyInstaller spec for VoiceCleaner (Windows, one-folder build).
#
#   python tools/build_windows.py        (recommended: also copies models/)
#   pyinstaller VoiceCleaner.spec        (application only)
#
# One-folder rather than one-file: PyTorch is several hundred MB to GB, and a
# one-file build would unpack all of it to a temp folder on every launch.
# Models are NOT bundled inside the executable; they live in dist/VoiceCleaner/models
# so they can be updated or replaced independently of the application.

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hiddenimports = (
    collect_submodules("df")  # DeepFilterNet loads its network module by name at runtime
    + ["libdf", "sounddevice", "_sounddevice_data", "soundfile", "_soundfile_data", "psutil"]
)

excludes = [
    "tkinter", "matplotlib", "pytest", "pyloudnorm", "pystoi", "librosa", "numba", "llvmlite",
    "IPython", "jupyter", "notebook", "pandas",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtPdf",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "torch.utils.tensorboard", "torchvision",
]

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoiceCleaner",
    icon="assets/voicecleaner.ico",
    console=False,
    disable_windowed_traceback=True,
    upx=False,
    version=None,
)

coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="VoiceCleaner")
