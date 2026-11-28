"""Turn the rustdoc sections that cbindgen copies into a C header into
Doxygen's, so that the header documents its functions as C does.

In each ``/** ... */`` comment, ``# Arguments`` and its list of
``* `name` - text`` become ``@param`` lines whose descriptions share a
column, ``# Returns`` becomes ``@return`` in that column, and any other
section becomes an ``@par`` paragraph.

Usage: python3 doxygen.py HEADER
"""

import re
import sys
import textwrap

ITEM = re.compile(r"^\* `([^`]+)`\s+- (.*)$")
SECTION = re.compile(r"^# (\w+)$")


def fill(text: list[str], col: int, width: int) -> list[str]:
    """Wraps a description that starts in a column.

    Args:
        text:  Its lines.
        col:   The column it starts in.
        width: The columns it may take, the comment's ``*`` and indentation
               left out.

    Returns:
        Its lines, rewrapped, the first without the column's indentation.
    """
    lines = textwrap.wrap(" ".join(text), max(width - col, 20))
    return lines[:1] + [" " * col + t for t in lines[1:]]


def comment(body: list[str], width: int) -> list[str]:
    """Converts the text of one comment.

    Args:
        body:  Its lines, without the leading `` * ``.
        width: The columns its text may take.

    Returns:
        The lines in Doxygen's form.
    """
    out: list[str] = []
    params: list[tuple[str, list[str]]] = []
    returns: list[str] = []
    others: list[tuple[str, list[str]]] = []
    section = None
    for line in body:
        m = SECTION.match(line.strip())
        if m:
            section = m.group(1)
            if section not in ("Arguments", "Returns"):
                others.append((section, []))
            continue
        if section is None:
            out.append(line)
        elif section == "Arguments":
            item = ITEM.match(line.strip())
            if item:
                params.append((item.group(1), [item.group(2)]))
            elif line.strip() and params:
                params[-1][1].append(line.strip())
        elif section == "Returns":
            if line.strip():
                returns.append(line.strip())
        elif line.strip() or others[-1][1]:
            others[-1][1].append(line.strip())
    while out and not out[-1].strip():
        out.pop()
    longest = max((len(n) for n, _ in params), default=0)
    col = len("@param ") + longest + 2
    if params or returns:
        out.append("")
    for name, text in params:
        lines = fill(text, col, width)
        out.append(f"@param {name}".ljust(col) + lines[0])
        out.extend(lines[1:])
    if returns:
        pad = max(col, len("@return "))
        lines = fill(returns, pad, width)
        out.append("@return".ljust(pad) + lines[0])
        out.extend(lines[1:])
    for name, text in others:
        while text and not text[-1]:
            text.pop()
        out.append("")
        out.append(f"@par {name}")
        out.extend(text)
    return out


def convert(text: str) -> str:
    """Converts every comment of a header.

    Args:
        text: The header.

    Returns:
        The header with its comments in Doxygen's form.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        start = re.match(r"^(\s*)/\*\*$", lines[i])
        if not start:
            out.append(lines[i])
            i += 1
            continue
        indent = start.group(1)
        j = i + 1
        body = []
        while lines[j].strip() != "*/":
            body.append(re.sub(r"^\s*\* ?", "", lines[j], count=1))
            j += 1
        out.append(f"{indent}/**")
        for b in comment(body, 80 - len(indent) - 3):
            out.append(f"{indent} * {b}".rstrip())
        out.append(f"{indent} */")
        i = j + 1
    return "\n".join(out)


def main() -> None:
    """Converts the header named on the command line, in place."""
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write(convert(text))


if __name__ == "__main__":
    main()
