"""The error tcs-xtce raises for XTCE it cannot read or write, in a module
of its own so that the digest, which needs nothing else of the XML
binding's, can be taken without it.
"""

from __future__ import annotations


class XtceError(ValueError):
    """Raised for malformed or schema-invalid XTCE input or output."""

    def __init__(self, message: str, line: int = 0) -> None:
        super().__init__(f"{line}: {message}" if line else message)
        self.message = message
        self.line = line
