from belegmanager import __version__
from belegmanager.versioning import get_app_version


def test_version_is_non_empty_string() -> None:
    assert isinstance(__version__, str)
    assert __version__.strip()


def test_get_app_version_matches_exported_version() -> None:
    assert get_app_version() == __version__
