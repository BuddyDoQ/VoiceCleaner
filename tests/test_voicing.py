"""Voicing in the speech detector: a loud low noise without pitch inside or between words is
not speech; breathy words (no pitch, but breath in the upper mids) and whispering (no pitch
anywhere) stay speech."""
import numpy as np
from scipy import signal

SR = 48_000


def _reference_pitch(x, sr, frame, hop):
    """Brute-force pitch strength of one frame, as ``voicing`` defines it."""
    k = max(1, int(sr // 8000))
    dr = sr / k
    sos = signal.butter(4, [70.0, 1000.0], "bandpass", fs=sr, output="sos")
    y = signal.sosfilt(sos, x.astype(np.float64))[::k]
    win, lo, hi = int(0.04 * dr), int(dr / 400), int(dr / 70) + 1
    a = int(round(frame * hop / k))
    seg = np.pad(y, (0, win + hi))[a : a + win + hi]
    f = seg[:win]
    return max(np.dot(f, seg[L : L + win]) / np.sqrt(np.dot(f, f) * np.dot(seg[L : L + win], seg[L : L + win]) + 1e-20)
               for L in range(lo, hi))


def test_voicing_tells_pitch_from_noise(speech):
    from app.audio.analyzer import FRAME_HOP_S, VOICED_CORR, analyze, voicing

    hop = int(FRAME_HOP_S * SR)
    an = analyze(speech[:, None], SR)
    p = voicing(speech, SR, hop, an.speech_mask.size)
    assert np.mean(p[an.speech_mask] > VOICED_CORR) > 0.5
    noise = np.random.default_rng(0).standard_normal(SR * 3).astype(np.float32) * 0.1
    q = voicing(noise, SR, hop, 300)
    assert np.mean(q > VOICED_CORR) < 0.05


def test_voicing_streams_across_blocks():
    """Frames on both sides of an internal block boundary match the brute-force definition."""
    from app.audio.analyzer import FRAME_HOP_S, voicing

    hop = int(FRAME_HOP_S * SR)
    rng = np.random.default_rng(1)
    t = np.arange(SR * 36) / SR
    x = (np.sin(2 * np.pi * 150 * t) * (t % 2 < 1) + 0.3 * rng.standard_normal(t.size)).astype(np.float32)
    n = int(np.ceil(x.size / hop))
    p = voicing(x, SR, hop, n)
    boundary = 6 * (1 << 18) // hop  # first block ends here at 48 kHz
    for frame in (10, 150, boundary - 3, boundary - 1, boundary, boundary + 2, n - 5):
        assert abs(p[frame] - _reference_pitch(x, SR, frame, hop)) < 1e-3, frame


def _speech_with_rumble(speech, band=(40, 800)):
    """A loud noise without pitch, as long as a word, between phrases: a low rumble by
    default, breath with ``band=(1000, 5000)``."""
    rng = np.random.default_rng(2)
    x = speech.copy()
    a, b = int(4.0 * SR), int(4.6 * SR)  # the pause between the 2nd and 3rd phrase
    rumble = signal.sosfilt(signal.butter(2, list(band), "bandpass", fs=SR, output="sos"),
                            rng.standard_normal(b - a))
    level = np.sqrt(np.mean(speech[speech != 0] ** 2))
    x[a:b] += (rumble / np.sqrt(np.mean(rumble**2)) * level * 0.7).astype(np.float32)
    return x, (a, b)


def _features(x):
    from app.audio import analyzer as A
    from app.audio.dsp import EPS, analysis_fft_size, band_edges_bins, db, iter_power_frames

    nfft, hop = analysis_fft_size(SR), int(A.FRAME_HOP_S * SR)
    freqs = np.fft.rfftfreq(nfft, 1 / SR)
    sb, fb = band_edges_bins(freqs, 300, 4000), band_edges_bins(freqs, 80, 16000)
    lv, fl, ce = [], [], []
    for _, p in iter_power_frames(x, nfft, hop):
        lv.append(db(p[:, fb].sum(1)))
        q = p[:, sb] + EPS
        fl.append(np.exp(np.mean(np.log(q), 1)) / np.mean(q, 1))
        ce.append((p[:, fb] * freqs[fb]).sum(1) / (p[:, fb].sum(1) + EPS))
    lv, fl, ce = np.concatenate(lv), np.concatenate(fl), np.concatenate(ce)
    return lv, fl, A.voicing(x, SR, hop, lv.size), ce


def test_unpitched_noise_between_words_is_not_speech(speech):
    from app.audio.analyzer import FRAME_HOP_S, voice_activity

    x, (a, b) = _speech_with_rumble(speech)
    lv, fl, pitch, centre = _features(x)
    core = slice(int(a / SR / FRAME_HOP_S) + 20, int(b / SR / FRAME_HOP_S) - 20)  # away from the hangover
    old, _ = voice_activity(lv, fl)
    assert old[core].mean() > 0.8  # level and flatness alone take it for speech
    new, _ = voice_activity(lv, fl, pitch, centre)
    assert new[core].mean() == 0.0
    # the words around it are unchanged
    words = np.ones_like(old)
    words[core.start - 40 : core.stop + 40] = False
    assert np.array_equal(old[words], new[words])


def test_whispering_keeps_its_speech(speech):
    """No pitch anywhere: the voicing check stays out of it."""
    from app.audio.analyzer import voice_activity

    x, _ = _speech_with_rumble(speech)
    lv, fl, _pitch, centre = _features(x)
    old, _ = voice_activity(lv, fl)
    whisper, _ = voice_activity(lv, fl, np.zeros_like(lv), centre)
    assert np.array_equal(old, whisper)


def test_breathy_words_stay_speech(speech):
    """No pitch, but the sound sits in the upper mids like breath: a breathy or whispered
    word between pitched ones is kept (a low rumble in the same place is not)."""
    from app.audio.analyzer import FRAME_HOP_S, UNVOICED_MAX_CENTROID_HZ, voice_activity

    x, (a, b) = _speech_with_rumble(speech, band=(1000, 5000))
    lv, fl, pitch, centre = _features(x)
    core = slice(int(a / SR / FRAME_HOP_S) + 20, int(b / SR / FRAME_HOP_S) - 20)
    old, _ = voice_activity(lv, fl)
    assert old[core].mean() > 0.8
    assert np.median(pitch[core]) < 0.6 and np.median(centre[core]) > UNVOICED_MAX_CENTROID_HZ
    new, _ = voice_activity(lv, fl, pitch, centre)
    assert np.array_equal(old, new)


def test_short_unpitched_sounds_stay_speech(speech):
    """A consonant cluster (well under UNVOICED_SPAN_S) inside a phrase is still speech."""
    from app.audio.analyzer import voice_activity

    lv, fl, pitch, centre = _features(speech)
    p = pitch.copy()
    p[200:220] = 0.0  # 200 ms without pitch in the middle of the first phrase
    old, _ = voice_activity(lv, fl)
    new, _ = voice_activity(lv, fl, p, centre)
    assert np.array_equal(old, new)


def _qc(original, processed):
    from app.audio.analyzer import analyze
    from app.audio.quality import check
    from app.audio.settings import from_preset

    an = analyze(original[:, None], SR)
    rep, _ = check(original[:, None], processed[:, None], SR, an, from_preset("Clean Voice"))
    return rep


def test_removing_a_noise_beside_words_is_not_speech_loss(speech):
    """The noise fills the detector's hangover beside the words; taking it out (the
    result is the clean speech) must not read as lost speech."""
    x, _ = _speech_with_rumble(speech)
    rep = _qc(x, speech.copy())
    assert "speech_loss" not in {i.code for i in rep.issues}, rep.stats


def test_lost_words_are_still_caught(speech):
    """Real loss: the middle of two phrases removed."""
    processed = speech.copy()
    for t in (1.1, 3.1, 5.1):
        processed[int(t * SR) : int((t + 0.45) * SR)] *= 0.01
    rep = _qc(speech, processed)
    assert "speech_loss" in {i.code for i in rep.issues}, rep.stats
