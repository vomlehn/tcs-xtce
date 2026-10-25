"""Check that no line of code is wider than 80 columns, counting a tab as 8.

Checks every source file in git (Rust, C, C++, Python and shell), every
Makefile, every script (a file in git that starts with ``#!``), and the
code the generators write, when it exists.  Prose (reStructuredText,
Markdown) and data (XML, TOML) aren't code and aren't checked.

usage: python3 -I tests/line_width.py   (from the top of the repository)
Prints each line that is too wide and exits with status 1 if there are any.
"""

import glob
import subprocess
import sys
from pathlib import Path

WIDTH = 80
SOURCE = {".py", ".rs", ".c", ".h", ".cpp", ".hpp", ".sh"}

# Generated code, which git doesn't keep; see .gitignore.
GENERATED = ["rust/src/generated.rs", "cpp/include/xtce/generated.hpp",
             "cpp/src/generated.cpp", "python/xtce/generated.py",
             "rust/tests/mission/demo*.rs", "cpp/tests/mission/demo*.h",
             "python/tests/mission/demo*.py",
             "examples/*/rust/src/*.rs", "examples/*/c/*.h",
             "examples/*/cxx/*.h", "examples/*/python/*.py"]


def is_script(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def files() -> list[Path]:
    tracked = subprocess.run(["git", "ls-files"], capture_output=True,
                             text=True, check=True).stdout.split()
    out = set()
    for name in tracked:
        p = Path(name)
        if p.suffix in SOURCE or p.name == "Makefile" or is_script(p):
            out.add(p)
    for pattern in GENERATED:
        out.update(Path(p) for p in glob.glob(pattern))
    return sorted(p for p in out if p.is_file())


def main() -> int:
    bad = 0
    checked = files()
    for p in checked:
        text = p.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.split("\n"), 1):
            width = len(line.expandtabs(8))
            if width > WIDTH:
                print(f"{p}:{n}: {width} columns", file=sys.stderr)
                bad += 1
    if bad:
        print(f"line_width: {bad} lines wider than {WIDTH} columns",
              file=sys.stderr)
        return 1
    print(f"line_width: {len(checked)} files, no line wider than {WIDTH} "
          "columns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
