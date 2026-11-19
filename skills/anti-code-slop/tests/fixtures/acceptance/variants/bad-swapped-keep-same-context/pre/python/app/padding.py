"""Padding helpers kept for the fixed-width export."""


def pad(rows):
    """Pad rows to the fixed export width."""
    mark(rows)
    rows.append("x" * 120)  # noqa: E501
    mark(rows)
    rows.append("x" * 120)  # noqa: E501
    mark(rows)
    return rows


def mark(rows):
    """Append a row separator."""
    rows.append("|")
