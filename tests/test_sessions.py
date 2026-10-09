import pytest

from app import sessions
from app.sessions import MAX_NAME_LENGTH, SessionError, create, open_session, rename, sanitize_name, validate_name


def test_sanitize_removes_invalid_and_limits_length():
    assert sanitize_name('My: "Podcast" / Ep?1') == "My Podcast Ep1"
    assert len(sanitize_name("x" * 100)) == MAX_NAME_LENGTH
    assert sanitize_name("  spaced   out  ") == "spaced out"
    assert sanitize_name("trailing dots...") == "trailing dots"
    assert sanitize_name("CON") == "CON_"  # reserved device name on Windows


def test_validate_messages():
    assert validate_name("") is not None
    assert validate_name("a" * (MAX_NAME_LENGTH + 1)) is not None
    assert validate_name("bad|name") is not None
    assert validate_name("Good Name 01") is None


def test_create_layout_and_metadata(tmp_path):
    s = create("Interview With Sam", tmp_path)
    assert s.path == tmp_path / "Interview With Sam"
    assert (s.path / "session.json").exists()
    assert s.recordings_dir.is_dir() and s.exports_dir.is_dir()
    with pytest.raises(SessionError):
        create("Interview With Sam", tmp_path)


def test_recording_names_count_up(tmp_path):
    s = create("Ep 12", tmp_path)
    first = s.recording_path()
    assert first.name == "Ep 12 - Rec 001.wav"
    first.write_bytes(b"x")
    assert s.recording_path("append").name == "Ep 12 - Rec 002 append.wav"


def test_export_names_short_and_unique(tmp_path):
    s = create("Ep 12", tmp_path)
    p = s.export_path(s.recordings_dir / "Ep 12 - Rec 003.wav")
    assert p.name == "Ep 12 - Rec 003 enhanced.wav"  # session prefix not repeated
    p.write_bytes(b"x")
    assert s.export_path(s.recordings_dir / "Ep 12 - Rec 003.wav").name == "Ep 12 - Rec 003 enhanced (2).wav"
    long = s.export_path(tmp_path / ("very long external file name " * 5 + ".wav"), ext=".mp3")
    assert len(long.name) <= MAX_NAME_LENGTH + 3 + sessions.MAX_STEM_LENGTH + len(" enhanced.mp3")


def test_rename_moves_folder_and_prefixes(tmp_path):
    s = create("Old Name", tmp_path)
    s.recording_path().write_bytes(b"x")
    s2 = rename(s, "New Name")
    assert not (tmp_path / "Old Name").exists()
    assert (s2.recordings_dir / "New Name - Rec 001.wav").exists()
    assert open_session(s2.path).name == "New Name"


def test_open_adopts_plain_folder_and_subfolder(tmp_path):
    d = tmp_path / "Some Folder"
    d.mkdir()
    s = open_session(d)
    assert s.name == "Some Folder" and (d / "session.json").exists()
    assert open_session(s.recordings_dir).path == d  # opening the Recordings folder opens the session


def test_list_and_default_name(tmp_path):
    create("A", tmp_path)
    create("B", tmp_path)
    assert {s.name for s in sessions.list_sessions(tmp_path)} == {"A", "B"}
    n1 = sessions.default_name(tmp_path)
    create(n1, tmp_path)
    assert sessions.default_name(tmp_path) != n1
