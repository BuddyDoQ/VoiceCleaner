"""The Enhance tab's take edits: played on both tracks (cuts skipped, silenced regions
silent), applied on export, and the playhead going to the start of a selection."""
import numpy as np
import pytest

from app.audio.compile import apply_edits

SR = 1000


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def player(qapp):
    from app.ui.audio_player import AudioPlayer

    p = AudioPlayer()
    p._sr = SR
    ramp = np.arange(4000, dtype=np.float32)[:, None] / 4000  # the value tells the frame
    p.set_source("original", ramp, SR)
    p.set_source("enhanced", ramp * 0.5, SR)
    p._gains = {s: 1.0 for s in p._gains}
    yield p
    p.shutdown()


def test_cuts_are_skipped(player):
    player.set_edits([(1.0, 2.0, "cut")])
    idx, nxt = player._positions(900, 300)
    assert idx[99] == 999 and idx[100] == 2000  # straight from before the cut to after it
    assert nxt == 2200
    assert not ((idx >= 1000) & (idx < 2000)).any()
    # starting inside a cut plays from its end
    idx, _ = player._positions(1500, 10)
    assert idx[0] == 2000


def test_cut_edges_fade(player):
    player.set_edits([(1.0, 2.0, "cut")])
    idx, _ = player._positions(900, 300)
    env = player._envelope(idx)
    assert env[99] == 0.0 and env[100] == 0.0  # silent at the jump
    assert env[0] == 1.0 and env[-1] == 1.0


def test_mutes_are_silent_on_both_sources(player):
    player.set_edits([(1.0, 2.0, "mute")])
    for name in ("original", "enhanced"):
        out = player._read(name, np.arange(900, 2100))
        assert np.all(out[200:1000] == 0)  # well inside the region
        assert out[0, 0] > 0 and out[-1, 0] > 0  # around it, unchanged
    idx, nxt = player._positions(900, 300)
    assert nxt == 1200  # silenced, not skipped


def test_loop_with_cut(player):
    player.set_loop((0.5, 1.5))
    player.set_edits([(1.0, 1.5, "cut")])
    idx, _ = player._positions(900, 300)
    assert idx[99] == 999 and idx[100] == 500  # the cut reaches the loop end: back to the start


def test_whole_loop_cut_plays_nothing(player):
    player.set_loop((0.5, 1.5))
    player.set_edits([(0.0, 2.0, "cut")])
    idx, _ = player._positions(600, 50)
    assert (idx == -1).all()
    assert not player._read("original", idx).any()


def test_no_edits_plays_straight(player):
    idx, nxt = player._positions(100, 64)
    assert (idx == np.arange(100, 164)).all() and nxt == 164
    assert player._envelope(idx) is None


def test_apply_edits_on_export():
    x = np.ones((3 * SR, 1), np.float32)
    out = apply_edits(x, SR, [[1.0, 2.0, "cut"]])
    assert abs(out.shape[0] - 2 * SR) < 20
    out = apply_edits(x, SR, [[1.0, 2.0, "mute"]])
    assert out.shape[0] == 3 * SR and np.all(out[1100:1900] == 0)
    assert apply_edits(x, SR, []) is x


def test_selection_moves_playhead(qapp):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from app.ui.waveform import WaveformOverview, WaveformView

    wv = WaveformView("original")
    wv.resize(1024, 200)
    wv.set_overview(WaveformOverview.compute(np.zeros((10 * SR, 1), np.float32), SR))
    got = []
    wv.selectionFinished.connect(got.append)

    def ev(kind, x):
        return QMouseEvent(kind, QPointF(x, 100), QPointF(x, 100), Qt.LeftButton,
                           Qt.LeftButton if kind != QMouseEvent.MouseButtonRelease else Qt.NoButton, Qt.NoModifier)

    wv.mousePressEvent(ev(QMouseEvent.MouseButtonPress, 600))
    wv.mouseMoveEvent(ev(QMouseEvent.MouseMove, 300))
    wv.mouseReleaseEvent(ev(QMouseEvent.MouseButtonRelease, 300))
    assert len(got) == 1 and got[0] == wv.selection and got[0][0] < got[0][1]
