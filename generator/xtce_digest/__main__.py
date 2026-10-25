"""Command line: ``python3 -m xtce_digest [--set] FILE...``.

Reads each XTCE document in the order given and prints ``FILE: DIGEST``,
where DIGEST is the MD5 digest of what the document means (see
``xtce_digest``).  With ``--set``, the documents are one mission split
across several files, and it prints the one DIGEST of the set instead,
which doesn't depend on the order of the files.  The first document that
can't be read or isn't valid XTCE is reported, and ends the command with a
failure status.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from xtce_digest import Document, XtceError, checked_digest, digest_set


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="xtce-digest", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", metavar="FILE", help="an XTCE document")
    ap.add_argument("--set", action="store_true",
                    help="print the one digest of the set of documents")
    args = ap.parse_args(argv)
    if args.set:
        documents = []
        for name in args.files:
            try:
                documents.append(Path(name).read_bytes())
                Document.from_bytes(documents[-1])
            except (OSError, XtceError) as e:
                print(f"xtce-digest: {name}: {e}", file=sys.stderr)
                return 1
        print(digest_set(documents))
        return 0
    for name in args.files:
        try:
            print(f"{name}: {checked_digest(Path(name).read_bytes())}",
                  flush=True)
        except (OSError, XtceError) as e:
            print(f"xtce-digest: {name}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
