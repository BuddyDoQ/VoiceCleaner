"""The command line: VoiceCleanerCLI --batch IN... OUT [--preset ...]."""
import numpy as np
import soundfile as sf

from app import cli


def _speech(path, seconds=2.0, sr=48000):
    t = np.arange(int(sr * seconds)) / sr
    x = 0.3 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t))
    x += 0.01 * np.random.default_rng(0).standard_normal(t.size)
    sf.write(path, x.astype(np.float32), sr, subtype="PCM_24")
    return path


def test_list_presets(capsys):
    assert cli.run(["--list-presets"]) == 0
    out = capsys.readouterr().out
    assert "Natural" in out and "Clean Voice" in out


def test_bad_arguments_exit_2(tmp_path, capsys):
    assert cli.run(["--batch", str(tmp_path)]) == 2  # no output folder
    _speech(tmp_path / "a.wav")
    assert cli.run(["--batch", str(tmp_path / "a.wav"), str(tmp_path / "out"), "--preset", "Nope"]) == 2
    assert "Unknown preset" in capsys.readouterr().err
    assert cli.run(["--batch", str(tmp_path / "missing.wav"), str(tmp_path / "out")]) == 2


def test_collect_inputs_folders_and_dedupe(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ("b.wav", "a.mp3", "notes.txt", "sub/c.m4a"):
        (tmp_path / name).write_bytes(b"x")
    flat = cli.collect_inputs([tmp_path, tmp_path / "b.wav"], recursive=False)
    assert [p.name for p in flat] == ["a.mp3", "b.wav"]
    deep = cli.collect_inputs([tmp_path], recursive=True)
    assert [p.name for p in deep] == ["a.mp3", "b.wav", "c.m4a"]


def test_batch_enhances_and_reports_failures(tmp_path, capsys):
    good = _speech(tmp_path / "talk.wav")
    bad = tmp_path / "broken.wav"
    bad.write_bytes(b"RIFF not really audio")
    out = tmp_path / "clean"
    code = cli.run(["--batch", str(good), str(bad), str(out), "--preset", "Natural", "--bits", "16"])
    text = capsys.readouterr().out
    assert code == 1 and "1 enhanced, 1 failed" in text and "broken.wav  FAILED" in text
    result = out / "talk_enhanced.wav"
    info = sf.info(str(result))
    assert info.subtype == "PCM_16" and abs(info.duration - 2.0) < 0.05
