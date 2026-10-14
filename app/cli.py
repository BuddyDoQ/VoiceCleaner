"""Command line: enhance many recordings without opening the window.

    VoiceCleanerCLI --batch recordings\\ out\\ --preset Natural
    VoiceCleanerCLI --batch a.m4a b.mp4 call.mp3 out\\ --format mp3
    VoiceCleanerCLI --list-presets

On Windows ``VoiceCleanerCLI.exe`` (next to VoiceCleaner.exe) is the console program: the
terminal waits for it and shows its progress. ``VoiceCleaner --batch ...`` works too.
Each file goes through exactly what the Batch tab does: analysis, Smart settings (unless
``--no-smart``), enhancement, export. One damaged file never stops the others.

Exit status: 0 every file enhanced, 1 some failed, 2 nothing to do or bad arguments.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

USAGE_EXAMPLES = """examples:
  VoiceCleanerCLI --batch recordings out --preset Natural
  VoiceCleanerCLI --batch "Voice Memo 14.m4a" zoom_0.mp4 out --format mp3
  VoiceCleanerCLI --batch D:\\Podcast --recursive D:\\Podcast\\clean --no-smart --preset Podcast
  VoiceCleanerCLI --list-presets
"""


def attach_console() -> None:
    """VoiceCleaner.exe is a windowed program: write to the terminal it was started from."""
    if sys.platform != "win32" or (sys.stdout is not None and sys.stdout.isatty()):
        return
    try:
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        pass


def _parser() -> argparse.ArgumentParser:
    from app.audio.settings import DEFAULT_PRESET

    p = argparse.ArgumentParser(
        prog="VoiceCleanerCLI", description="Enhance recordings with VoiceCleaner, without the window.",
        epilog=USAGE_EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--batch", nargs="+", metavar="PATH",
                   help="input files and/or folders, then the output folder (created if needed)")
    p.add_argument("--preset", default=DEFAULT_PRESET, help=f"built-in or your own preset (default: {DEFAULT_PRESET})")
    p.add_argument("--no-smart", action="store_true",
                   help="use the preset as it is (by default Smart settings adapt it to each file)")
    p.add_argument("--format", choices=("wav", "mp3"), default="wav", help="output format (default: wav)")
    p.add_argument("--bits", choices=("16", "24", "32"), default="24", help="WAV bit depth (32 = float; default 24)")
    p.add_argument("--recursive", action="store_true", help="include recordings in subfolders of input folders")
    p.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto", help="processor (default: auto)")
    p.add_argument("--list-presets", action="store_true", help="list the presets and exit")
    return p


def collect_inputs(paths: list[Path], recursive: bool) -> list[Path]:
    """Recordings named on the command line, and those in folders (sorted by name)."""
    from app.audio.decode import AUDIO_EXTENSIONS

    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            found = p.rglob("*") if recursive else p.glob("*")
            out += sorted((q for q in found if q.is_file() and q.suffix.lower() in AUDIO_EXTENSIONS),
                          key=lambda q: str(q).lower())
        elif p.is_file():
            out.append(p)
        else:
            print(f"Not found: {p}", file=sys.stderr)
    seen, unique = set(), []
    for p in out:
        key = os.path.normcase(str(p.resolve()))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


class _Context:
    """What the pipeline reports progress to: one updating line per file in a terminal."""

    def __init__(self, prefix: str):
        self.prefix, self.tty = prefix, bool(sys.stdout and sys.stdout.isatty())
        self._last = -1

    def progress(self, fraction: float, message: str = ""):
        pct = int(fraction * 100)
        if self.tty and pct != self._last:
            self._last = pct
            sys.stdout.write(f"\r{self.prefix} {pct:3d}%  {message[:40]:40s}")
            sys.stdout.flush()

    def check(self):
        pass

    @property
    def cancelled(self) -> bool:
        return False


def run(argv: list[str]) -> int:
    from app.audio import settings as presets
    from app.utils import user_presets

    args = _parser().parse_args(argv)
    user_presets.load_all()
    if args.list_presets:
        for name in presets.preset_names():
            kind = "your preset" if presets.is_user_preset(name) else presets.PRESETS[name].description
            print(f"{name:20s} {kind}")
        return 0
    if not args.batch or len(args.batch) < 2:
        _parser().print_usage(sys.stderr)
        print("--batch needs at least one input and an output folder", file=sys.stderr)
        return 2
    if args.preset not in presets.preset_names():
        print(f"Unknown preset \u201c{args.preset}\u201d. Presets: {', '.join(presets.preset_names())}", file=sys.stderr)
        return 2
    *inputs, out_dir = [Path(p) for p in args.batch]
    if out_dir.exists() and not out_dir.is_dir():
        print(f"The output must be a folder: {out_dir}", file=sys.stderr)
        return 2
    files = collect_inputs(inputs, args.recursive)
    if not files:
        print("No recordings found.", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    from app.ai.model_manager import ModelManager
    from app.utils.errors import friendly_message
    from app.utils.hardware import ensure_torch_imported
    from app.utils.logging import get_logger, setup_logging
    from app.workers.batch_worker import BatchOptions, enhance_file

    setup_logging(console=False)  # details go to the log file; the terminal gets one line per file
    log = get_logger("cli")
    print(f"VoiceCleaner: {len(files)} file{'s' if len(files) != 1 else ''}, preset {args.preset}"
          f"{'' if args.no_smart else ' + Smart settings'} -> {out_dir}")
    ensure_torch_imported()
    mm = ModelManager(args.device)
    opts = BatchOptions(settings=presets.from_preset(args.preset), smart=not args.no_smart, output_dir=out_dir,
                        fmt=args.format, bit_depth=args.bits)
    done = failed = 0
    t_all = time.perf_counter()
    for i, path in enumerate(files, 1):
        prefix = f"[{i}/{len(files)}] {path.name}"
        ctx = _Context(prefix)
        try:
            out, result = enhance_file(path, opts, mm, ctx)
            done += 1
            line = f"{prefix} -> {out.name}  ({result.realtime_factor:.1f}x realtime)"
            log.info("CLI: %s -> %s", path, out)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the others
            failed += 1
            line = f"{prefix}  FAILED: {friendly_message(exc)[1]}"
            log.warning("CLI: %s failed: %s", path, exc)
        if ctx.tty:
            sys.stdout.write("\r" + " " * (len(prefix) + 48) + "\r")
        print(line)
    print(f"{done} enhanced" + (f", {failed} failed" if failed else "") + f" in {time.perf_counter() - t_all:.0f} s")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    attach_console()
    for stream in (sys.stdout, sys.stderr):  # redirected to a file or a pipe: UTF-8, never a crash
        if stream is not None and not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:  # argparse: --help, bad arguments
        return int(exc.code or 0)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
