import numpy as np
import pytest
import soundfile as sf

from app.audio import loudness
from app.audio.analyzer import analyze
from app.audio.loader import AudioData, load_wav
from app.audio.pipeline import EnhancementPipeline
from app.audio.settings import PRESETS, ProcessingSettings, auto_configure, from_preset
from app.export.mp3_exporter import export_mp3, mp3_supported
from app.export.wav_exporter import ExportOptions, export_wav
from app.utils.errors import CancelledError, ExportError

SR = 48_000


def _rms_db(x):
    return 10 * np.log10(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-20)


def _run(x, settings=None, mm=None, channels=1):
    samples = x[:, None] if x.ndim == 1 else x
    audio = AudioData(samples.astype(np.float32), SR)
    analysis = analyze(audio.samples, SR)
    settings = settings or auto_configure("Clean Voice", analysis).settings
    return EnhancementPipeline(mm).run(audio, analysis, settings), analysis


def test_dsp_only_pipeline_targets_and_safety(noisy):
    x, clean, _ = noisy
    s = from_preset("Clean Voice").copy(use_ai=False)
    result, analysis = _run(x, s)
    y = result.samples
    assert y.shape == (x.shape[0], 1)
    assert np.all(np.isfinite(y))
    # on target, unless reaching it would need more than 12 dB of limiting (this test
    # signal is a very peaky pulse train); then the pipeline deliberately stays below
    lufs = loudness.integrated_loudness(y, SR)
    assert s.target_lufs - 1.5 < lufs < s.target_lufs + 0.3
    assert loudness.true_peak_db(y, SR) <= s.peak_ceiling_dbtp + 0.05
    # noise in the leading pause reduced relative to speech (measured without the
    # mastering stage, whose limiting of this peaky signal would skew the ratio)
    restored, _ = _run(x, s.copy(normalize_loudness=False, compressor_amount=0.0))
    y2 = restored.samples[:, 0]
    lead = slice(int(0.1 * SR), int(0.7 * SR))
    body = slice(int(2.8 * SR), int(3.8 * SR))
    snr_in = _rms_db(x[body]) - _rms_db(x[lead])
    snr_out = _rms_db(y2[body]) - _rms_db(y2[lead])
    assert snr_out > snr_in + 8
    assert result.enhanced_metrics.snr_db > result.original_metrics.snr_db


@pytest.mark.parametrize("preset", list(PRESETS))
def test_every_preset_runs(noisy, preset):
    x, _, _ = noisy
    s = from_preset(preset).copy(use_ai=False)
    result, _ = _run(x, s)
    assert np.all(np.isfinite(result.samples))
    assert loudness.true_peak_db(result.samples, SR) <= s.peak_ceiling_dbtp + 0.05


def test_dual_mono_stereo_keeps_layout_and_loudness(noisy):
    x, _, _ = noisy
    st = np.stack([x, x], axis=1)
    s = from_preset("Clean Voice").copy(use_ai=False)
    result, analysis = _run(st, s)
    assert analysis.stereo.mode == "dual_mono"
    assert result.samples.shape == st.shape
    np.testing.assert_allclose(result.samples[:, 0], result.samples[:, 1])
    # identical channels: within the limiter-protected range of the mono case
    assert s.target_lufs - 1.5 < loudness.integrated_loudness(result.samples, SR) < s.target_lufs + 0.3


def test_one_sided_stereo_is_centred(noisy):
    x, _, _ = noisy
    st = np.stack([x, x * 1e-4], axis=1)
    result, analysis = _run(st, from_preset("Natural").copy(use_ai=False))
    assert analysis.stereo.mode == "left_only"
    assert abs(_rms_db(result.samples[:, 0]) - _rms_db(result.samples[:, 1])) < 0.1
    assert any("left channel" in n for n in result.notes)


def test_noise_only_file_is_not_boosted():
    rng = np.random.default_rng(3)
    noise = (rng.standard_normal(SR * 5) * 0.003).astype(np.float32)
    result, analysis = _run(noise, from_preset("Clean Voice").copy(use_ai=False))
    assert not analysis.speech_detected
    assert loudness.integrated_loudness(result.samples, SR) <= analysis.lufs + 1.0


def test_clean_audio_gets_light_processing(speech):
    analysis = analyze(speech[:, None], SR)
    s = auto_configure("Clean Voice", analysis).settings
    assert s.noise_reduction <= 0.2
    # restoration only (mastering gain/limiting would dominate the comparison)
    result, _ = _run(speech, s.copy(use_ai=False, normalize_loudness=False, compressor_amount=0.0))
    # level-matched, the waveform stays close to the input (no heavy-handed processing)
    y = result.samples[:, 0]
    g = np.dot(y, speech) / np.dot(speech, speech)
    assert _rms_db(y - g * speech) < _rms_db(g * speech) - 12


def test_cancellation(noisy):
    x, _, _ = noisy

    class Ctx:
        def progress(self, f, m=""):
            pass

        def check(self):
            raise CancelledError()

    audio = AudioData(x[:, None], SR)
    analysis = analyze(audio.samples, SR)
    with pytest.raises(CancelledError):
        EnhancementPipeline(None).run(audio, analysis, from_preset("Clean Voice").copy(use_ai=False), Ctx())


def test_settings_roundtrip():
    s = from_preset("Podcast")
    assert ProcessingSettings.from_dict(s.to_dict()) == s


def test_long_audio_chunked_disk_backed(noisy, tmp_path):
    x, _, _ = noisy
    long_x = np.tile(x, 8)  # ~48 s, several chunks
    path = tmp_path / "long.wav"
    sf.write(path, long_x, SR, subtype="FLOAT")
    audio = load_wav(path, force_disk=True)
    analysis = analyze(audio.samples, SR)
    s = from_preset("Clean Voice").copy(use_ai=False, quality_control=False)
    chunked = EnhancementPipeline(None, chunk_seconds=10.0, overlap_seconds=0.5).run(audio, analysis, s)
    whole = EnhancementPipeline(None, chunk_seconds=1000.0).run(audio, analysis, s)
    assert isinstance(chunked.samples, np.memmap)
    a, b = np.asarray(chunked.samples[:, 0]), np.asarray(whole.samples[:, 0])
    # chunked processing matches one-pass processing closely everywhere, including at the seams
    assert _rms_db(a - b) < _rms_db(b) - 25
    for k in range(1, 5):
        seam = slice(k * 9 * SR, k * 9 * SR + SR)  # around each chunk boundary
        assert _rms_db(a[seam] - b[seam]) < _rms_db(b[seam]) - 20


def test_wav_export_bit_depths_and_rate(noisy, tmp_path):
    x, _, _ = noisy
    data = x[:, None] * 0.5
    for depth, subtype in (("16", "PCM_16"), ("24", "PCM_24"), ("32f", "FLOAT")):
        p = export_wav(data, SR, tmp_path / f"out_{depth}.wav", ExportOptions(depth))
        info = sf.info(p)
        assert info.subtype == subtype and info.samplerate == SR and info.frames == x.shape[0]
    p = export_wav(data, SR, tmp_path / "out_44k.wav", ExportOptions("24", 44_100))
    info = sf.info(p)
    assert info.samplerate == 44_100
    assert abs(info.frames - x.shape[0] * 44_100 / SR) <= 2


def test_export_never_overwrites_source(noisy, tmp_path):
    x, _, _ = noisy
    src = tmp_path / "source.wav"
    sf.write(src, x, SR)
    with pytest.raises(ExportError):
        export_wav(x[:, None], SR, src, ExportOptions(), source_path=src)
    assert sf.info(src).frames == x.shape[0]


@pytest.mark.skipif(not mp3_supported(), reason="libsndfile without MP3")
def test_mp3_export(noisy, tmp_path):
    x, _, _ = noisy
    p = export_mp3(x[:, None] * 0.5, SR, tmp_path / "out.mp3")
    assert p.exists() and p.stat().st_size > 1000


@pytest.fixture(scope="module")
def model_manager():
    from app.ai.model_manager import ModelManager

    mm = ModelManager("cpu")
    if not mm.model.is_installed():
        pytest.skip("DeepFilterNet3 model not installed")
    return mm


def test_ai_pipeline_improves_noisy_speech(noisy, model_manager):
    x, clean, _ = noisy
    result, analysis = _run(x, mm=model_manager)
    assert any("AI" in st for st in result.stages)
    assert result.enhanced_metrics.snr_db > result.original_metrics.snr_db + 10
    assert np.all(np.isfinite(result.samples))
