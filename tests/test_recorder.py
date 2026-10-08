import numpy as np
import pytest
import soundfile as sf

from app.audio.loader import AudioData, load_wav
from app.audio.recorder import RECORD_SR, InputSource, RecordingSession, combine_with_original

SR = RECORD_SR


def _session(tmp_path, gains=(1.0, 1.0)):
    srcs = [InputSource("mic-id", "Mic", "microphone", 1), InputSource("loop-id", "Speakers", "desktop", 2)]
    return RecordingSession(srcs, out_dir=tmp_path, gains=list(gains))


def _clicks(n, positions, width=48):
    x = np.zeros(n, np.float32)
    for p in positions:
        x[p : p + width] = np.hanning(width)
    return x


def test_two_sources_are_aligned_and_drift_corrected(tmp_path):
    s = _session(tmp_path)
    st0, st1 = s._states
    n = SR * 20
    events = [SR * k for k in range(1, 19)]
    a = _clicks(n, events)[:, None]
    # source 1 started 0.25 s later and its clock runs 0.05 % fast
    ratio = 1.0005
    late = int(0.25 * SR)
    n1 = int((n - late) * ratio)
    b = np.zeros((n1, 2), np.float32)
    for e in events:
        i = int(round((e - late) * ratio))
        if 0 <= i < n1 - 48:
            b[i : i + 48] = np.hanning(48)[:, None] * 0.5
    st0.start_time, st0.end_time = 100.0, 100.0 + n / SR
    st1.start_time, st1.end_time = 100.25, 100.25 + (n - late) / SR  # same wall-clock end
    mix = s._mix_aligned([(st0, a), (st1, b)])
    assert mix.shape[1] == 2
    # every event from source 1 lands within 1 ms of the reference event
    for e in events[2:]:
        seg = mix[e - 480 : e + 480, 0]
        peaks = np.flatnonzero(seg > 1.2)  # both sources overlap only if aligned
        assert peaks.size, f"event at {e / SR:.1f}s not aligned"
        assert abs((peaks.mean() + e - 480) - (e + 24)) < 48


def test_single_source_gain_applied(tmp_path):
    s = _session(tmp_path, gains=(0.5, 1.0))
    st0 = s._states[0]
    x = (np.ones((SR, 1), np.float32) * 0.4)
    sf.write(st0.part_path, x, SR, subtype="FLOAT")
    st0.frames, st0.max_peak, st0.start_time, st0.end_time = SR, 0.4, 1.0, 2.0
    res = s._finalize()
    y, _ = sf.read(res.path)
    assert res.duration == pytest.approx(1.0)
    assert np.allclose(y, 0.2, atol=1e-3)


def test_nothing_recorded_raises(tmp_path):
    from app.audio.recorder import RecordingError

    s = _session(tmp_path)
    with pytest.raises(RecordingError):
        s._finalize()


def test_silent_source_warns(tmp_path):
    s = _session(tmp_path)
    st0 = s._states[0]
    sf.write(st0.part_path, np.zeros((SR, 1), np.float32), SR, subtype="FLOAT")
    st0.frames, st0.start_time, st0.end_time = SR, 1.0, 2.0
    res = s._finalize()
    assert any("only silence" in w for w in res.warnings)


@pytest.fixture
def original(tmp_path, speech):
    p = tmp_path / "orig.wav"
    sf.write(p, np.stack([speech, speech], 1), SR, subtype="PCM_24")
    return load_wav(p)


@pytest.fixture
def take(tmp_path):
    p = tmp_path / "Recording 2026-01-01 10-00-00.wav"
    t = np.arange(SR * 2) / SR
    sf.write(p, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), SR)  # mono take
    return p


def test_combine_new_returns_take(original, take):
    assert combine_with_original(original, take, "new") == take


def test_combine_append(original, take):
    out = load_wav(combine_with_original(original, take, "append"))
    assert out.channels == 2
    assert out.frames == original.frames + SR * 2
    np.testing.assert_allclose(out.samples[: original.frames], original.samples, atol=1e-4)


def test_combine_overdub_at_position_with_latency(original, take):
    pos, latency = 1.5, 0.1
    out = load_wav(combine_with_original(original, take, "overdub", pos, latency))
    assert out.frames == original.frames  # take fits inside the original
    diff = np.asarray(out.samples[:, 0]) - np.asarray(original.samples[:, 0])
    active = np.flatnonzero(np.abs(diff) > 1e-3)
    assert active[0] == pytest.approx(pos * SR, abs=2)
    # latency trimmed from the head of the take: layer is 2.0 - 0.1 s long
    assert (active[-1] - active[0]) / SR == pytest.approx(1.9, abs=0.01)
    assert original.info.path.exists()  # original untouched


def test_combine_resamples_take_to_original_rate(tmp_path, take, speech):
    from scipy import signal

    p = tmp_path / "orig44.wav"
    sf.write(p, signal.resample_poly(speech, 147, 160), 44_100)
    orig = load_wav(p)
    out = load_wav(combine_with_original(orig, take, "append"))
    assert out.sample_rate == 44_100
    assert abs(out.frames - (orig.frames + 2 * 44_100)) <= 2


def test_list_sources_shape():
    pytest.importorskip("soundcard")
    from app.audio.recorder import list_sources

    try:
        mics, desk = list_sources()
    except Exception as exc:  # no audio subsystem on CI machines
        pytest.skip(str(exc))
    assert all(s.kind == "microphone" for s in mics)
    assert all(s.kind == "desktop" for s in desk)
