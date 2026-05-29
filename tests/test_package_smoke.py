from importlib.metadata import version


def test_package_metadata_is_exposed() -> None:
    assert version("tftp-monitor") == "0.1.0"
