from app.ui import tour
from app.utils.config import APP_VERSION


def test_new_install_gets_the_basics():
    assert tour.steps_on_start(True, None) == tour.BASICS


def test_update_shows_only_newer_entries():
    news = tour.steps_on_start(False, "1.1.1")
    assert news and all(tour._vkey(s.version) > (1, 1, 1) for s in news)
    assert tour.steps_on_start(False, APP_VERSION) == []


def test_settings_from_before_the_tour_count_as_an_update():
    assert tour.steps_on_start(False, None) == tour.news_since(tour.FIRST_TOUR_VERSION)


def test_versions_compare_numerically():
    assert tour._vkey("1.10.0") > tour._vkey("1.9.9")
    assert tour.news_since("99.0.0") == []


def test_every_news_entry_has_a_version_and_basics_have_none():
    assert all(s.version for s in tour.WHATS_NEW)
    assert not any(s.version for s in tour.BASICS)
    assert any(s.version == APP_VERSION for s in tour.WHATS_NEW)
