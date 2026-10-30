"""Check tcs-xtce against public XTCE documents.

``make samples-check`` downloads the documents listed in
tests/external/sources.txt into tests/external/files/ and runs this script,
which for each document checks that

1. the XTCE binding accepts it exactly when ``xmllint`` finds it valid
   against the XTCE 1.3 schema.  Like the binding, which doesn't check
   namespaces, this reads older XTCE namespaces, and a document without
   one, as 1.3; and since the binding leaves facets (patterns and ranges of
   values) to xmllint, a document that breaks only facets may be accepted;
2. a document the binding accepts writes back to an equal document;
3. the packet compiler, the size report and the three emitters never crash:
   they either produce output or report an unsupported construct;
4. the generated code compiles: the C header as C11 and as C++, the Rust
   module against the tcs-xtce crate, and the Python module;
5. the Rust, C, C++ and Python runtimes give it the same XTCE digest;

Documents from Yamcs projects are compiled in the Yamcs dialect.
It also compiles the missions in tests/external/missions.txt, each split
across several of the documents, together as one, and checks 3 to 5 for
each (5 for the set's one digest).

It prints one summary per document and per mission, which must equal
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
sys.path[:0] = [str(TOP / "python")]

import tcs_xtce  # noqa: E402
from tcs_xtce.binding.__main__ import rustfmt  # noqa: E402
from tcs_xtce.compiler import (  # noqa: E402
    emit_c, emit_python, emit_rust, report)
from tcs_xtce.compiler.compile import compile_documents  # noqa: E402
from tcs_xtce.compiler.model import Unsupported  # noqa: E402
from tcs_xtce.digest import digest_file, digest_files  # noqa: E402

XSD = TOP / "references" / "SpaceSystem.xsd"
CATALOG = TOP / "references" / "catalog.xml"
NS13 = b"http://www.omg.org/spec/XTCE/20250214"
OLD_NS = re.compile(rb"https?://www\.omg\.org/(space/xtce|spec/XTCE/\d{8})"
                    rb"(/SpaceSystem\.xsd)?")
RLIB = TOP / "rust" / "target" / "release" / "libtcs_xtce.rlib"
# The digest tools of the other runtimes, which print one digest per file,
# or with --set, the digest of the set.
DIGEST_TOOLS = [TOP / "rust" / "target" / "release" / "examples"
                / "digest_tool", TOP / "cpp" / "build" / "digest_tool",
                TOP / "cpp" / "build" / "digest_tool_cxx"]


def sources() -> list[tuple[str, str]]:
    """The documents listed in sources.txt.

    Returns:
        The local name and SHA-256, in hex, of each.
    """
    out = []
    for line in (EXTERNAL / "sources.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, digest, _url = line.split()
            out.append((name, digest))
    return out


def missions() -> list[tuple[str, list[str]]]:
    """The missions listed in missions.txt.

    Returns:
        The name of each and the local names of its documents.
    """
    out = []
    for line in (EXTERNAL / "missions.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if line[0].isspace():
            out[-1][1].append(line.strip())
        else:
            out.append((line.strip().removesuffix(":"), []))
    return out


def schema_verdict(path: Path) -> str:
    """Validates a document against the XTCE 1.3 schema with xmllint.

    Older XTCE namespaces, and a root without one, are read as 1.3, and
    the document's own schemaLocation is ignored.

    Args:
        path: The document.

    Returns:
        "valid", "invalid", or "facets" when only facets (patterns and
        ranges of values) are broken.
    """
    data = OLD_NS.sub(NS13, path.read_bytes())
    # A root without a namespace is read as XTCE 1.3, as the binding reads
    # it.
    if not re.search(rb"<(\w+:)?SpaceSystem\b[^>]*\sxmlns(:\w+)?=", data):
        data = re.sub(rb"<SpaceSystem\b", b'<SpaceSystem xmlns="' + NS13 + b'"',
                      data, count=1)
    # Validate against the local schema, not the one a document points at.
    data = re.sub(rb'\s+xsi:schemaLocation="[^"]*"', b"", data)
    with tempfile.NamedTemporaryFile(suffix=".xml") as t:
        t.write(data)
        t.flush()
        r = subprocess.run(
            ["xmllint", "--noout", "--nonet", "--schema", str(XSD), t.name],
            capture_output=True, text=True,
            env={**os.environ, "XML_CATALOG_FILES": str(CATALOG)})
    if r.returncode == 0:
        return "valid"
    errors = [x for x in r.stderr.splitlines() if "validity error" in x]
    if errors and all("[facet " in x for x in errors):
        return "facets"
    return "invalid"


def run(cmd: list[str]) -> str:
    """Runs a compiler.

    Args:
        cmd: The command and its arguments.

    Returns:
        "" on success; otherwise its first line that mentions an error, or
        its first line of output, or its exit status if it printed nothing.
    """
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        return ""
    lines = ([x for x in (r.stderr + r.stdout).splitlines() if "error" in x]
             or (r.stderr + r.stdout).splitlines())
    return lines[0].strip() if lines else f"exit {r.returncode}"


def build(mission, stem: str, out: Path) -> list[str]:
    """Generates code in every language and compiles it: the C header as
    C11 and as C++20, the Rust module against the tcs-xtce crate, and the
    Python module.

    Args:
        mission: The compiled mission.
        stem:    The generated files' and modules' base name.
        out:     The directory to write the generated files in.

    Returns:
        A line for each failure; empty if everything compiles.
    """
    bad = []
    header = out / f"{stem}.h"
    header.write_text(emit_c.emit(mission, stem))
    for lang, compiler in (("c", "cc"), ("c++", "c++")):
        std = "-std=c11" if lang == "c" else "-std=c++20"
        cmd = [os.environ.get("CC" if lang == "c" else "CXX", compiler), std,
               "-Wall", "-Wextra", "-Werror"]
        cmd += [f"-I{TOP / 'cpp' / 'include'}", "-fsyntax-only", "-x", lang,
                str(header)]
        err = run(cmd)
        if err:
            bad.append(f"C header as {lang}: {err}")
    module = out / f"{stem}.rs"
    module.write_text(rustfmt(emit_rust.emit(mission, stem)))
    cmd = ["rustc", "--edition", "2021", "--crate-type", "lib", "--crate-name",
           "sample", "-D", "warnings"]
    cmd += ["--extern", f"tcs_xtce={RLIB}", "-L",
            f"dependency={RLIB.parent / 'deps'}"]
    cmd += ["--emit", "metadata", "-o", str(out / f"lib{stem}.rmeta"),
            str(module)]
    err = run(cmd)
    if err:
        bad.append(f"Rust module: {err}")
    try:
        compile(emit_python.emit(mission, stem), f"{stem}.py", "exec")
    except SyntaxError as e:
        bad.append(f"Python module: {e}")
    return bad


def digests(paths: list[Path]) -> dict:
    """Each document's digest line, from the Python runtime and the digest
    tools of the others.

    Args:
        paths: The documents.

    Returns:
        For each path, "digest" and the digest, or that the runtimes
        disagree about it.  Python's digest is "error" when it can't read
        the document.

    Raises:
        subprocess.CalledProcessError: A digest tool fails.
    """
    results = [subprocess.run([str(t)] + [str(p) for p in paths],
                              capture_output=True, text=True,
                              check=True).stdout.split() for t in DIGEST_TOOLS]
    out = {}
    for i, p in enumerate(paths):
        try:
            mine = digest_file(p)
        except (OSError, tcs_xtce.XtceError):
            mine = "error"
        others = {r[i] for r in results}
        out[p] = (f"digest {mine}" if others == {mine}
                  else f"DIGESTS DIFFER: Python {mine}, others "
                       f"{' '.join(sorted(others))}")
    return out


def set_digest(paths: list[Path]) -> str:
    """The digest line of a set of documents, as digests() gives one
    document's.

    Args:
        paths: The documents.

    Returns:
        "digest" and the set's digest, or that the runtimes disagree
        about it.

    Raises:
        subprocess.CalledProcessError: A digest tool fails.
    """
    try:
        mine = digest_files(paths)
    except (OSError, tcs_xtce.XtceError):
        mine = "error"
    others = {subprocess.run([str(t), "--set"] + [str(p) for p in paths],
                             capture_output=True, text=True,
                             check=True).stdout.strip() for t in DIGEST_TOOLS}
    return (f"digest {mine}" if others == {mine}
            else f"DIGESTS DIFFER: Python {mine}, others "
                 f"{' '.join(sorted(others))}")


def dialect(paths: list[Path]) -> str | None:
    """The dialect of XTCE the documents follow: Yamcs's, for those from
    Yamcs projects."""
    files = EXTERNAL / "files"
    yamcs = {p.relative_to(files).parts[0].startswith("yamcs") for p in paths}
    return "yamcs" if yamcs == {True} else None


def compiled(roots: list, stem: str, out: Path,
             dialect: str | None) -> list[str]:
    """The summary lines of compiling documents together and building the
    code generated from them.

    Args:
        roots:   The documents' root SpaceSystems.
        stem:    The generated files' base name.
        out:     The directory to write the generated files in.
        dialect: The XTCE dialect to compile in, or None for standard XTCE.

    Returns:
        The packet and unsupported counts, then each unsupported construct
        and each build failure; or only that it wasn't compiled.
    """
    try:
        mission = compile_documents(roots, skip_unsupported=True,
                                    dialect=dialect)
        report.report(mission)
    except Unsupported as e:
        return [f"not compiled: {e}"]
    n = len(mission.nodes)
    lines = [f"{n} {'packet' if n == 1 else 'packets'}, {len(mission.skipped)} "
             "not supported"]
    lines += [f"  unsupported: {m}" for m in mission.skipped]
    if mission.nodes:
        lines += [f"  FAILED: {x}" for x in build(mission, stem, out)]
    return lines


def check_mission(paths: list[Path], stem: str, out: Path) -> list[str]:
    """The summary lines of one mission split across several documents.

    Args:
        paths: The mission's documents.
        stem:  The generated files' base name.
        out:   The directory to write the generated files in.

    Returns:
        The lines compiled() gives, or that the binding rejects a document.
    """
    roots = []
    for p in paths:
        try:
            roots.append(tcs_xtce.Document.from_file(str(p)).root)
        except tcs_xtce.XtceError:
            return [f"not compiled: the binding rejects {p.name}"]
    return compiled(roots, stem, out, dialect(paths))


def check(path: Path, out: Path) -> list[str]:
    """The summary lines of one document: its schema verdict, whether the
    binding loads it and agrees, whether it writes back to an equal
    document, and the lines compiled() gives.

    Args:
        path: The document.
        out:  The directory to write the generated files in.

    Returns:
        The lines, the first the schema verdict and the binding's.
    """
    verdict = schema_verdict(path)
    schema = {"valid": "schema valid", "invalid": "schema invalid",
              "facets": "schema invalid (facets only)"}[verdict]
    try:
        doc = tcs_xtce.Document.from_file(str(path))
    except tcs_xtce.XtceError:
        return ([f"{schema}; rejected by the binding"]
                + (["DISAGREE: the schema accepts it"] if verdict == "valid"
                   else []))
    lines = [f"{schema}; loaded by the binding"]
    if verdict == "invalid":
        lines.append("DISAGREE: the schema rejects it")
    if tcs_xtce.Document.from_bytes(doc.to_bytes()).root != doc.root:
        lines.append("ROUND TRIP DIFFERS")
    stem = re.sub(r"\W", "_", path.stem).lower()
    return lines + compiled([doc.root], stem, out, dialect([path]))


def main() -> int:
    """Checks every document and mission and compares the summaries with
    expected.txt, or with --update, rewrites it.

    Returns:
        The exit status: 0 if the summaries match and flag nothing, else 1,
        also when a tool is missing or a document's SHA-256 is wrong.
    """
    update = sys.argv[1:] == ["--update"]
    for need in [RLIB] + DIGEST_TOOLS:
        if not need.exists():
            print(f"samples_check: {need} is missing; run make rust cpp",
                  file=sys.stderr)
            return 1
    files = EXTERNAL / "files"
    out_lines = []
    digest_of = digests([files / name for name, _ in sources()])
    with tempfile.TemporaryDirectory() as tmp:
        for name, digest in sources():
            path = files / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                print(f"samples_check: {path} does not have the SHA-256 in "
                      "sources.txt", file=sys.stderr)
                return 1
            try:
                lines = check(path, Path(tmp))
            except Exception as e:  # a crash is a finding, so keep going
                lines = [f"CRASH {type(e).__name__}: {e}"]
            out_lines.append(f"{name}: {lines[0]}")
            out_lines += [f"  {x}" for x in lines[1:]]
            out_lines.append(f"  {digest_of[path]}")
        for name, docs in missions():
            paths = [files / d for d in docs]
            try:
                lines = check_mission(
                    paths, "mission_" + re.sub(r"\W", "_", name), Path(tmp))
            except Exception as e:  # a crash is a finding, so keep going
                lines = [f"CRASH {type(e).__name__}: {e}"]
            out_lines.append(f"mission {name} ({len(docs)} documents): "
                             f"{lines[0]}")
            out_lines += [f"  {x}" for x in lines[1:]]
            out_lines.append(f"  {set_digest(paths)}")
    text = "\n".join(out_lines) + "\n"
    expected = EXTERNAL / "expected.txt"
    if update:
        expected.write_text(text)
        print(f"samples_check: wrote {expected}")
        return 0
    if text != expected.read_text():
        got = EXTERNAL / "files" / "results.txt"
        got.write_text(text)
        print(f"samples_check: results differ from {expected}:",
              file=sys.stderr)
        subprocess.run(["diff", str(expected), str(got)])
        return 1
    # These are failures even when expected.txt records them.
    flagged = [x for x in out_lines
               if re.search(r"CRASH|DISAGREE|DIFFERS|FAILED", x)]
    for x in flagged:
        print(f"samples_check: {x.strip()}", file=sys.stderr)
    if flagged:
        return 1
    print(f"samples_check: {len(sources())} documents and {len(missions())} "
          f"missions match {expected.relative_to(TOP)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
