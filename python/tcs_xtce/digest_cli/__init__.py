"""xtce-digest: an MD5 digest of what an XTCE document means.

The digest itself is computed by the runtime library, ``tcs_xtce.digest``
(the Rust and C/C++ runtimes compute the same digest); see it
for what the digest covers.  This package re-exports it for the
``xtce-digest`` command, which also checks that each document is valid
XTCE before it digests it.
"""

from __future__ import annotations

from tcs_xtce import Document, XtceError  # noqa: F401
from tcs_xtce.digest import (  # noqa: F401
    UNORDERED, digest, digest_file, digest_files, digest_set, digest_string,
    encoding)


def checked_digest(data: bytes) -> str:
    """The digest of ``data``, after checking that it is valid XTCE: raises
    ``XtceError`` if it isn't."""
    Document.from_bytes(data)
    return digest(data)

