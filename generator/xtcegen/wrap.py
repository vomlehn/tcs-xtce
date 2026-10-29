"""Wrap generated code to a maximum line width.

The generators build each statement on one line and call ``wrap`` on the
finished text, so that every line fits in ``WIDTH`` columns where it can:

* comments and docstrings are rewrapped at spaces, and a comment after
  code moves to its own lines above the code;
* a code line is broken after a comma, a semicolon or an opening bracket,
  or before ``&&``, ``||`` (C and C++) or ``and``, ``or``, ``if``,
  ``else``, ``for`` (Python), with continuation lines indented 8 columns
  past the line's own indentation;
* Python breaks only inside brackets, so a Python assignment or ``return``
  that can't break otherwise has its value put in parentheses;
* a string literal too long to fit is split into adjacent literals, which
  C, C++ and Python join back together.

For Rust, rustfmt lays out the code; ``wrap`` rewraps the comments and
breaks only code lines that rustfmt left too long.

A line that can't be broken, such as one holding a single long word, is
left as it is.  Python is then laid out in the compact aligned style of the
hand-written code (see ``pyalign``).
"""

from __future__ import annotations

import re

from . import pyalign

WIDTH = 80

# What continuation lines add to the indentation of the line they continue.
CONTINUATION = 8

_OPEN = "([{"
_CLOSE = ")]}"
_C_OPERATORS = ("&&", "||")
_PY_KEYWORDS = ("and", "or", "if", "else", "for")


def wrap(text: str, lang: str, width: int = WIDTH) -> str:
    """Wraps generated code so that its lines fit, where they can.

    Args:
        text:  The code.
        lang:  Its language: "c" (C or C++), "python" or "rust".
        width: The widest a line may be.

    Returns:
        The wrapped code; Python is also laid out in the aligned style
        when ``width`` is pyalign's.

    Raises:
        ValueError: If ``lang`` is none of those.
    """
    if lang not in ("c", "python", "rust"):
        raise ValueError(f"unknown language {lang!r}")
    w = _Wrapper(lang, width)
    out: list[str] = []
    for line in text.split("\n"):
        out.extend(w.line(line))
    text = "\n".join(out)
    if lang == "python" and width == pyalign.WIDTH:
        # Lay out the statements that now span lines as hand-written
        # Python is: continuation lines under the brackets they continue.
        text = pyalign.align(text)
    return text


class _Scan:
    """What one line of code holds, as far as wrapping it is concerned."""

    def __init__(self) -> None:
        # Where each piece of the line may start on a new line: (index,
        # bracket depth there).
        self.breaks: list[tuple[int, int]] = []
        # Other places it may start, used only when none of the above fit.
        self.fallbacks: list[tuple[int, int]] = []
        # Breaks before the statement of an `if (...)` and the like, which
        # win over later breaks.
        self.statements: list[int] = []
        # String literals: (start, end, prefix, quote), end exclusive.
        self.strings: list[tuple[int, int, str, str]] = []
        # Where a comment after the code starts, or -1.
        self.comment = -1


class _Wrapper:
    def __init__(self, lang: str, width: int) -> None:
        """Starts wrapping a file.

        Args:
            lang:  Its language: "c", "python" or "rust".
            width: The widest a line may be.
        """
        self.lang = lang
        self.width = width
        # State carried from line to line.
        self.depth = 0  # open brackets
        self.block = False  # inside a C block comment
        self.triple: str | None = None  # inside a Python triple-quoted string
        # The indentation for the next line of a Rust string literal that
        # continues after a backslash, or None.
        self.rust_string: str | None = None

    # -- lines --

    def line(self, line: str) -> list[str]:
        """Wraps one line, given what the lines before it left open.

        Args:
            line: The line, without its newline.

        Returns:
            The lines it becomes.
        """
        if self.lang == "rust":
            return self._rust(line)
        if self.lang == "c" and self.block:
            return self._c_block(line)
        if self.lang == "python" and self.triple:
            return self._docstring_body(line)
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip(" "))]
        if self.lang == "c":
            if stripped.startswith("//"):
                return self._comment(line, indent, "// ", stripped[2:])
            if stripped.startswith("/*"):
                return self._c_block_start(line, indent, stripped)
            if stripped.startswith("#"):
                # Preprocessor lines would need a backslash at each break.
                self._scan(line)
                return [line]
        else:
            if stripped.startswith("#"):
                return self._comment(line, indent, "# ", stripped[1:])
            m = re.match(r'[rRuU]?("""|\'\'\')', stripped)
            if m:
                return self._docstring_start(line, indent, stripped, m)
        return self._code(line, indent)

    def _rust(self, line: str) -> list[str]:
        """Wraps one line of Rust that rustfmt has laid out: rewraps a
        comment that is too long and breaks code rustfmt left too long.

        Args:
            line: The line.

        Returns:
            The lines it becomes; the line itself if it fits or holds a char
            literal, a lifetime or a continued string.
        """
        if self.rust_string is not None:
            # Rust skips the whitespace that starts a continued string line.
            line = self.rust_string + line.lstrip()
            self.rust_string = None
        stripped = line.strip()
        if not stripped.startswith("//") and _rust_continues(line):
            indent = line[: len(line) - len(line.lstrip(" "))]
            self.rust_string = indent + " " * CONTINUATION
        m = re.match(r"(///|//!|//)\s?", stripped)
        if len(line) <= self.width:
            return [line]
        indent = line[: len(line) - len(line.lstrip(" "))]
        if m is None:
            # Code rustfmt couldn't lay out. Rust allows a newline between
            # any two tokens, so break it as C would, unless it holds a char
            # literal or a lifetime, which the C scanner would misread, or
            # continues a string.
            if "'" in line or self.rust_string is not None:
                return [line]
            return _Wrapper("c", self.width)._code(line, indent)
        marker = m.group(1) + " "
        return self._comment(line, indent, marker, stripped[m.end() :])

    # -- comments --

    def _comment(self, line: str, indent: str, marker: str,
                 text: str) -> list[str]:
        """Rewraps a ``//`` or ``#`` comment line that is too long.

        Args:
            line:   The line.
            indent: Its indentation.
            marker: The comment marker and its space, repeated on each line.
            text:   The comment's text.

        Returns:
            The lines it becomes.
        """
        if len(line) <= self.width:
            return [line]
        return _fill(text.strip(), indent + marker, indent + marker, self.width)

    def _c_block_start(self, line: str, indent: str,
                       stripped: str) -> list[str]:
        """Wraps a line that starts a block comment: ``/* ... */`` or
        ``/* ...``.

        Args:
            line:     The line.
            indent:   Its indentation.
            stripped: The line without its indentation.

        Returns:
            The lines it becomes; the line itself if it fits or code follows
            the comment.
        """
        end = stripped.find("*/", 2)
        if end >= 0 and stripped[end + 2 :].strip():
            # Code follows the comment; leave it alone.
            self._scan(line)
            return [line]
        closed = end >= 0
        self.block = not closed
        if len(line) <= self.width:
            return [line]
        text = stripped[2:end] if closed else stripped[2:]
        lines = _fill(text.strip(), indent + "/* ", indent + " * ", self.width)
        if closed:
            lines = _close(lines, " */", indent + " ", self.width)
        return lines

    def _c_block(self, line: str) -> list[str]:
        """Wraps a line inside a block comment.

        Args:
            line: The line.

        Returns:
            The lines it becomes; the line itself if it fits or doesn't start
            with ``*``.
        """
        stripped = line.strip()
        closed = "*/" in stripped
        if closed:
            self.block = False
        if len(line) <= self.width or not stripped.startswith("*"):
            return [line]
        indent = line[: len(line) - len(line.lstrip(" "))]
        text = stripped[1:]
        if closed:
            text = text[: text.rfind("*/")]
        lines = _fill(text.strip(), indent + "* ", indent + "* ", self.width)
        if closed:
            lines = _close(lines, " */", indent, self.width)
        return lines

    def _docstring_start(self, line: str, indent: str, stripped: str,
                         m: re.Match) -> list[str]:
        """Wraps a line that starts a Python triple-quoted string.

        Args:
            line:     The line.
            indent:   Its indentation.
            stripped: The line without its indentation.
            m:        The match of the string's prefix and opening quotes.

        Returns:
            The lines it becomes; the line itself if it fits or code follows
            the string.
        """
        quote = m.group(1)
        rest = stripped[m.end() :]
        end = rest.find(quote)
        if end >= 0 and rest[end + 3 :].strip():
            return [line]  # code follows the string
        closed = end >= 0
        if not closed:
            self.triple = quote
        if len(line) <= self.width:
            return [line]
        opening = stripped[: m.end()]
        text = rest[:end] if closed else rest
        lines = _fill(text.strip(), indent + opening, indent, self.width)
        if closed:
            lines = _close(lines, quote, indent, self.width)
        return lines

    def _docstring_body(self, line: str) -> list[str]:
        """Wraps a line inside a Python triple-quoted string.

        Args:
            line: The line.

        Returns:
            The lines it becomes; the line itself if it fits, is blank or has
            code after the closing quotes.
        """
        quote = self.triple
        assert quote is not None
        end = line.find(quote)
        if end >= 0:
            self.triple = None
            if line[end + 3 :].strip():
                return [line]
        if len(line) <= self.width or not line.strip():
            return [line]
        indent = line[: len(line) - len(line.lstrip(" "))]
        text = line[:end] if end >= 0 else line
        lines = _fill(text.strip(), indent, indent, self.width)
        if end >= 0:
            lines = _close(lines, quote, indent, self.width)
        return lines

    # -- code --

    def _code(self, line: str, indent: str) -> list[str]:
        """Wraps a line of code, moving a comment after the code to its own
        lines above it.

        Args:
            line:   The line.
            indent: Its indentation.

        Returns:
            The lines it becomes.
        """
        start_depth = self.depth
        scan = self._scan(line)
        if len(line) <= self.width:
            return [line]
        lines: list[str] = []
        if scan.comment >= 0:
            # Move the comment above the code.
            code = line[: scan.comment].rstrip()
            comment = line[scan.comment :]
            if self.lang == "c" and comment.startswith("/*"):
                text = comment[2 : comment.rfind("*/")].strip()
                body = _fill(text, indent + "/* ", indent + " * ", self.width)
                lines += _close(body, " */", indent + " ", self.width)
            else:
                marker = "// " if self.lang == "c" else "# "
                text = comment[2:] if self.lang == "c" else comment[1:]
                lines += _fill(text.strip(), indent + marker, indent + marker,
                               self.width)
            line = code
            self.depth = start_depth
            scan = self._scan(line)
            if len(line) <= self.width:
                return lines + [line]
        end_depth = self.depth
        broken = self._break(line, indent, start_depth, scan)
        if self.lang == "python" and max(map(len, broken)) > self.width:
            wrapped = self._parenthesize(line, indent, start_depth)
            if (wrapped is not None
                    and max(map(len, wrapped)) < max(map(len, broken))):
                broken = wrapped
        self.depth = end_depth
        return lines + broken

    def _break(self, line: str, indent: str, start_depth: int,
               scan: _Scan) -> list[str]:
        """Breaks a code line into pieces that fit, where it can.

        Args:
            line:        The line.
            indent:      Its indentation.
            start_depth: The bracket depth at its start.
            scan:        What ``_scan`` found in it.

        Returns:
            The pieces, continuation lines indented CONTINUATION columns
            further; a piece that can't be broken is left too long.
        """
        cont = len(indent) + CONTINUATION
        need_depth = 1 if self.lang == "python" else 0
        breaks = [p for p, d in scan.breaks if d >= need_depth]
        fallbacks = [p for p, d in scan.fallbacks if d >= need_depth]
        out: list[str] = []
        s = 0  # where the current piece starts
        while True:
            lead = 0 if s == 0 else cont
            if lead + len(line[s:].rstrip()) <= self.width:
                out.append(" " * lead + line[s:].rstrip())
                return out
            fit = [p for p in breaks if p > s
                   and lead + len(line[s:p].rstrip()) <= self.width]
            if not fit:
                fit = [p for p in fallbacks if p > s
                       and lead + len(line[s:p].rstrip()) <= self.width]
            preferred = [p for p in fit if p in scan.statements]
            if preferred:
                p = preferred[-1]
            elif fit:
                p = fit[-1]
            else:
                split = self._split_string(line, s, lead, scan, need_depth)
                if split is not None:
                    # Split a long string literal and start over on the
                    # rest of the line.
                    self.depth = start_depth
                    head = line[:split] + _split_quote(scan, split)
                    rest = line[split:]
                    line = head + _split_resume(scan, split) + rest
                    scan = self._rescan(line, start_depth)
                    breaks = [b for b, d in scan.breaks if d >= need_depth]
                    fallbacks = [b for b, d in scan.fallbacks
                                 if d >= need_depth]
                    continue
                later = [b for b in breaks + fallbacks if b > s]
                if not later:
                    out.append(self._piece(line[s:].rstrip(), lead, indent))
                    return out
                p = min(later)
            out.append(self._piece(line[s:p].rstrip(), lead, indent))
            s = p
            while s < len(line) and line[s] == " ":
                s += 1

    def _piece(self, text: str, lead: int, indent: str) -> str:
        """Indents a continuation piece, less than usual if that makes it fit,
        but never less than the line it continues.

        Args:
            text:   The piece.
            lead:   The usual indentation.
            indent: The indentation of the line it continues.

        Returns:
            The indented piece.
        """
        if lead > len(indent) and lead + len(text) > self.width:
            lead = max(len(indent), self.width - len(text))
        return " " * lead + text

    def _rescan(self, line: str, start_depth: int) -> _Scan:
        """Scans a line again from the bracket depth at its start.

        Args:
            line:        The line.
            start_depth: The bracket depth at its start.

        Returns:
            What ``_scan`` finds in it.
        """
        self.depth = start_depth
        return self._scan(line)

    def _split_string(self, line: str, s: int, lead: int, scan: _Scan,
                      need_depth: int) -> int | None:
        """Finds where to split a string literal that runs past the width in
        the piece starting at ``s``.

        Args:
            line:       The line.
            s:          Where the piece starts.
            lead:       The piece's indentation.
            scan:       What ``_scan`` found in the line.
            need_depth: The bracket depth a break needs (1 in Python).

        Returns:
            The index to split at, after a space or a separator if there is
            one, never inside an escape or an f-string field; None if no
            literal there can be split.
        """
        limit = s + self.width - lead - 1  # room for the closing quote
        for start, end, prefix, quote in scan.strings:
            if start < s or start >= limit:
                continue
            # The literal, with what follows it up to the next break, must
            # overflow.
            nxt = min([p for p, _ in scan.breaks + scan.fallbacks if p > end]
                      + [len(line)])
            if lead + len(line[s:nxt].rstrip()) <= self.width:
                continue
            if (len(quote) == 3
                    or (self.lang == "python"
                        and self._depth_at(scan, start) < need_depth)):
                return None
            content = start + len(prefix) + len(quote)
            if limit - content < 8:
                return None  # too little would stay on this line
            # Prefer splitting after a space or a separator.
            k = limit
            for j in range(limit, content + 8, -1):
                if line[j - 1] in " |,;./" and line[j] != " ":
                    k = j
                    break
            k = min(k, end - len(quote) - 1)
            # Don't split an escape sequence or, in an f-string, a field.
            while k > content and (_in_escape(line, content, k)
                                   or ("f" in prefix.lower()
                                       and _in_field(line, content, k))):
                k -= 1
            if k - content < 8:
                return None
            return k
        return None

    def _depth_at(self, scan: _Scan, index: int) -> int:
        """Finds the bracket depth at a position, as the breaks record it.

        Args:
            scan:  What ``_scan`` found in the line.
            index: The position.

        Returns:
            The depth of the last break at or before it, 0 if there is none.
        """
        depth = 0
        for p, d in scan.breaks:
            if p > index:
                break
            depth = d
        return depth

    def _parenthesize(self, line: str, indent: str,
                      start_depth: int) -> list[str] | None:
        """Puts the value of a Python assignment or return in parentheses, so
        that it can break.

        Args:
            line:        The line.
            indent:      Its indentation.
            start_depth: The bracket depth at its start.

        Returns:
            The lines it becomes, or None if it is neither an assignment nor
            a return, or starts inside brackets.
        """
        if start_depth:
            return None
        body = line.strip()
        m = re.match(r"return (.+)$", body)
        if m:
            head, value = "return", m.group(1)
        else:
            eq = _top_level_assignment(body)
            if eq is None:
                return None
            head, value = body[:eq].rstrip() + " =", body[eq + 1 :].strip()
        inner_indent = " " * (len(indent) + CONTINUATION)
        inner = inner_indent + value
        self.depth = 1
        body = self._break(inner, inner_indent, 1, self._scan(inner))
        self.depth = 0
        return [f"{indent}{head} ("] + body + [f"{indent})"]

    def _scan(self, line: str) -> _Scan:
        """Finds the break points, string literals and any trailing comment
        of a code line, and updates the bracket depth.

        Args:
            line: The line.

        Returns:
            What it holds.
        """
        scan = _Scan()
        i, n = 0, len(line)
        py = self.lang == "python"
        while i < n:
            c = line[i]
            if py and c == "#":
                scan.comment = i
                break
            if not py and line.startswith("//", i):
                scan.comment = i
                break
            if not py and line.startswith("/*", i):
                end = line.find("*/", i + 2)
                if end < 0:
                    self.block = True
                    scan.comment = i
                    break
                if not line[end + 2 :].strip():
                    scan.comment = i
                    break
                i = end + 2
                continue
            m = _string_start(line, i, py)
            if m is not None:
                prefix, quote = m
                end = _string_end(line, i + len(prefix) + len(quote), quote)
                if end < 0:
                    if py and len(quote) == 3:
                        self.triple = quote
                    break
                scan.strings.append((i, end, prefix, quote))
                # Adjacent string literals may break between them.
                j = end
                while j < n and line[j] == " ":
                    j += 1
                if j > end and _string_start(line, j, py) is not None:
                    scan.breaks.append((j, self.depth))
                i = end
                continue
            if not py and c in "{}":
                # In C, braces mostly open and close blocks, which don't
                # change where a line may break.
                if c == "{" and line[i + 1 : i + 2] not in ("", "}", " "):
                    scan.breaks.append((i + 1, self.depth))
            elif c in _OPEN:
                self.depth += 1
                if (i + 1 < n and line[i + 1] not in _CLOSE
                        and line[i + 1] != " "):
                    scan.breaks.append((i + 1, self.depth))
            elif c in _CLOSE:
                if py and self.depth > 0 and i > 0 and line[i - 1] != " ":
                    # Python may break before a closing bracket, as in
                    # `def f(...\n) -> T:`.
                    scan.fallbacks.append((i, self.depth))
                self.depth = max(0, self.depth - 1)
                # The statement after `if (...)` and the like.
                if (not py and self.depth == 0 and line[i + 1 : i + 2] == " "
                        and line[i + 2 : i + 3] not in ("", "{", ";")):
                    scan.breaks.append((i + 2, 0))
                    scan.statements.append(i + 2)
            elif c == "," or (c == ";" and self.depth > 0):
                if i + 1 < n and line[i + 1] == " ":
                    scan.breaks.append((i + 2, self.depth))
            elif c == " ":
                word = _operator_at(line, i + 1, py)
                if word is not None:
                    scan.breaks.append((i + 1, self.depth))
                elif not py and line.startswith(" = ", i) and i + 3 < n:
                    scan.breaks.append((i + 3, self.depth))
                elif (not py and i > 0 and line[i - 1] != " " and i + 1 < n
                      and line[i + 1] != " "):
                    scan.fallbacks.append((i + 1, self.depth))
            elif (py and c == ":" and line[i + 1 : i + 2] == " "
                  and self.depth > 0):
                scan.fallbacks.append((i + 2, self.depth))
            elif (not py and (c == "." or line.startswith("->", i)) and i > 0
                  and (line[i - 1].isalnum() or line[i - 1] in "_)]")
                  and line[i + (2 if c == "-" else 1) : i
                           + (3 if c == "-" else 2)].isidentifier()):
                # A member access, as a last resort.
                scan.fallbacks.append((i, self.depth))
            i += 1
        return scan


def _rust_continues(line: str) -> bool:
    """Says whether a line of Rust ends inside a string literal, after a
    backslash that continues it on the next line.

    Args:
        line: The line.

    Returns:
        Whether it does.
    """
    if not line.endswith("\\"):
        return False
    i, n = 0, len(line)
    inside = False
    while i < n:
        c = line[i]
        if inside:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                inside = False
        elif line.startswith("//", i):
            return False
        elif c == "'" and i + 2 < n and line[i + 2] == "'":
            i += 3
            continue
        elif c == "'" and line[i + 1 : i + 2] == "\\":
            end = line.find("'", i + 2)
            i = end + 1 if end > 0 else n
            continue
        elif c == '"':
            inside = True
        i += 1
    return inside


def _string_start(line: str, i: int, py: bool) -> tuple[str, str] | None:
    """Finds the string literal starting at a position, if one does.

    Args:
        line: The line.
        i:    The position.
        py:   Whether the line is Python, whose literals may have
              prefixes and triple quotes.

    Returns:
        The literal's prefix and quote, or None if none starts there.
    """
    if py:
        m = re.match(r'([rRbBuUfF]{0,2})("""|\'\'\'|"|\')', line[i:])
        if m is None:
            return None
        if (m.group(1) and i > 0
                and (line[i - 1].isalnum() or line[i - 1] == "_")):
            return None  # the end of a name, not a prefix
        return m.group(1), m.group(2)
    if line[i] == '"':
        if i > 0 and line[i - 1] == "R":
            return None  # a C++ raw string; leave it whole
        return "", '"'
    if line[i] == "'":
        return "", "'"
    return None


def _string_end(line: str, i: int, quote: str) -> int:
    """Finds the end of a string literal.

    Args:
        line:  The line.
        i:     Where the literal's contents start.
        quote: Its quote.

    Returns:
        The index just past it, or -1 if it doesn't end on this line.
    """
    n = len(line)
    while i < n:
        if line[i] == "\\":
            i += 2
            continue
        if line.startswith(quote, i):
            return i + len(quote)
        i += 1
    return -1


def _operator_at(line: str, i: int, py: bool) -> str | None:
    """Finds the operator a line may break before, if one starts at a
    position.

    Args:
        line: The line.
        i:    The position.
        py:   Whether the line is Python, which breaks before keywords;
              C and C++ break before ``&&`` and ``||``.

    Returns:
        The operator, or None.
    """
    if py:
        for k in _PY_KEYWORDS:
            if line.startswith(k + " ", i):
                return k
        return None
    for op in _C_OPERATORS:
        if line.startswith(op + " ", i):
            return op
    return None


def _in_escape(line: str, start: int, k: int) -> bool:
    """Says whether splitting a string at ``k`` would cut an escape
    sequence.

    Args:
        line:  The line.
        start: Where the literal's contents start.
        k:     Where it would be split.

    Returns:
        Whether it would.
    """
    j = k - 1
    slashes = 0
    while j >= start and line[j] == "\\":
        slashes += 1
        j -= 1
    if slashes % 2:
        return True
    # \x.., \u...., and octal escapes: don't split their digits.
    m = re.search(r"\\(x[0-9a-fA-F]*|u[0-9a-fA-F]*|U[0-9a-fA-F]*|[0-7]{1,3})$",
                  line[start:k])
    return m is not None and line[k : k + 1].isalnum()


def _in_field(line: str, start: int, k: int) -> bool:
    """Says whether a position is inside a replacement field of an
    f-string.

    Args:
        line:  The line.
        start: Where the f-string's contents start.
        k:     The position.

    Returns:
        Whether it is.
    """
    depth = 0
    i = start
    while i < k:
        if line.startswith("{{", i) or line.startswith("}}", i):
            i += 2
            continue
        if line[i] == "{":
            depth += 1
        elif line[i] == "}":
            depth -= 1
        i += 1
    return depth > 0


def _split_quote(scan: _Scan, k: int) -> str:
    """Says what ends the first half of a string literal split at ``k``.

    Args:
        scan: What ``_scan`` found in the line.
        k:    Where the literal is split.

    Returns:
        The literal's quote.
    """
    for start, end, prefix, quote in scan.strings:
        if start < k < end:
            return quote
    raise AssertionError("no string to split")


def _split_resume(scan: _Scan, k: int) -> str:
    """Says what starts the second half of a string literal split at
    ``k``.

    Args:
        scan: What ``_scan`` found in the line.
        k:    Where the literal is split.

    Returns:
        A space, the literal's prefix and its quote.
    """
    for start, end, prefix, quote in scan.strings:
        if start < k < end:
            return " " + prefix + quote
    raise AssertionError("no string to split")


def _top_level_assignment(body: str) -> int | None:
    """Finds the ``=`` of a Python assignment statement.

    Args:
        body: The statement, without its indentation.

    Returns:
        The index of its ``=``, or None if it isn't an assignment.
    """
    depth = 0
    i = 0
    while i < len(body):
        m = _string_start(body, i, True)
        if m is not None:
            end = _string_end(body, i + len(m[0]) + len(m[1]), m[1])
            if end < 0:
                return None
            i = end
            continue
        c = body[i]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == "=" and depth == 0:
            before = body[i - 1] if i else ""
            after = body[i + 1] if i + 1 < len(body) else ""
            if before not in "=!<>:+-*/%&|^@" and after != "=":
                return i
        i += 1
    return None


def _fill(text: str, first: str, rest: str, width: int) -> list[str]:
    """Fills text into lines at spaces.

    Args:
        text:  The text.
        first: What the first line starts with.
        rest:  What the other lines start with.
        width: The widest a line may be.

    Returns:
        The lines; a word too long to fit gets a line of its own.
    """
    words = text.split()
    if not words:
        return [first.rstrip()]
    lines: list[str] = []
    cur = first + words[0]
    for word in words[1:]:
        if len(cur) + 1 + len(word) <= width:
            cur += " " + word
        else:
            lines.append(cur)
            cur = rest + word
    lines.append(cur)
    return lines


def _close(lines: list[str], closer: str, indent: str, width: int) -> list[str]:
    """Appends a closer to the last line, or puts it on a line of its own
    if it doesn't fit.

    Args:
        lines:  The lines, the last one updated in place.
        closer: The closer, such as ``*/``, with its leading space.
        indent: The indentation of a line of its own.
        width:  The widest a line may be.

    Returns:
        The lines.
    """
    if len(lines[-1]) + len(closer) <= width:
        lines[-1] += closer
    else:
        lines.append(indent + closer.strip())
    return lines


# The longest piece of a long Rust string literal that split_rust_strings
# leaves on one line.
_RUST_PIECE = 40


def split_rust_strings(text: str) -> str:
    """Splits long Rust string literals over lines, before rustfmt lays
    out the code, which it can't do around a literal too long to fit.

    A backslash at the end of a line inside a Rust string drops the newline
    and the next line's leading whitespace, so each literal keeps its value.
    Pieces end after a ``|`` or a space and never start with whitespace.

    Args:
        text: The Rust source.

    Returns:
        The source with its long literals split.
    """
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith("//"):
            out.append(line)
            continue
        out.append(_split_rust_line(line))
    return "\n".join(out)


def _split_rust_line(line: str) -> str:
    """Splits the long string literals in one line of Rust.

    Args:
        line: The line, not a comment.

    Returns:
        The line, with backslash-newlines inside each literal longer than
        _RUST_PIECE.
    """
    pieces = []
    i, n = 0, len(line)
    last = 0
    while i < n:
        c = line[i]
        if line.startswith("//", i):
            break
        if (c == "'" and i + 2 < n
                and (line[i + 2] == "'"
                     or (line[i + 1] == "\\" and "'" in line[i + 3 : i + 12]))):
            # A char literal, such as '"'.
            i = line.index("'", i + 2) + 1
            continue
        if (c == '"'
                and not (i > 0
                         and (line[i - 1].isalnum() or line[i - 1] in "#_"))):
            end = _string_end(line, i + 1, '"')
            if end < 0:
                break
            content = line[i + 1 : end - 1]
            if len(content) > _RUST_PIECE:
                pieces.append(line[last : i + 1])
                pieces.append(_rust_pieces(content))
                last = end - 1
            i = end
            continue
        i += 1
    pieces.append(line[last:])
    return "".join(pieces)


def _rust_pieces(content: str) -> str:
    """Breaks the contents of a long Rust string literal into pieces.

    Args:
        content: The literal's contents.

    Returns:
        ``content`` with backslash-newlines between pieces of at most
        about _RUST_PIECE characters.
    """
    parts = []
    start = 0
    while len(content) - start > _RUST_PIECE:
        k = -1
        for j in range(start + _RUST_PIECE, start + 8, -1):
            if (content[j - 1] in "| " and not content[j].isspace()
                    and not _in_escape(content, start, j)):
                k = j
                break
        if k < 0:
            break
        parts.append(content[start:k])
        start = k
    parts.append(content[start:])
    return "\\\n".join(parts)
