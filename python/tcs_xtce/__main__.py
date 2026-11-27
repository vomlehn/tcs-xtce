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

Warnings go to standard error, and the code is written all the same:
binary data whose size another value gives, with nothing in the document
to bound it below the maximum field size, or that takes the rest of the
packet, has no maximum size, and the generated code's buffer for it is the
maximum field size instead.  ``--fatal-warnings`` makes any warning fail
the run, as GNU ld's does: each is still printed, the output is not
written, and the exit status is 1.

A loop in the document -- a type, container or meta-command that inherits
from or includes itself, or streams that feed one another -- is always an
error, which ``--skip-unsupported`` does not skip.

``python3 -m tcs_xtce INPUT... [OUTPUT] --size-report`` instead reports, for
each packet, the bits every field uses on the wire and the bits the document
says it needs.  OUTPUT may be left out when there is only one INPUT, and the
report goes to standard output.

``python3 -m tcs_xtce INPUT... [OUTPUT] --dump-model`` instead writes the
resolved model the code would be generated from, as the canonical text
:mod:`tcs_xtce.compiler.dump` describes, for comparing the Python compiler
with the Rust one.  OUTPUT may be left out, as for ``--size-report``.
``--dump-types`` writes each parameter and argument type alone instead,
resolved whether or not the rest of the mission compiles: the first stage
the two compilers are compared on.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import tcs_xtce
from tcs_xtce.binding.__main__ import rustfmt
from tcs_xtce.binding.wrap import wrap
from tcs_xtce.compiler import dump, emit_c, emit_python, emit_rust, report
from tcs_xtce.compiler.compile import DIALECTS, Compiler, compile_documents
from tcs_xtce.compiler.model import Cycle, Unsupported

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
        The exit status: 0 on success, 1 if a document can't be read, has
        a loop in it or uses something unsupported, or there was a warning
        and --fatal-warnings was given.  Bad arguments exit with status 2.
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
    ap.add_argument("--dump-model", action="store_true",
                    help="instead of code, write the resolved model as "
                         "canonical text, for comparing compilers")
    ap.add_argument("--dump-types", action="store_true",
                    help="instead of code, write each parameter and argument "
                         "type, resolved alone, as canonical text")
    ap.add_argument(
        "--dynamic-allocations", choices=("true", "false"), default="true",
        help="whether the generated code may allocate heap memory at any time "
             "(true, the default) or only during initialization (false)")
    ap.add_argument("--fatal-warnings", action="store_true",
                    help="fail, writing nothing, if there is any warning, "
                         "after printing them all")
    ap.add_argument("--source-name",
                    help="name of the input recorded in the generated header "
                         "comment (default: the INPUT file names)")
    # Before Python 3.13, parse_args() can't place the optional OUTPUT when an
    # option separates it from INPUT.
    args = ap.parse_intermixed_args(argv)
    chosen = [o
              for o, on in (("--size-report", args.size_report),
                            ("--dump-model", args.dump_model),
                            ("--dump-types", args.dump_types)) if on]
    if len(chosen) > 1:
        ap.error(f"give one of {', '.join(chosen)}, not more")
    no_code = bool(chosen)
    if no_code and len(args.files) == 1:
        inputs, output = args.files, None
    elif len(args.files) < 2:
        ap.error("OUTPUT is required unless --size-report, --dump-model or "
                 "--dump-types is given")
    else:
        inputs, output = args.files[:-1], args.files[-1]
    if output == "-":
        output = None
    # Forgetting OUTPUT would otherwise overwrite the last document.
    if output is not None and Path(output).suffix.lower() in (".xtce", ".xml"):
        ap.error(f"OUTPUT {output} looks like an XTCE document; give the file "
                 "to write after the INPUT documents")
    if no_code:
        if args.lang is not None or args.prefix is not None:
            ap.error(f"{chosen[0]} writes no code; leave out --lang and "
                     "--prefix")
    elif args.lang is None:
        ap.error("--lang is required unless --size-report, --dump-model or "
                 "--dump-types is given")
    if args.prefix is not None and args.lang not in ("c", "c++"):
        ap.error("--prefix only applies to --lang c and --lang c++")
    alloc = args.dynamic_allocations == "true"
    if not alloc and args.lang == "python":
        ap.error("--dynamic-allocations false is not available for --lang "
                 "python, which allocates every object on the heap")

    roots = []
    for name in inputs:
        try:
            roots.append(tcs_xtce.Document.from_file(name).root)
        except (OSError, tcs_xtce.XtceError) as e:
            print(f"tcs-xtce: {name}: {e}", file=sys.stderr)
            return 1
    if args.dump_types:
        try:
            compiler = Compiler(roots, args.skip_unsupported, args.dialect)
        except (Cycle, Unsupported) as e:
            where = f"{inputs[0]}: " if len(inputs) == 1 else ""
            print(f"tcs-xtce: {where}{e}", file=sys.stderr)
            return 1
        write(output, dump.dump_types(compiler))
        return 0
    try:
        mission = compile_documents(
            roots, args.skip_unsupported or args.size_report, args.dialect)
    except (Cycle, Unsupported) as e:
        where = f"{inputs[0]}: " if len(inputs) == 1 else ""
        print(f"tcs-xtce: {where}{e}", file=sys.stderr)
        return 1
    warnings = list(mission.warnings)
    if not args.size_report:  # the report lists them itself
        warnings += [f"skipped {msg}" for msg in mission.skipped]
    for msg in warnings:
        print(f"tcs-xtce: warning: {msg}", file=sys.stderr)
    if warnings and args.fatal_warnings:
        print(f"tcs-xtce: {len(warnings)} "
              f"warning{'' if len(warnings) == 1 else 's'}, and "
              "--fatal-warnings was given: nothing written", file=sys.stderr)
        return 1
    source = args.source_name or ", ".join(Path(n).name for n in inputs)
    if args.size_report:
        write(output, report.report(mission))
        return 0
    if args.dump_model:
        write(output, dump.dump(mission))
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
