"""Stable, human-readable test naming shared by the API and UI."""

import re
from datetime import datetime


def _part(value, fallback):
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip()).strip("-")
    return cleaned[:32] or fallback


def build_test_name(operator, test_type, sample_id, when=None):
    """Return YYYYMMDD-test-sample-operator using filesystem-safe segments."""
    moment = when or datetime.now().astimezone()
    return "-".join((moment.strftime("%Y%m%d"), _part(test_type, "test"),
                     _part(sample_id, "sample"), _part(operator, "operator")))

