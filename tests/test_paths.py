"""Where LexiTrack keeps its files, in a clone and in an installed copy."""

from __future__ import annotations

from pathlib import Path

import pytest

from lexitrack.core import paths
from lexitrack.telegram import config


@pytest.fixture
def no_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No LEXITRACK_DATA_DIR, and a private LOCALAPPDATA."""
    monkeypatch.delenv("LEXITRACK_DATA_DIR", raising=False)
    monkeypatch.delenv("LEXITRACK_LOG_DIR", raising=False)
    local = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("XDG_DATA_HOME", str(local))
    return local


def test_a_clone_keeps_its_data_beside_the_code(
    no_override: Path, tmp_path: Path, monkeypatch
) -> None:
    clone = tmp_path / "LexiTrack"
    clone.mkdir()
    (clone / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(paths, "PROJECT_ROOT", clone)
    assert paths.is_source_checkout() is True
    assert paths.data_dir() == clone / "data"
    assert paths.logs_dir() == clone / "logs"
    assert clone / ".env" in config.env_file_candidates()


def test_an_installed_copy_uses_the_user_data_folder(
    no_override: Path, tmp_path: Path, monkeypatch
) -> None:
    """The bug this guards: site-packages is writable, so data landed there."""
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    monkeypatch.setattr(paths, "PROJECT_ROOT", site_packages)
    assert paths.is_source_checkout() is False
    assert paths.data_dir() == no_override / "LexiTrack"
    assert paths.logs_dir() == no_override / "LexiTrack" / "logs"
    assert config.env_file_candidates() == [no_override / "LexiTrack" / ".env"]


def test_the_environment_override_wins_everywhere(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LEXITRACK_DATA_DIR", str(tmp_path / "portable"))
    assert paths.data_dir() == (tmp_path / "portable").resolve()


def test_the_shortcut_starts_the_app_without_a_console() -> None:
    from lexitrack.core import shortcut

    program, arguments = shortcut.launcher()
    assert arguments == "-m lexitrack"
    if shortcut.supported():
        assert program.name.lower() == "pythonw.exe"


def test_the_icon_is_a_real_multi_size_ico() -> None:
    """Windows shortcuts need an .ico; an SVG would show a blank icon."""
    import struct

    from lexitrack.core.shortcut import ICON

    data = ICON.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1), "ICO header"
    sizes = {data[6 + 16 * i] or 256 for i in range(count)}
    assert {16, 32, 48, 256} <= sizes
