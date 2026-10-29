"""Command line: ``python3 -m tcs_xtce INPUT... OUTPUT --lang
{c,c++,rust,python}``.

Reads the mission's XTCE documents INPUT... and writes their packet codecs
to OUTPUT in one language; OUTPUT ``-`` is standard output.  ``c`` and
``c++`` write the same header, which compiles both as C11 and as C++.

A mission may be split across several documents, as Yamcs allows: each
document's root SpaceSystem sits at the top of one tree, so a reference
such as ``/PUS/pus-tm`` can name a container in the document whose root
SpaceSystem is ``PUS``.  Code is written for the packets of every document.
List the documents that others build on first: the mission, and the
default C prefix, are named for the last.

``--dynamic-allocations false`` generates code that allocates no heap memory
after initialization: Rust gets fixed-capacity arrays, strings and binary
fields, and C and C++ never allocate either way.  Python allocates every
object on the heap, so it accepts only ``true``.

``python3 -m tcs_xtce INPUT... [OUTPUT] --size-report`` instead reports, for
each packet, the bits every field uses on the wire and the bits the document
says it needs.  OUTPUT may be left out when there is only one INPUT, and the
report goes to standard output.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The compiler reads XTCE documents with the generated `xtce` Python package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

import xtce  # noqa: E402

from xtcegen.__main__ import rustfmt  # noqa: E402
from xtcegen.wrap import wrap  # noqa: E402
from xtcepkt import emit_c, emit_python, emit_rust, report  # noqa: E402
from xtcepkt.compile import DIALECTS, compile_documents  # noqa: E402
from xtcepkt.model import Unsupported  # noqa: E402

LANGS = ("c", "c++", "rust", "python")


def write(path, text: str) -> None:
    """Writes ``text`` to a file, or to standard output.  An unchanged file
    is left alone, so make doesn't rebuild what depends on it.

    Args:
        path: The file, its directories made as needed, or None for
              standard output.
        text: What to write.
    """
    if path is None:
        sys.stdout.write(text)
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists() or p.read_text() != text:
        p.write_text(text)


def main(argv=None) -> int:
    """Runs the command (see the module docstring).

    Args:
        argv: The arguments, without the program name, or None for
              ``sys.argv``'s.

    Returns:
        The exit status: 0 on success, 1 if a document can't be read or
        uses something unsupported.  Bad arguments exit with status 2.
    """

    ap = argparse.ArgumentParser(
        prog="tcs-xtce", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", metavar="INPUT... OUTPUT",
                    help="the mission's XTCE documents, then the file to write "
                         "(- for standard output; with --size-report and one "
                         "INPUT, it may be left out)")
    ap.add_argument("--lang", choices=LANGS,
                    help="language to generate; c and c++ both write a header "
                         "usable from C and C++")
    ap.add_argument("--prefix",
                    help="prefix of every C/C++ name (default: the root "
                         "SpaceSystem name in snake_case, then _)")
    ap.add_argument("--skip-unsupported", action="store_true",
                    help="leave out containers and meta-commands that use "
                         "unsupported XTCE features instead of failing")
    ap.add_argument("--dialect", choices=DIALECTS,
                    help="the dialect of XTCE the documents follow where the "
                         "standard leaves something open, unless their "
                         "tcs-xtce.dialect AncillaryData says")
    ap.add_argument("--size-report", action="store_true",
                    help="instead of code, report the bits each packet field "
                         "uses and the bits the document says it needs")
    ap.add_argument(
        "--dynamic-allocations", choices=("true", "false"), default="true",
        help="whether the generated code may allocate heap memory at any time "
             "(true, the default) or only during initialization (false)")
    ap.add_argument("--source-name",
                    help="name of the input recorded in the generated header "
                         "comment (default: the INPUT file names)")
    # Before Python 3.13, parse_args() can't place the optional OUTPUT when an
    # option separates it from INPUT.
    args = ap.parse_intermixed_args(argv)
    if args.size_report and len(args.files) == 1:
        inputs, output = args.files, None
    elif len(args.files) < 2:
        ap.error("OUTPUT is required unless --size-report is given")
    else:
        inputs, output = args.files[:-1], args.files[-1]
    if output == "-":
        output = None
    # Forgetting OUTPUT would otherwise overwrite the last document.
    if output is not None and Path(output).suffix.lower() == ".xml":
        ap.error(f"OUTPUT {output} looks like an XTCE document; give the file "
                 "to write after the INPUT documents")
    if args.size_report:
        if args.lang is not None or args.prefix is not None:
            ap.error("--size-report writes no code; leave out --lang and "
                     "--prefix")
    elif args.lang is None:
        ap.error("--lang is required unless --size-report is given")
    if args.prefix is not None and args.lang not in ("c", "c++"):
        ap.error("--prefix only applies to --lang c and --lang c++")
    alloc = args.dynamic_allocations == "true"
    if not alloc and args.lang == "python":
        ap.error("--dynamic-allocations false is not available for --lang "
                 "python, which allocates every object on the heap")

    roots = []
    for name in inputs:
        try:
            roots.append(xtce.Document.from_file(name).root)
        except (OSError, xtce.XtceError) as e:
            print(f"tcs-xtce: {name}: {e}", file=sys.stderr)
            return 1
    try:
        mission = compile_documents(
            roots, args.skip_unsupported or args.size_report, args.dialect)
    except Unsupported as e:
        where = f"{inputs[0]}: " if len(inputs) == 1 else ""
        print(f"tcs-xtce: {where}{e}", file=sys.stderr)
        return 1
    if not args.size_report:  # the report lists them itself
        for msg in mission.skipped:
            print(f"tcs-xtce: warning: skipped {msg}", file=sys.stderr)
    source = args.source_name or ", ".join(Path(n).name for n in inputs)
    if args.size_report:
        write(output, report.report(mission))
        return 0
    if args.lang == "rust":
        # Generated Rust must follow standard formatting, like all Rust here.
        text = rustfmt(emit_rust.emit(mission, source, alloc))
    elif args.lang == "python":
        text = wrap(emit_python.emit(mission, source), "python")
    else:
        text = wrap(emit_c.emit(mission, source, args.prefix), "c")
    write(output, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
