from pathlib import Path


def test_readme_mentions_start_command() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "python -m tftp_monitor.app" in readme


def test_readme_mentions_bidirectional_file_and_directory_monitoring() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "/tftpboot" in readme
    assert "Reverse Monitor" in readme
    assert "files or directories" in readme
    assert "commas, semicolons, or new lines" in readme
    assert "nested subdirectories" in readme
    assert "unrelated files" in readme


def test_readme_mentions_first_scan_baseline_behavior() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "first successful scan creates a baseline" in readme
