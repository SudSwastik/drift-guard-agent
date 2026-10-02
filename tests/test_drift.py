"""Fixed-time thresholds never equate a single error with a repeated trend."""

from datetime import UTC, datetime

import pytest

from drift_guard.drift.models import Aggregate, CodeCount, timestamp
from drift_guard.drift.report import build_report
from tests.test_storage import scope


@pytest.mark.parametrize(
    ("samples", "count", "status", "detected"),
    [
        (0, 0, "insufficient_data", False),
        (1, 1, "insufficient_data", False),
        (9, 5, "insufficient_data", False),
        (10, 2, "below_threshold", False),
        (20, 3, "below_threshold", False),
        (10, 3, "threshold_exceeded", True),
        (15, 3, "threshold_exceeded", True),
    ],
)
def test_threshold_boundaries(samples: int, count: int, status: str, detected: bool) -> None:
    aggregate = Aggregate(
        samples=samples,
        invalid=count,
        first_seen=100 if samples else None,
        last_seen=200 if samples else None,
        codes=(CodeCount("invalid_type", count, 100, 200),) if count else (),
    )
    report = build_report(scope(), aggregate, 10, 3, 0.2)
    assert report["status"] == status and report["driftDetected"] is detected
    assert report == build_report(scope(), aggregate, 10, 3, 0.2)


def test_timestamp_requires_explicit_timezone() -> None:
    with pytest.raises(ValueError):
        timestamp(datetime(2026, 1, 1))
    assert timestamp(datetime(1970, 1, 1, tzinfo=UTC)) == 0
