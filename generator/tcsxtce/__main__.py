"""Command line: ``python3 -m tcsxtce INPUT OUTPUT --lang {c,c++,rust,python}``.

Reads the mission's XTCE document INPUT and writes its packet codecs to
OUTPUT in one language.  ``c`` and ``c++`` write the same header, which
compiles both as C11 and as C++.

``python3 -m tcsxtce INPUT [OUTPUT] --size-report`` instead reports, for each
packet, the bits every field uses on the wire and the bits the document says
it needs; OUTPUT defaults to standard output.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The compiler reads XTCE documents with the generated `xtce` Python package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

import xtce  # noqa: E402

from xtcegen.__main__ import rustfmt  # noqa: E402
from xtcepkt import emit_c, emit_python, emit_rust, report  # noqa: E402
from xtcepkt.compile import compile_document  # noqa: E402
from xtcepkt.model import Unsupported  # noqa: E402

LANGS = ("c", "c++", "rust", "python")


def write(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists() or p.read_text() != text:
        p.write_text(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="tcsxtce", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("input", help="the mission's XTCE document")
    ap.add_argument("output", nargs="?", help="the file to write (with --size-report, default: standard output)")
    ap.add_argument(
        "--lang",
        choices=LANGS,
        help="language to generate; c and c++ both write a header usable from C and C++",
    )
    ap.add_argument(
        "--prefix",
        help="prefix of every C/C++ name (default: the root SpaceSystem name in snake_case, then _)",
    )
    ap.add_argument(
        "--skip-unsupported",
        action="store_true",
        help="leave out containers and meta-commands that use unsupported XTCE features instead of failing",
    )
    ap.add_argument(
        "--size-report",
        action="store_true",
        help="instead of code, report the bits each packet field uses and the bits the document says it needs",
    )
    ap.add_argument("--source-name", help="name of the input file recorded in the generated header comment")
    # Before Python 3.13, parse_args() can't place the optional OUTPUT when an
    # option separates it from INPUT.
    args = ap.parse_intermixed_args(argv)
    if args.size_report:
        if args.lang is not None or args.prefix is not None:
            ap.error("--size-report writes no code; leave out --lang and --prefix")
    elif args.output is None or args.lang is None:
        ap.error("OUTPUT and --lang are required unless --size-report is given")
    if args.prefix is not None and args.lang not in ("c", "c++"):
        ap.error("--prefix only applies to --lang c and --lang c++")

    try:
        root = xtce.Document.from_file(args.input).root
        mission = compile_document(root, args.skip_unsupported or args.size_report)
    except (Unsupported, xtce.XtceError) as e:
        print(f"tcsxtce: {args.input}: {e}", file=sys.stderr)
        return 1
    if not args.size_report:  # the report lists them itself
        for msg in mission.skipped:
            print(f"tcsxtce: warning: skipped {msg}", file=sys.stderr)
    source = args.source_name or Path(args.input).name
    if args.size_report:
        text = report.report(mission)
        if args.output is None:
            sys.stdout.write(text)
        else:
            write(args.output, text)
        return 0
    if args.lang == "rust":
        # Generated Rust must follow standard formatting, like all Rust here.
        text = rustfmt(emit_rust.emit(mission, source))
    elif args.lang == "python":
        text = emit_python.emit(mission, source)
    else:
        text = emit_c.emit(mission, source, args.prefix)
    write(args.output, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
