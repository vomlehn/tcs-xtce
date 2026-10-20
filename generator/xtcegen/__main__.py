"""Command line entry point: ``python3 -m xtcegen --xsd ... --rust ... ``."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from . import binding, xsd


def write_if_changed(path: str, text: str) -> None:
    """Only touch files whose contents change, so make stays incremental."""
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
    exe = shutil.which("rustfmt")
    if exe is None:
        sys.exit("error: rustfmt is required to generate Rust code")
    res = subprocess.run(
        [exe, "--edition", "2021", "--emit", "stdout"],
        input=text,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        sys.exit(f"error: rustfmt failed:\n{res.stderr}")
    return res.stdout


def main() -> None:
    p = argparse.ArgumentParser(prog="xtcegen", description=__doc__)
    p.add_argument("--xsd", required=True, help="path to SpaceSystem.xsd")
    p.add_argument("--rust", help="output path of the generated Rust module")
    p.add_argument("--cpp-header", help="output path of the generated C++ header")
    p.add_argument("--cpp-source", help="output path of the generated C++ source")
    p.add_argument("--python", help="output path of the generated Python module")
    p.add_argument("--sample", help="write schema-covering sample documents to this directory")
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
            write_if_changed(args.cpp_header, header)
        if args.cpp_source:
            write_if_changed(args.cpp_source, source)
    if args.python:
        from . import emit_python

        write_if_changed(args.python, emit_python.emit(b))
    if args.sample:
        from . import sample

        for name, text in sample.generate(b):
            write_if_changed(os.path.join(args.sample, name), text)


if __name__ == "__main__":
    main()
