"""Prints the digest of each XTCE document named on the command line, one
per line, or ``error`` for one that can't be read: what
``tests/digest_check.sh`` compares across languages."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xtce import XtceError  # noqa: E402
from xtce.digest import digest_file  # noqa: E402

for path in sys.argv[1:]:
    try:
        print(digest_file(path))
    except (OSError, XtceError):
        print("error")
