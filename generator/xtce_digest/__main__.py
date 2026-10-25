"""Command line: ``python3 -m xtce_digest FILE...``.

Reads each XTCE document in the order given and prints ``FILE: DIGEST``,
where DIGEST is the MD5 digest of what the document means (see
``xtce_digest``).  The first document that can't be read or isn't valid
XTCE is reported, and ends the command with a failure status.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Documents are checked with the generated `xtce` Python package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

import xtce  # noqa: E402

from xtce_digest import digest  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="xtce-digest", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", metavar="FILE", help="an XTCE document")
    args = ap.parse_args(argv)
    for name in args.files:
        try:
            data = Path(name).read_bytes()
            # Only valid XTCE has a meaning to digest.
            xtce.Document.from_bytes(data)
            print(f"{name}: {digest(data)}", flush=True)
        except (OSError, xtce.XtceError) as e:
            print(f"xtce-digest: {name}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
