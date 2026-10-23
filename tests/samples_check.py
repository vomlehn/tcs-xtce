"""Check tcs-xtce against public XTCE documents.

``make samples-check`` downloads the documents listed in
tests/external/sources.txt into tests/external/files/ and runs this script,
which for each document checks that

1. the XTCE binding accepts it exactly when ``xmllint`` finds it valid
   against the XTCE 1.3 schema (older XTCE namespaces are read as 1.3);
2. a document the binding accepts writes back to an equal document;
3. the packet compiler, the size report and the three emitters never crash:
   they either produce output or report an unsupported construct;
4. the generated code compiles: the C header as C11 and as C++, the Rust
   module against the tcs-xtce crate, and the Python module;

and prints one summary per document, which must equal
tests/external/expected.txt.  ``--update`` rewrites expected.txt instead.

The documents are only parsed, never executed.

Usage: python3 -I tests/samples_check.py [--update]
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TOP = Path(__file__).resolve().parents[1]
EXTERNAL = TOP / "tests" / "external"
sys.path[:0] = [str(TOP / "python"), str(TOP / "generator")]

import xtce  # noqa: E402
from xtcegen.__main__ import rustfmt  # noqa: E402
from xtcepkt import emit_c, emit_python, emit_rust, report  # noqa: E402
from xtcepkt.compile import compile_document  # noqa: E402
from xtcepkt.model import Unsupported  # noqa: E402

XSD = TOP / "references" / "SpaceSystem.xsd"
CATALOG = TOP / "references" / "catalog.xml"
NS13 = b"http://www.omg.org/spec/XTCE/20250214"
OLD_NS = re.compile(rb"http://www\.omg\.org/(space/xtce|spec/XTCE/\d{8})")
RLIB = TOP / "rust" / "target" / "release" / "libtcs_xtce.rlib"


def sources() -> list[tuple[str, str]]:
    """(local name, SHA-256) of every document in sources.txt."""
    out = []
    for line in (EXTERNAL / "sources.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, digest, _url = line.split()
            out.append((name, digest))
    return out


def schema_valid(path: Path) -> bool:
    data = OLD_NS.sub(NS13, path.read_bytes())
    # Validate against the local schema, not the one a document points at.
    data = re.sub(rb'\s+xsi:schemaLocation="[^"]*"', b"", data)
    with tempfile.NamedTemporaryFile(suffix=".xml") as t:
        t.write(data)
        t.flush()
        r = subprocess.run(
            ["xmllint", "--noout", "--nonet", "--schema", str(XSD), t.name],
            capture_output=True,
            env={**os.environ, "XML_CATALOG_FILES": str(CATALOG)},
        )
    return r.returncode == 0


def run(cmd: list[str]) -> str:
    """Runs a compiler; returns its first error line, or "" on success."""
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        return ""
    lines = [x for x in (r.stderr + r.stdout).splitlines() if "error" in x] or (r.stderr + r.stdout).splitlines()
    return lines[0].strip() if lines else f"exit {r.returncode}"


def build(mission, stem: str, out: Path) -> list[str]:
    """Generates code in every language and compiles it; returns failures."""
    bad = []
    header = out / f"{stem}.h"
    header.write_text(emit_c.emit(mission, stem))
    for lang, compiler in (("c", "cc"), ("c++", "c++")):
        std = "-std=c11" if lang == "c" else "-std=c++20"
        cmd = [os.environ.get("CC" if lang == "c" else "CXX", compiler), std, "-Wall", "-Wextra", "-Werror"]
        cmd += [f"-I{TOP / 'cpp' / 'include'}", "-fsyntax-only", "-x", lang, str(header)]
        err = run(cmd)
        if err:
            bad.append(f"C header as {lang}: {err}")
    module = out / f"{stem}.rs"
    module.write_text(rustfmt(emit_rust.emit(mission, stem)))
    cmd = ["rustc", "--edition", "2021", "--crate-type", "lib", "--crate-name", "sample", "-D", "warnings"]
    cmd += ["--extern", f"tcs_xtce={RLIB}", "-L", f"dependency={RLIB.parent / 'deps'}"]
    cmd += ["--emit", "metadata", "-o", str(out / f"lib{stem}.rmeta"), str(module)]
    err = run(cmd)
    if err:
        bad.append(f"Rust module: {err}")
    try:
        compile(emit_python.emit(mission, stem), f"{stem}.py", "exec")
    except SyntaxError as e:
        bad.append(f"Python module: {e}")
    return bad


def check(path: Path, out: Path) -> list[str]:
    """The summary lines of one document."""
    valid = schema_valid(path)
    try:
        doc = xtce.Document.from_file(str(path))
    except xtce.XtceError:
        return [f"schema {'valid' if valid else 'invalid'}; rejected by the binding"] + (
            ["DISAGREE: the schema accepts it"] if valid else []
        )
    lines = [f"schema {'valid' if valid else 'invalid'}; loaded by the binding"]
    if not valid:
        lines.append("DISAGREE: the schema rejects it")
    if xtce.Document.from_bytes(doc.to_bytes()).root != doc.root:
        lines.append("ROUND TRIP DIFFERS")
    try:
        mission = compile_document(doc.root, skip_unsupported=True)
        report.report(mission)
    except Unsupported as e:
        return lines + [f"not compiled: {e}"]
    n = len(mission.nodes)
    lines.append(f"{n} {'packet' if n == 1 else 'packets'}, {len(mission.skipped)} not supported")
    lines += [f"  unsupported: {m}" for m in mission.skipped]
    if mission.nodes:
        stem = re.sub(r"\W", "_", path.stem).lower()
        lines += [f"  FAILED: {x}" for x in build(mission, stem, out)]
    return lines


def main() -> int:
    update = sys.argv[1:] == ["--update"]
    if not RLIB.exists():
        print(f"samples_check: {RLIB} is missing; run make rust", file=sys.stderr)
        return 1
    files = EXTERNAL / "files"
    out_lines = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, digest in sources():
            path = files / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                print(f"samples_check: {path} does not have the SHA-256 in sources.txt", file=sys.stderr)
                return 1
            try:
                lines = check(path, Path(tmp))
            except Exception as e:  # a crash is a finding, so keep going
                lines = [f"CRASH {type(e).__name__}: {e}"]
            out_lines.append(f"{name}: {lines[0]}")
            out_lines += [f"  {x}" for x in lines[1:]]
    text = "\n".join(out_lines) + "\n"
    expected = EXTERNAL / "expected.txt"
    if update:
        expected.write_text(text)
        print(f"samples_check: wrote {expected}")
        return 0
    if text != expected.read_text():
        got = EXTERNAL / "files" / "results.txt"
        got.write_text(text)
        print(f"samples_check: results differ from {expected}:", file=sys.stderr)
        subprocess.run(["diff", str(expected), str(got)])
        return 1
    # These are failures even when expected.txt records them.
    flagged = [x for x in out_lines if re.search(r"CRASH|DISAGREE|DIFFERS|FAILED", x)]
    for x in flagged:
        print(f"samples_check: {x.strip()}", file=sys.stderr)
    if flagged:
        return 1
    print(f"samples_check: {len(sources())} documents match {expected.relative_to(TOP)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
