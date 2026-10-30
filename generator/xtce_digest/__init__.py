"""xtce-digest: an MD5 digest of what an XTCE document means.

The digest itself is computed by the runtime library, ``xtce.digest`` in
``python/`` (the Rust and C/C++ runtimes compute the same digest); see it
for what the digest covers.  This package re-exports it for the
``xtce-digest`` command, which also checks that each document is valid
XTCE before it digests it.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The digest and the schema check come from the `xtce` Python package:
# installed beside it, or, run from the repository, in python/.
_RUNTIME = Path(__file__).resolve().parents[2] / "python"
if (_RUNTIME / "xtce").is_dir():
    sys.path.insert(0, str(_RUNTIME))

from xtce import Document, XtceError  # noqa: E402,F401
from xtce.digest import (  # noqa: E402,F401
    UNORDERED, digest, digest_file, digest_files, digest_set, digest_string,
    encoding)


def checked_digest(data: bytes) -> str:
    """The digest of ``data``, after checking that it is valid XTCE: raises
    ``XtceError`` if it isn't."""
    Document.from_bytes(data)
    return digest(data)

