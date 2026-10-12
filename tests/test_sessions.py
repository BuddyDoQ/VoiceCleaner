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


def test_original_recording_of_enhanced_takes(tmp_path):
    s = create("Show", tmp_path)
    rec = s.recording_path()
    rec.write_bytes(b"x")
    exp = s.export_path(rec)
    exp.write_bytes(b"y")
    assert sessions.original_recording(exp) == rec
    second = s.export_path(rec)  # "... enhanced (2).wav"
    second.write_bytes(b"y")
    assert sessions.original_recording(second) == rec
    assert sessions.original_recording(rec) == rec  # a recording is its own original
    # long names are shortened in export names
    long_rec = s.recordings_dir / f"{s.name} - {'A very long take name ' * 4}.wav"
    long_rec.write_bytes(b"x")
    assert sessions.original_recording(s.export_path(long_rec)) == long_rec
    # not from a session recording
    other = s.exports_dir / f"{s.name} - imported enhanced.wav"
    assert sessions.original_recording(other) is None
    assert sessions.original_recording(tmp_path / "loose.wav") is None


def test_previous_version_location(tmp_path):
    p = tmp_path / "Exports" / "a enhanced.wav"
    assert sessions.previous_version(p) == tmp_path / "Exports" / "Previous Versions" / "a enhanced.wav"
