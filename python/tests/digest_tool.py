"""Prints the digest of each XTCE document named on the command line, one
per line, or ``error`` for one that can't be read: what
``tests/digest_check.sh`` compares across languages.  With ``--set``,
prints the one digest of the set of documents instead."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xtce import XtceError  # noqa: E402
from xtce.digest import digest_file, digest_files  # noqa: E402

if sys.argv[1:2] == ["--set"]:
    try:
        print(digest_files(sys.argv[2:]))
    except (OSError, XtceError):
        print("error")
    sys.exit(0)
for path in sys.argv[1:]:
    try:
        print(digest_file(path))
    except (OSError, XtceError):
        print("error")
