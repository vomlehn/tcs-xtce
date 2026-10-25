"""Command line: ``python3 -m xtce_digest FILE...``.

Reads each XTCE document in the order given and prints ``FILE: DIGEST``,
where DIGEST is the MD5 digest of what the document means (see
``xtce_digest``).  The first document that can't be read or isn't valid
XTCE is reported, and ends the command with a failure status.
"""

from __future__ import annotations

import argparse
import sys

from xtce_digest import XtceError, digest_file


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="xtce-digest", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", metavar="FILE", help="an XTCE document")
    args = ap.parse_args(argv)
    for name in args.files:
        try:
            print(f"{name}: {digest_file(name)}", flush=True)
        except (OSError, XtceError) as e:
            print(f"xtce-digest: {name}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
