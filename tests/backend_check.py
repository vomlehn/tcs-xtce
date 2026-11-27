"""Check that the tcs_xtce package reads and writes XTCE the same way
through the tcs-xtce crate as in its own Python.

The package reads and writes XML through its extension module,
``tcs_xtce._native``, built from rust/tcs-xtce-python, and keeps its own
Python reader and writer until the move to Rust retires them (see
docs/rust-migration-design.rst).  For every XTCE document here -- the
schema-coverage samples, the demo mission, the examples, and the public
documents ``make samples-check`` downloads, where it has -- this reads the
document both ways, in two processes chosen with ``TCS_XTCE_BACKEND``,
and checks that

1. each accepts the document exactly when the other does;
2. what they read is equal, object for object;
3. what they write is the same bytes, indented and not.

Usage: python3 -I tests/backend_check.py
"""

from __future__ import annotations

import os
import pickle
import subprocess
import sys
from pathlib import Path

TOP = Path(__file__).resolve().parents[1]

# Run in each process: reads every document named on the command line and
# writes, pickled, the backend and for each document either what it read
# and wrote or the error it raised.
READ = r'''
import pickle
import sys

from tcs_xtce import Document
from tcs_xtce.document import BACKEND

out = {}
for path in sys.argv[1:]:
    try:
        doc = Document.from_file(path)
        out[path] = ("read", pickle.dumps(doc.root), doc.to_bytes(),
                     Document(doc.root, indent=False).to_bytes())
    except Exception as e:
        out[path] = ("refused", type(e).__name__)
sys.stdout.buffer.write(pickle.dumps((BACKEND, out)))
'''


def documents() -> list[str]:
    """Lists the XTCE documents to read.

    Returns:
        Their paths, in order.
    """
    found = sorted((TOP / "tests" / "samples").glob("*.xtce"))
    found += sorted((TOP / "tests" / "mission").glob("*.xtce"))
    found += sorted((TOP / "examples").glob("*/*.xtce"))
    external = TOP / "tests" / "external" / "files"
    found += sorted(p for p in external.rglob("*")
                    if p.suffix in (".xml", ".xtce") and p.is_file())
    return [str(p) for p in found]


def read_all(backend: str, paths: list[str]) -> dict:
    """Reads every document with one backend, in a process of its own.

    Args:
        backend: ``rust`` or ``python``.
        paths:   The documents.

    Returns:
        For each path, what reading and writing it gave.

    Raises:
        SystemExit: The process used another backend than ``backend``,
                    as it would where the extension module isn't built.
    """
    env = dict(os.environ, TCS_XTCE_BACKEND=backend,
               PYTHONPATH=str(TOP / "python"))
    r = subprocess.run([sys.executable, "-c", READ, *paths], env=env,
                       capture_output=True, check=True)
    used, out = pickle.loads(r.stdout)
    if used != backend:
        sys.exit(f"backend_check: asked for {backend}, got {used}: build the "
                 "extension module with make python")
    return out


def main() -> int:
    """Reads and writes every document both ways and compares them.

    Returns:
        The exit status: 0 if every document compares alike, else 1.
    """
    paths = documents()
    sys.path.insert(0, str(TOP / "python"))
    rust, python = read_all("rust", paths), read_all("python", paths)
    problems = []
    for path in paths:
        name = os.path.relpath(path, TOP)
        r, p = rust[path], python[path]
        if r[0] != p[0]:
            problems.append(f"{name}: Rust {r[0]} it, Python {p[0]} it")
        elif r[0] == "read":
            if pickle.loads(r[1]) != pickle.loads(p[1]):
                problems.append(f"{name}: what Rust and Python read differs")
            if r[2] != p[2]:
                problems.append(f"{name}: Rust and Python write it "
                                "differently, indented")
            if r[3] != p[3]:
                problems.append(f"{name}: Rust and Python write it "
                                "differently, not indented")
    for line in problems:
        print(line, file=sys.stderr)
    if problems:
        return 1
    read = sum(1 for p in paths if rust[p][0] == "read")
    print(f"backend_check: {len(paths)} documents, {read} read, read and "
          "written alike through the tcs-xtce crate and in Python")
    return 0


if __name__ == "__main__":
    sys.exit(main())
