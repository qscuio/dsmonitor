from tftp_monitor.gui import format_status_summary
from tftp_monitor.models import SyncCycleResult


def test_format_status_summary_includes_core_counts() -> None:
    result = SyncCycleResult(scanned_files=5, changed_files=2, synced_files=2, failed_files=1)
    assert format_status_summary(result) == "Scanned 5 | Changed 2 | Synced 2 | Failed 1"
