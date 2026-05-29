from pathlib import Path

from tftp_monitor.pathing import build_destination_path, build_local_cache_path


def test_build_local_cache_path_preserves_relative_structure(tmp_path: Path) -> None:
    result = build_local_cache_path(tmp_path, "configs/a.bin")
    assert result == tmp_path / "configs" / "a.bin"


def test_build_destination_path_preserves_relative_structure() -> None:
    result = build_destination_path("/home/tsl", "images/fw.bin")
    assert result == "/home/tsl/images/fw.bin"
