import hashlib

import pytest

from app.ai import model_download
from app.ai.model_download import DownloadError, download_file_model
from app.utils import config


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    bundled, user = tmp_path / "bundled", tmp_path / "user"
    bundled.mkdir()
    monkeypatch.setenv("VOICECLEANER_MODELS", str(bundled))
    monkeypatch.setenv("VOICECLEANER_USER_MODELS", str(user))
    return bundled, user


def test_model_path_prefers_bundled_then_user(dirs):
    bundled, user = dirs
    assert config.model_path("BigVGAN-v2-44k", "bigvgan_generator.pt") == user / "BigVGAN-v2-44k"
    (bundled / "BigVGAN-v2-44k").mkdir()
    (bundled / "BigVGAN-v2-44k" / "bigvgan_generator.pt").write_bytes(b"x")
    assert config.model_path("BigVGAN-v2-44k", "bigvgan_generator.pt") == bundled / "BigVGAN-v2-44k"


def _fake_model(tmp_path, monkeypatch, payload: bytes, sha: str | None = None):
    src = tmp_path / "server" / "weights.bin"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(payload)
    digest = sha or hashlib.sha256(payload).hexdigest()
    monkeypatch.setitem(model_download.FILE_MODELS, "Fake", {
        "size": len(payload), "files": {"weights.bin": (src.as_uri(), digest)}})


def test_download_verifies_checksum_and_installs(dirs, tmp_path, monkeypatch):
    _, user = dirs
    payload = b"model-bytes" * 100_000
    _fake_model(tmp_path, monkeypatch, payload)
    seen = []
    dest = download_file_model("Fake", user, progress=seen.append)
    assert (dest / "weights.bin").read_bytes() == payload
    assert seen and seen[-1] == pytest.approx(1.0)
    assert not list(dest.glob("*.part"))
    # already installed and valid: nothing is downloaded again
    seen.clear()
    download_file_model("Fake", user, progress=seen.append)
    assert seen == []


def test_download_rejects_wrong_checksum(dirs, tmp_path, monkeypatch):
    _, user = dirs
    _fake_model(tmp_path, monkeypatch, b"tampered", sha="0" * 64)
    with pytest.raises(DownloadError):
        download_file_model("Fake", user)
    assert not (user / "Fake" / "weights.bin").exists()
    assert not list((user / "Fake").glob("*.part"))


def test_download_can_be_cancelled(dirs, tmp_path, monkeypatch):
    _, user = dirs
    _fake_model(tmp_path, monkeypatch, b"x" * 3_000_000)
    with pytest.raises(DownloadError):
        download_file_model("Fake", user, cancelled=lambda: True)
    assert not (user / "Fake" / "weights.bin").exists()


def test_manager_finds_downloaded_model(dirs, monkeypatch):
    _, user = dirs
    from app.ai.model_manager import RESYNTH_FOLDER, ModelManager

    mm = ModelManager("cpu")
    assert not mm.resynth_available
    d = user / RESYNTH_FOLDER
    d.mkdir(parents=True)
    (d / "config.json").write_text("{}")
    (d / "bigvgan_generator.pt").write_bytes(b"x")
    mm.refresh_model_paths()
    assert mm.resynth_available and mm.resynth.model_dir == d
