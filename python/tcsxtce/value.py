"""Conversion of scalar values to their text forms, shared by the packet
runtime and tcs-xtce's XML: the formats are identical in the Rust, C++ and
Python libraries, so that all three write a given value the same way. It
mirrors ``tcsxtce::value`` in Rust.
"""

from __future__ import annotations

import math


def f_double(v: float) -> str:
    """Formats a double in the shortest form that reads back the same.

    Matches the Rust and C++ libraries.

    Args:
        v: The value.

    Returns:
        Its text: ``NaN``, ``INF``, ``-INF`` or Python's ``repr``.
    """
    if v != v:
        return "NaN"
    if v == math.inf:
        return "INF"
    if v == -math.inf:
        return "-INF"
    return repr(float(v))
