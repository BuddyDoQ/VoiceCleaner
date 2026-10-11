"""Apple Silicon GPU for voice re-synthesis: device choice, fallback, and identical output."""
import numpy as np
import pytest

from app.ai import model_manager as mm_mod
from app.ai import resynthesis
from app.utils import hardware
from app.utils.config import model_path
from app.utils.errors import ProcessingError
from app.utils.hardware import DeviceInfo

CPU = DeviceInfo("cpu", "cpu", "Apple M6")
MPS = DeviceInfo("mps", "mps", "Apple M6")
CUDA = DeviceInfo("cuda", "cuda:0", "NVIDIA GeForce RTX 5070 Ti")


def test_labels_and_gpu_flag():
    assert MPS.label == "Apple GPU (Apple M6)" and MPS.is_gpu
    assert CUDA.label == "NVIDIA GPU (GeForce RTX 5070 Ti)" and CUDA.is_gpu
    assert CPU.label == "CPU (Apple M6)" and not CPU.is_gpu


@pytest.mark.parametrize("pref, main, mps, expected", [
    ("auto", CPU, MPS, MPS),  # Apple Silicon: re-synthesis on the GPU, noise reduction stays on the CPU
    ("cpu", CPU, MPS, CPU),  # "CPU only" means CPU only
    ("auto", CPU, None, CPU),  # Intel Mac, Windows/Linux without a GPU
    ("auto", CUDA, None, CUDA),  # NVIDIA: unchanged, re-synthesis follows the main device
    ("cuda", CUDA, None, CUDA),
])
def test_select_resynth_device(monkeypatch, pref, main, mps, expected):
    monkeypatch.setattr(hardware, "mps_device", lambda: mps)
    assert hardware.select_resynth_device(pref, main) == expected


def test_manager_moves_resynthesis_with_the_menu_choice(monkeypatch):
    monkeypatch.setattr(hardware, "mps_device", lambda: MPS)
    monkeypatch.setattr(hardware, "cuda_device", lambda: None)
    monkeypatch.setattr(hardware, "cpu_name", lambda: "Apple M6")
    m = mm_mod.ModelManager("auto")
    assert m.device.kind == "cpu" and m.resynth_device.kind == "mps"
    unloads = []
    monkeypatch.setattr(m.resynth, "unload", lambda: unloads.append(1))
    m.set_device_preference("cpu")
    assert m.device.kind == "cpu" and m.resynth_device.kind == "cpu" and unloads  # GPU model released
    m.set_device_preference("auto")
    assert m.resynth_device.kind == "mps"


class _FakeVocoder:
    sample_rate = 44_100

    def __init__(self, fail_on: set[str]):
        self.fail_on, self.device, self.calls = fail_on, "mps", []

    def vocode(self, y):
        self.calls.append(self.device)
        if self.device in self.fail_on:
            raise RuntimeError("MPS backend: unsupported operation")
        return y


class _FakeManager:
    def __init__(self, vocoder, resynth_kind="mps"):
        self.vocoder = vocoder
        self.resynth_device = DeviceInfo(resynth_kind, resynth_kind, "x")

    def get_resynthesizer(self):
        return self.vocoder

    def resynth_fallback_to_cpu(self):
        self.vocoder.device = "cpu"
        self.resynth_device = CPU


def test_apple_gpu_failure_is_retried_on_the_cpu():
    voc = _FakeVocoder(fail_on={"mps"})
    man = _FakeManager(voc)
    x = (0.1 * np.sin(np.linspace(0, 200, 44_100))).astype(np.float32)[:, None]
    out = resynthesis.Resynthesis(man).process(x, 44_100, strength=1.0)
    assert out.shape == x.shape and np.all(np.isfinite(out))
    assert voc.calls[:2] == ["mps", "cpu"] and man.resynth_device.kind == "cpu"


def test_failure_on_the_cpu_too_is_a_friendly_error():
    voc = _FakeVocoder(fail_on={"mps", "cpu"})
    x = np.zeros((44_100, 1), np.float32)
    with pytest.raises(ProcessingError):
        resynthesis.Resynthesis(_FakeManager(voc)).process(x, 44_100, strength=1.0)


def test_cpu_failure_is_not_retried():
    voc = _FakeVocoder(fail_on={"cpu"})
    voc.device = "cpu"
    with pytest.raises(ProcessingError):
        resynthesis.Resynthesis(_FakeManager(voc, "cpu")).process(np.zeros((44_100, 1), np.float32), 44_100, 1.0)
    assert voc.calls == ["cpu"]


def test_apple_gpu_output_matches_the_cpu():
    """The real BigVGAN on the Apple GPU must sound the same as on the CPU."""
    torch = pytest.importorskip("torch")
    if not (hardware.mps_device() and torch.backends.mps.is_available()):
        pytest.skip("no Apple Silicon GPU")
    mdir = model_path(mm_mod.RESYNTH_FOLDER, "bigvgan_generator.pt")
    if not (mdir / "bigvgan_generator.pt").exists():
        pytest.skip("BigVGAN model not downloaded")
    t = np.arange(44_100) / 44_100
    x = (0.3 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)
    outs = {}
    for dev in ("cpu", "mps"):
        v = resynthesis.BigVGANResynthesizer(mdir)
        v.load(dev)
        outs[dev] = v.vocode(x)
        v.unload()
    err = outs["mps"] - outs["cpu"]
    agreement = 10 * np.log10(np.sum(outs["cpu"] ** 2) / max(np.sum(err ** 2), 1e-20))
    assert agreement > 60, f"Apple GPU output differs from the CPU ({agreement:.1f} dB)"
