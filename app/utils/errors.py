"""User-facing error types.

Every error that can reach the UI carries a short, human readable message.
Stack traces go to the log file only.
"""
from __future__ import annotations


class VoiceCleanerError(Exception):
    """Base class for errors with a message suitable for end users."""

    title = "Something went wrong"

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.user_message = message
        self.detail = detail


class AudioLoadError(VoiceCleanerError):
    title = "Could not open audio file"


class UnsupportedFormatError(AudioLoadError):
    title = "Unsupported audio format"


class EmptyAudioError(AudioLoadError):
    title = "The file contains no audio"


class InsufficientMemoryError(VoiceCleanerError):
    title = "Not enough memory"


class ModelUnavailableError(VoiceCleanerError):
    title = "AI model not available"


class ProcessingError(VoiceCleanerError):
    title = "Processing failed"


class ExportError(VoiceCleanerError):
    title = "Could not save the file"


class CancelledError(VoiceCleanerError):
    title = "Cancelled"

    def __init__(self):
        super().__init__("Processing was cancelled.")


def friendly_message(exc: BaseException) -> tuple[str, str]:
    """Return ``(title, message)`` for any exception, never a stack trace."""
    if isinstance(exc, VoiceCleanerError):
        return exc.title, exc.user_message
    if isinstance(exc, MemoryError):
        return InsufficientMemoryError.title, (
            "The computer ran out of memory while processing this file. "
            "Close other applications or try a shorter recording."
        )
    if isinstance(exc, PermissionError):
        return "Permission denied", (
            "Windows did not allow access to the file. It may be open in another "
            "program, or the folder may be read-only."
        )
    if isinstance(exc, OSError):
        return "File system error", f"A file operation failed: {exc.strerror or exc}"
    return "Unexpected error", (
        "An unexpected problem occurred. Details were written to the log file "
        "(Help → Open Log Folder)."
    )
