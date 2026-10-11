"""VoiceCleaner application package."""
import os as _os
import sys as _sys

if _sys.platform == "darwin":
    # Must be set before PyTorch is imported: an operation the Apple GPU (MPS) does not
    # support then runs on the CPU instead of failing the whole re-synthesis.
    _os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
