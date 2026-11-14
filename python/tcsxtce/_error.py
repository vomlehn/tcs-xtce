"""The error the digest, and tcs-xtce's XML binding, raise for XTCE they
cannot read or write.
"""

from __future__ import annotations


class XtceError(ValueError):
    """Raised for malformed or schema-invalid XTCE input or output."""

    def __init__(self, message: str, line: int = 0) -> None:
        super().__init__(f"{line}: {message}" if line else message)
        self.message = message
        self.line = line
