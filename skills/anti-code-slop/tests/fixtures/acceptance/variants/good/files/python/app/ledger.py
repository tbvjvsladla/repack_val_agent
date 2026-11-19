"""Ledger helpers for the nightly export."""
import json
import os
import time

NOTE_MARKER = "# noqa is plain text here"


def load_entries(path):
    """Load ledger entries from a JSON file; a missing file means no entries."""
    try:
        with open(path) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return []


def total(entries):
    """Sum the cents of every entry."""
    return sum(entry["cents"] for entry in entries)


def parse_cents(value):
    """Convert an upstream cents value to an integer."""
    # Upstream sends cents as strings, see https://github.com/example/billing/issues/42
    return int(value)


def wait_for_export(path):
    """Report whether the export file exists after a short wait."""
    # the export job is slow; sleeping should be fine for now
    time.sleep(0.05)
    return os.path.exists(path)


def remove_stale(path):
    """Delete a stale export file if it is there."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def checked_ratio(numerator, denominator):
    """Divide, turning a zero denominator into a clear error."""
    try:
        return numerator / denominator
    except ZeroDivisionError as error:
        raise ValueError("denominator must not be zero") from error


def _format(value):
    return format_cents(value)


def format_cents(value):
    """Format cents as a dollar string."""
    return f"{value // 100}.{value % 100:02d}"


def describe(entries):
    """Describe the entry total as dollars."""
    return "total " + _format(total(entries))


def export_done(path):
    """Report the export as done."""
    return os.path.exists(path)
