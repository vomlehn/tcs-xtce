"""Compare the Rust model with the Python compiler's.

The compiler's analysis is being ported to Rust a stage at a time (see
docs/rust-migration-design.rst). For each mission the tests compile, this
writes the model as canonical text with both the Python compiler and the
Rust model's ``model_dump`` example, and fails at the first line where
they differ. A document that the binding rejects must be rejected by both.

The stages compared so far:

- ``--dump-types``: every parameter and argument type of the mission,
  each resolved alone, or the error it gives.
- ``--dump-containers``: every telemetry container, each compiled as
  ``tm_node`` compiles it, before the mission is finished.
- ``--dump-commands``: every meta-command and message, each compiled as
  ``tc_node`` and ``msg_node`` compile it, after the containers.

The missions are the demo mission, the examples (the split mission's three
documents together) and the schema-coverage samples; with ``--external``,
the public documents ``make samples-check`` downloads too, those from
Yamcs projects in the Yamcs dialect, and its missions of several of them.

Usage: python3 -I tests/model_check.py MODEL_DUMP [--external]
"""

from __future__ import annotations

import difflib
import subprocess
import sys
from pathlib import Path

TOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOP / "python"))

import tcs_xtce  # noqa: E402
from tcs_xtce.compiler.compile import Compiler  # noqa: E402
from tcs_xtce.compiler.dump import (  # noqa: E402
    dump_commands, dump_containers, dump_types)
from tcs_xtce.compiler.model import Cycle, Unsupported  # noqa: E402

EXTERNAL = TOP / "tests" / "external"

# The missions of the repository, each a list of its documents.
MISSIONS = ([["tests/mission/demo.xtce"]]
            + [[str(p.relative_to(TOP))]
               for p in sorted((TOP / "examples").glob("*/*.xtce"))
               if p.parent.name != "split-mission"]
            + [["examples/split-mission/pus.xtce",
                "examples/split-mission/dt.xtce",
                "examples/split-mission/rover.xtce"]]
            + [[str(p.relative_to(TOP))]
               for p in sorted((TOP / "tests" / "samples").glob("*.xtce"))])


def external() -> list[tuple[list[str], str | None]]:
    """Lists the public documents and missions, if they are downloaded.

    Returns:
        Each mission's documents and its dialect: each document alone,
        then the missions of tests/external/missions.txt.
    """
    files = EXTERNAL / "files"
    if not files.is_dir():
        return []

    def dialect(paths: list[str]) -> str | None:
        """Gives the dialect of documents: Yamcs's for Yamcs projects'.

        Args:
            paths: The documents, relative to the top of the repository.

        Returns:
            ``yamcs``, or None.
        """
        yamcs = {Path(
                     p).relative_to(
                     files.relative_to(TOP)).parts[0].startswith("yamcs")
                 for p in paths}
        return "yamcs" if yamcs == {True} else None

    out = []
    for p in sorted(files.rglob("*")):
        if p.suffix in (".xml", ".xtce") and p.is_file():
            docs = [str(p.relative_to(TOP))]
            out.append((docs, dialect(docs)))
    listing = EXTERNAL / "missions.txt"
    missions: list[list[str]] = []
    if listing.exists():
        # "NAME:" starts a mission; its documents are indented after it.
        for line in listing.read_text().splitlines():
            text = line.split("#", 1)[0].rstrip()
            if not text.strip():
                continue
            if not text[0].isspace():
                missions.append([])
            elif missions:
                missions[-1].append(str((files
                                         / text.strip()).relative_to(TOP)))
    for docs in missions:
        if docs and all((TOP / d).exists() for d in docs):
            out.append((docs, dialect(docs)))
    return out


# The stages compared: each one's name, the Python function that writes
# it and the model_dump option that does.
STAGES = [("types", dump_types, "--types"),
          ("containers", dump_containers, "--containers"),
          ("commands", dump_commands, "--commands")]



def python_dump(docs: list[str], dialect: str | None, write) -> str | None:
    """Writes one stage of a mission's model with the Python compiler.

    Args:
        docs:    The documents, relative to the top of the repository.
        dialect: The dialect to read them in, or None.
        write:   The function that writes the stage from a Compiler.

    Returns:
        The canonical text, an ``error:`` line if the documents don't
        index, or None if the binding rejects one.
    """
    roots = []
    for d in docs:
        try:
            roots.append(tcs_xtce.Document.from_file(str(TOP / d)).root)
        except tcs_xtce.XtceError:
            return None
    try:
        compiler = Compiler(roots, False, dialect)
    except (Unsupported, Cycle) as e:
        return f"error: {e}\n"
    return write(compiler)


def rust_dump(tool: str, docs: list[str], dialect: str | None,
              option: str) -> str | None:
    """Writes one stage of a mission's model with the Rust model.

    Args:
        tool:    The ``model_dump`` example.
        docs:    The documents, relative to the top of the repository.
        dialect: The dialect to read them in, or None.
        option:  The option that names the stage.

    Returns:
        The canonical text, an ``error:`` line if the documents don't
        index, or None if the binding rejects one.
    """
    args = [tool, option] + ([f"--dialect={dialect}"] if dialect else [])
    r = subprocess.run(args + [str(TOP / d) for d in docs], capture_output=True,
                       text=True)
    if r.returncode == 0:
        return r.stdout
    if r.returncode == 3:  # the binding rejects a document
        return None
    err = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
    prefix = f"tcs-xtce: {TOP / docs[0]}: " if len(docs) == 1 else "tcs-xtce: "
    return f"error: {err.removeprefix(prefix)}\n"


def main() -> int:
    """Compares the two models on every mission, stage by stage.

    Returns:
        0 if they agree on all of them, 1 otherwise.
    """
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    tool = args[0]
    missions = [(m, None) for m in MISSIONS]
    if "--external" in args[1:]:
        missions += external()
    compared = rejected = 0
    failed = []
    for docs, dialect in missions:
        name = " ".join(docs) + (f" ({dialect})" if dialect else "")
        for stage, write, option in STAGES:
            py = python_dump(docs, dialect, write)
            rs = rust_dump(tool, docs, dialect, option)
            if py is None and rs is None:
                rejected += stage == STAGES[0][0]
                break
            compared += stage == STAGES[0][0]
            if py == rs:
                continue
            failed.append(f"{name} {stage}")
            diff = difflib.unified_diff((py or "rejected\n").splitlines(True),
                                        (rs or "rejected\n").splitlines(True),
                                        "python", "rust", n=2)
            print(f"model_check: {name}: the {stage} differ", file=sys.stderr)
            sys.stderr.writelines(list(diff)[:40])
    if failed:
        print(f"model_check: {len(failed)} stages of {compared} missions "
              "differ", file=sys.stderr)
        return 1
    stages = " and ".join(s for s, _, _ in STAGES)
    print(f"model_check: the Python and Rust {stages} agree on {compared} "
          "missions"
          + (f"; {rejected} the binding rejects" if rejected else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
