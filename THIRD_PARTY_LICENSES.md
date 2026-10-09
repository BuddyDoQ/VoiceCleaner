# Third-party components

VoiceCleaner is distributed together with the following components. Each remains under
its own license; full license texts are included in the respective packages inside
`_internal/` of the built application.

| Component | Use | License |
|---|---|---|
| DeepFilterNet / DeepFilterNet3 weights | AI speech enhancement | MIT or Apache-2.0 |
| NVIDIA BigVGAN-v2 code (vendored) and weights | neural voice re-synthesis | MIT |
| PyTorch, torchaudio | neural network runtime | BSD-3-Clause |
| NVIDIA CUDA runtime libraries (GPU build only) | GPU acceleration | NVIDIA CUDA EULA (redistributable components) |
| Qt 6 / PySide6 | user interface | LGPL-3.0 (dynamically linked) |
| NumPy, SciPy | signal processing | BSD-3-Clause |
| libsndfile / soundfile | WAV and MP3 file I/O | LGPL-2.1 / BSD-3-Clause |
| LAME (inside libsndfile) | MP3 encoding | LGPL-2.0 |
| PortAudio / sounddevice | audio playback | MIT |
| psutil | memory detection | BSD-3-Clause |
| soundcard, cffi | recording (WASAPI microphone and desktop-audio loopback capture) | BSD-3-Clause / MIT |
| loguru, sympy, requests, appdirs, packaging | DeepFilterNet dependencies | MIT / BSD / Apache-2.0 |
| Bebas Neue, Syne, DM Mono fonts | interface typography | SIL Open Font License 1.1 (license texts in `app/ui/assets/fonts`) |

The Steamburger Studios name and burger logo are trademarks of Steamburger Studios
(<https://www.steamburgerstudios.com/>) and are not covered by the licenses above.

Qt is used under the LGPL: it is dynamically linked (separate DLLs in `_internal/`), so it
can be replaced by the user, as the LGPL requires.
