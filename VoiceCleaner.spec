# PyInstaller spec for VoiceCleaner (one-folder build on Windows, .app bundle on macOS).
#
#   python tools/build_windows.py        (recommended on Windows: also copies models/)
#   python tools/build_mac.py            (recommended on macOS: also copies models/, makes the DMG)
#   pyinstaller VoiceCleaner.spec        (application only)
#
# One-folder rather than one-file: PyTorch is several hundred MB to GB, and a
# one-file build would unpack all of it to a temp folder on every launch.
# Models are NOT bundled inside the executable; they live in dist/VoiceCleaner/models
# so they can be updated or replaced independently of the application.

import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# DeepFilterNet loads its network module by name at runtime, and importing `df`
# for discovery fails outside the app's torchaudio shim, so list modules from disk.
_df_dir = Path(importlib.util.find_spec("df").origin).parent
df_modules = ["df"] + [f"df.{p.stem}" for p in _df_dir.glob("*.py") if p.stem != "__init__"]

MAC = sys.platform == "darwin"
soundcard_backend = "soundcard.coreaudio" if MAC else "soundcard.mediafoundation"
hiddenimports = df_modules + ["libdf", "sounddevice", "_sounddevice_data", "soundfile", "_soundfile_data", "psutil",
                              "soundcard", soundcard_backend, "cffi", "PySide6.QtSvg"]
# soundcard declares the WASAPI API in *.py.h files it reads at import time
datas = collect_data_files("soundcard", includes=["*.h"])
# Steamburger Studios brand assets: logo and fonts (SIL Open Font License)
datas += [("app/ui/assets/SteamburgerLogoIcon.svg", "app/ui/assets"), ("app/ui/assets/fonts", "app/ui/assets/fonts")]

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
    datas=datas,
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
    icon="assets/voicecleaner.icns" if MAC else "assets/voicecleaner.ico",
    # Windows version resource (Properties > Details); written by tools/release.py
    version="build/version_info.txt" if not MAC and Path("build/version_info.txt").exists() else None,
    console=False,
    disable_windowed_traceback=True,
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="VoiceCleaner")

if MAC:
    sys.path.insert(0, ".")
    from app.utils.config import APP_VERSION

    app = BUNDLE(
        coll,
        name="VoiceCleaner.app",
        icon="assets/voicecleaner.icns",
        bundle_identifier="com.steamburgerstudios.voicecleaner",
        version=APP_VERSION,
        info_plist={
            "CFBundleDisplayName": "VoiceCleaner",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHumanReadableCopyright": "Steamburger Studios. MIT License.",
            "LSMinimumSystemVersion": "12.0",
            "LSApplicationCategoryType": "public.app-category.music",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # follow Dark Mode
            # without this macOS silently delivers silence to the recorder
            "NSMicrophoneUsageDescription": "VoiceCleaner records from your microphone when you click Record.",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "WAVE Audio",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["com.microsoft.waveform-audio"],
            }],
        },
    )
