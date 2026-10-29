"""Command line entry point: ``python3 -m xtcegen --xsd ... --rust ... ``."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from . import binding, xsd
from .wrap import WIDTH, split_rust_strings, wrap


def write_if_changed(path: str, text: str) -> None:
    """Writes a file only if its contents change, so make stays
    incremental.

    Creates the file's directory if need be, and prints the file's name
    when it writes it.

    Args:
        path: The file.
        text: Its new contents.
    """
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"generated {path}")


def rustfmt(text: str) -> str:
    """Formats Rust with rustfmt at the project's line width, then wraps
    what rustfmt leaves too long: comments, and code it can't lay out.

    Args:
        text: The Rust source.

    Returns:
        The formatted source, as cargo fmt would leave it.
    """
    text = wrap(_rustfmt(split_rust_strings(text)), "rust")
    # Format the wrapped code again, so that it is what cargo fmt would
    # make of it.
    return wrap(_rustfmt(text), "rust")


def _rustfmt(text: str) -> str:
    """Runs rustfmt once over Rust source, at the project's line width.

    Exits the program if rustfmt is missing or fails.

    Args:
        text: The Rust source.

    Returns:
        The source as rustfmt lays it out.
    """
    exe = shutil.which("rustfmt")
    if exe is None:
        sys.exit("error: rustfmt is required to generate Rust code")
    res = subprocess.run(
        [exe, "--edition", "2021", "--config", f"max_width={WIDTH}", "--emit",
         "stdout"], input=text, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"error: rustfmt failed:\n{res.stderr}")
    return res.stdout


def main() -> None:
    """Parses the schema named on the command line and writes the
    outputs asked for: the Rust module, the C++ header and source, the
    Python module and the sample documents.
    """
    p = argparse.ArgumentParser(prog="xtcegen", description=__doc__)
    p.add_argument("--xsd", required=True, help="path to SpaceSystem.xsd")
    p.add_argument("--rust", help="output path of the generated Rust module")
    p.add_argument("--cpp-header",
                   help="output path of the generated C++ header")
    p.add_argument("--cpp-source",
                   help="output path of the generated C++ source")
    p.add_argument("--python",
                   help="output path of the generated Python module")
    p.add_argument("--sample",
                   help="write schema-covering sample documents to this "
                        "directory")
    args = p.parse_args()

    schema = xsd.parse(args.xsd)
    b = binding.build(schema)
    if args.rust:
        from . import emit_rust

        write_if_changed(args.rust, rustfmt(emit_rust.emit(b)))
    if args.cpp_header or args.cpp_source:
        from . import emit_cpp

        header, source = emit_cpp.emit(b)
        if args.cpp_header:
            write_if_changed(args.cpp_header, wrap(header, "c"))
        if args.cpp_source:
            write_if_changed(args.cpp_source, wrap(source, "c"))
    if args.python:
        from . import emit_python

        write_if_changed(args.python, wrap(emit_python.emit(b), "python"))
    if args.sample:
        from . import sample

        for name, text in sample.generate(b):
            write_if_changed(os.path.join(args.sample, name), text)


if __name__ == "__main__":
    main()
