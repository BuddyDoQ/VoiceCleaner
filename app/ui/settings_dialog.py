"""The Settings window: the options that change how VoiceCleaner looks and runs, not how a
recording sounds (that is the panel beside the waveforms).

Everything applies at once, as people expect from a settings window, so there is no OK or
Cancel: Close (or Esc) just closes it. Opened from the menu, or Ctrl+, (Cmd+, on a Mac,
where it also sits in the app menu as Settings…).
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QLabel, QRadioButton,
                               QVBoxLayout, QWidget)

from .widgets import label

APPEARANCE = (("auto", "Automatic", "Follows the system's light or dark setting"),
              ("day", "Day", "Always light"),
              ("night", "Night", "Always dark"))
FONTS = (("system", "System fonts", "The most readable on this computer"),
         ("brand", "Brand fonts", "Bebas Neue, Syne and DM Mono, as on the Steamburger Studios website"))


def _devices() -> tuple:
    if sys.platform == "darwin":  # no NVIDIA GPUs on a Mac; the Apple GPU runs re-synthesis
        return (("auto", "Automatic", "The Apple GPU for voice re-synthesis"),
                ("cpu", "CPU only", "Slower; leaves the GPU to other programs"))
    return (("auto", "Automatic", "An NVIDIA GPU when there is one, otherwise the CPU"),
            ("cpu", "CPU only", "Slower; leaves the GPU to other programs"),
            ("cuda", "NVIDIA GPU", "Always the NVIDIA graphics card"))


class SettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window_ = window
        self.setWindowTitle("Settings")
        self.setMinimumWidth(440)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 16)
        v.setSpacing(6)

        self.appearance = self._choices(v, "APPEARANCE", APPEARANCE, window.theme_pref, window.set_theme)
        self.fonts = self._choices(v, "TEXT", FONTS, window.config.extra.get("fonts", "system"), window.set_fonts)
        self.device = self._choices(v, "PROCESSING", _devices(), window.config.device_preference,
                                    window._set_device)

        v.addSpacing(10)
        v.addWidget(label("UPDATES", "section"))
        self.updates = QCheckBox("Check for updates when VoiceCleaner starts")
        self.updates.setChecked(bool(window.config.extra.get("update_auto", True)))
        self.updates.toggled.connect(lambda on: window.config.extra.__setitem__("update_auto", on))
        v.addWidget(self.updates)

        v.addSpacing(12)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        v.addWidget(buttons)

    def _choices(self, v, title: str, options, current: str, apply) -> QButtonGroup:
        v.addSpacing(10)
        v.addWidget(label(title, "section"))
        group = QButtonGroup(self)
        for key, text, hint in options:
            row = QWidget()
            r = QVBoxLayout(row)
            r.setContentsMargins(0, 2, 0, 2)
            r.setSpacing(0)
            b = QRadioButton(text)
            b.setChecked(key == current)
            b.toggled.connect(lambda on, k=key: on and apply(k))
            b.setAccessibleDescription(hint)
            group.addButton(b)
            r.addWidget(b)
            h = QLabel(hint)
            h.setProperty("role", "faint")
            h.setContentsMargins(26, 0, 0, 0)
            h.setWordWrap(True)
            h.setTextInteractionFlags(Qt.NoTextInteraction)
            r.addWidget(h)
            v.addWidget(row)
        return group

    def refresh(self):
        """Show the current choices (they can also change from elsewhere, e.g. the teleprompter)."""
        w = self.window_
        for group, options, current in ((self.appearance, APPEARANCE, w.theme_pref),
                                        (self.fonts, FONTS, w.config.extra.get("fonts", "system")),
                                        (self.device, _devices(), w.config.device_preference)):
            for b, (key, *_rest) in zip(group.buttons(), options):
                b.blockSignals(True)
                b.setChecked(key == current)
                b.blockSignals(False)
        self.updates.blockSignals(True)
        self.updates.setChecked(bool(w.config.extra.get("update_auto", True)))
        self.updates.blockSignals(False)

