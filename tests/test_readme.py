from pathlib import Path


def test_readme_mentions_start_command() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "python -m tftp_monitor.app" in readme


def test_readme_mentions_both_default_source_directories() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "/tftpboot" in readme
    assert "/home/wei.li" in readme
