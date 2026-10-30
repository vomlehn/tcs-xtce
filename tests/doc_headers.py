"""Check that every non-trivial function has a documentation header.

A header gives, in order, a brief description of what the function does,
its parameters with what each means, and what it returns.  Every
parameter's description, continuation lines included, starts in the same
column, at least one space after the longest name.  A function that
returns nothing has no return section.  The header follows the
conventions of its language, as below: in Rust, RFC 1574 and the standard
library (a third-person summary; ``# Errors``, ``# Panics`` and
``# Safety`` sections where they apply); in C and C++, Doxygen; in
Python, PEP 257 and Google's style guide.

Rust (rustdoc)::

    /// Brief description.
    ///
    /// # Arguments
    ///
    /// * `raw`  - The raw field, continued
    ///            in this column.
    /// * `bits` - Its size in bits.
    ///
    /// # Returns
    ///
    /// What it returns.
    ///
    /// # Errors
    ///
    /// When and why it fails, for a function that returns a `Result`.

C and C++ (Doxygen)::

    /** Brief description.
     *
     * @param raw   The raw field, continued
     *              in this column.
     * @param bits  Its size in bits.
     * @return      What it returns. */

Python (Google style docstrings)::

    \"\"\"Brief description.

    Args:
        raw:  The raw field, continued
              in this column.
        bits: Its size in bits.

    Returns:
        What it returns.
    \"\"\"

A function is trivial, and needs no header, when its body is at most
TRIVIAL lines.  Tests (Rust ``#[test]`` functions, Python ``test*``
methods and unittest's set-up and tear-down methods) need none; their
names say what they test.  Nor do methods that implement a Rust trait,
C++ overrides, or Python special methods other than ``__init__``: their
documentation is the trait's, base class's or language's.  A Python
``__init__``'s parameters may be in its class's docstring instead.  A
Rust function returning ``Result<()>`` (or ``fmt::Result``, or a
``Result`` alias such as ``io::Result<()>``) needs ``# Errors`` but, as
in the standard library, no ``# Returns``.  A C or C++ function defined
in a source file may be documented where a header declares it instead.

Checks every Rust, C, C++ and Python file in git (Python scripts without
a suffix too, by their ``#!`` line), except under ``vendor/`` and in fuzz
corpora, and the code the generators write, when it exists.

usage: python3 -I tests/doc_headers.py [FILE...]
                                          (from the top of the repository)
Prints each function whose header is missing or wrong and exits with
status 1 if there are any.
"""

import ast
import glob
import re
import subprocess
import sys
from pathlib import Path

TRIVIAL = 3

# Generated code, which git doesn't keep; see .gitignore.
GENERATED = ["rust/src/generated.rs", "cpp/include/xtce/generated.hpp",
             "cpp/src/generated.cpp", "python/tcs_xtce/generated.py",
             "rust/tests/mission/demo*.rs", "cpp/tests/mission/demo*.h",
             "python/tests/mission/demo*.py", "examples/*/rust/src/*.rs",
             "examples/*/c/*.h", "examples/*/cxx/*.h", "examples/*/python/*.py"]
SKIP = ("vendor/", "target/")


class Problems:
    """Collects what is wrong with the headers of one file.

    Args:
        path: The file being checked.
    """

    def __init__(self, path: Path):
        self.path = path
        self.found: list[str] = []

    def add(self, line: int, name: str, what: str) -> None:
        """Records one problem.

        Args:
            line: The line the function starts on.
            name: The function's name.
            what: What is wrong with its header.
        """
        self.found.append(f"{self.path}:{line}: {name}: {what}")


def check_list(probs: Problems, line: int, name: str,
               entries: list[tuple[str, int, list[int]]],
               params: list[str]) -> None:
    """Checks a header's parameter list against the parameters.

    Args:
        probs:   Where to record problems.
        line:    The line the function starts on.
        name:    The function's name.
        entries: Each documented parameter: its name, the column its
                 description starts in, and the columns its continuation
                 lines start in.
        params:  The function's parameters, in order.
    """
    names = [e[0] for e in entries]
    if names != params:
        probs.add(line, name, f"documents parameters {names}, not {params}")
        return
    cols = {e[1] for e in entries}
    for e in entries:
        cols.update(e[2])
    if len(cols) > 1:
        probs.add(line, name,
                  "parameter descriptions don't all start in one column")


# ---------------------------------------------------------------- Rust ---

def strip_rust(text: str) -> str:
    """Blanks Rust comments, strings and character literals.

    Keeps every newline and the length of each line, so that positions in
    the result are positions in the text.

    Args:
        text: Rust source.

    Returns:
        The source with comments and literals replaced by spaces.
    """
    out = []
    i, n = 0, len(text)

    def blank(s: str) -> str:
        return re.sub(r"[^\n]", " ", s)

    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(blank(text[i:j]))
            i = j
        elif text.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            out.append(blank(text[i:j]))
            i = j
        elif ((m := re.compile(r'b?r(#*)"').match(text, i))
              and (i == 0
                   or not (text[i - 1].isalnum() or text[i - 1] == "_"))):
            end = '"' + m.group(1)
            j = text.find(end, m.end())
            j = n if j < 0 else j + len(end)
            out.append(blank(text[i:j]))
            i = j
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(blank(text[i:j + 1]))
            i = j + 1
        elif c == "'":
            m = re.compile(r"'(\\u\{[0-9a-fA-F]+\}|\\x..|\\.|"
                           r"[^\\'\n])'").match(text, i)
            if m:
                out.append(blank(m.group(0)))
                i = m.end()
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def match_close(code: str, i: int) -> int:
    """Finds the bracket that closes the one at a position.

    Args:
        code: Source with comments and literals blanked.
        i:    The position of an opening bracket.

    Returns:
        The position of the matching closing bracket, or the length of
        the source if there is none.
    """
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    stack = []
    j = i
    while j < len(code):
        c = code[j]
        if c in "([{":
            stack.append(pairs[c])
        elif c in ")]}":
            if stack and stack[-1] == c:
                stack.pop()
            if not stack:
                return j
        j += 1
    return len(code)


def split_top(s: str) -> list[str]:
    """Splits a parameter list at the commas outside any brackets.

    Args:
        s: The text between a function's parentheses.

    Returns:
        Each parameter's text, stripped, without empty ones.
    """
    parts, depth, cur = [], 0, []
    for c in s:
        if c in "([{<":
            depth += 1
        elif c in ")]}>":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def rust_params(s: str) -> list[str]:
    """Names a Rust function's parameters, without its receiver.

    Args:
        s: The text between the function's parentheses.

    Returns:
        The parameters' names in order; a pattern that isn't a plain
        name is given as written.
    """
    names = []
    s = s.replace("->", "  ")
    for p in split_top(s):
        p = re.sub(r"^#\[[^\]]*\]\s*", "", p)
        pat = p.split(":", 1)[0].strip() if ":" in p else p
        pat = re.sub(r"^(ref\s+)?(mut\s+)?", "", pat)
        if re.fullmatch(r"&?\s*('\w+\s+)?(mut\s+)?self", pat):
            continue
        names.append(pat)
    return names


FN_RE = re.compile(r"\bfn\s+(\w+)")


def rust_doc(lines: list[str], start: int) -> tuple[list[str], list[str]]:
    """Gathers the doc comment and attributes above a Rust item.

    Args:
        lines: The file's lines.
        start: The index of the item's first line.

    Returns:
        The doc comment's lines, with ``///`` removed, and the
        attributes' lines.
    """
    doc, attrs = [], []
    j = start - 1
    in_attr = False
    while j >= 0:
        s = lines[j].strip()
        if s.startswith("///"):
            doc.insert(0, s[3:])
        elif s.startswith("#[") or s.startswith("#!["):
            attrs.insert(0, s)
        elif (s.endswith("]") and not in_attr and j > 0
              and ("#[" in "".join(lines[max(0, j - 8):j]))):
            # The end of an attribute spread over several lines.
            attrs.insert(0, s)
            in_attr = True
        elif in_attr:
            attrs.insert(0, s)
            if s.startswith("#["):
                in_attr = False
        elif s.startswith("//") and not s.startswith("///"):
            pass
        else:
            break
        j -= 1
    return doc, attrs


def rust_sections(doc: list[str]) -> dict[str, list[str]]:
    """Splits a rustdoc comment into its sections.

    Args:
        doc: The comment's lines, without ``///``.

    Returns:
        Each section's lines by heading; the summary is under ``""``.
    """
    secs: dict[str, list[str]] = {"": []}
    cur = ""
    for d in doc:
        m = re.match(r"\s*#\s+(\w+)", d)
        if m:
            cur = m.group(1)
            secs[cur] = []
        else:
            secs[cur].append(d)
    return secs


def check_rust(probs: Problems, text: str) -> None:
    """Checks the headers of the functions in one Rust file.

    Args:
        probs: Where to record problems.
        text:  The file's contents.
    """
    code = strip_rust(text)
    lines = text.split("\n")
    line_starts = [0]
    for ln in lines:
        line_starts.append(line_starts[-1] + len(ln) + 1)

    def line_of(pos: int) -> int:
        """Finds the line a position is on.

        Args:
            pos: A position in the file.

        Returns:
            The index of the line holding it.
        """
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    # Spans of `impl Trait for Type` blocks, whose methods are the trait's.
    trait_spans = []
    for m in re.finditer(r"\bimpl\b", code):
        j = code.find("{", m.end())
        semi = code.find(";", m.end())
        if j < 0 or (0 <= semi < j):
            continue
        head = code[m.end():j]
        if (re.search(r"\bfor\b", re.sub(r"<[^<>]*>", "", head))
                or re.search(r"\bfor\b", head)):
            trait_spans.append((j, match_close(code, j)))

    # Spans of #[cfg(test)] modules.
    test_spans = []
    for m in re.finditer(r"#\[cfg\(test\)\]\s*(pub\s+)?mod\s+\w+\s*\{", code):
        j = m.end() - 1
        test_spans.append((j, match_close(code, j)))

    for m in FN_RE.finditer(code):
        name = m.group(1)
        pos = m.start()
        if any(a < pos < b for a, b in trait_spans):
            continue
        # Skip `fn` in types such as `fn(u8) -> u8` and in macros.
        # Skip the generic parameters, whose bounds may hold parentheses,
        # as in `fn with<F: FnOnce(u8)>(f: F)`.
        after = m.end()
        while after < len(code) and code[after].isspace():
            after += 1
        if code.startswith("<", after):
            depth, j = 0, after
            while j < len(code):
                if code[j] == "<":
                    depth += 1
                elif code[j] == ">" and code[j - 1] != "-":
                    depth -= 1
                    if not depth:
                        break
                j += 1
            after = j + 1
        open_p = code.find("(", after)
        if open_p < 0:
            continue
        between = code[after:open_p]
        if between.strip():
            continue
        close_p = match_close(code, open_p)
        rest = code[close_p + 1:]
        brace = rest.find("{")
        semi = rest.find(";")
        has_body = brace >= 0 and (semi < 0 or brace < semi)
        sig_tail = rest[:brace if has_body else semi]
        ret = ""
        rm = re.search(r"->\s*(.*?)(\bwhere\b|$)", sig_tail, re.S)
        if rm:
            ret = rm.group(1).strip()
        returns = ret not in ("", "()", "!")
        # A `Result<()>` gives nothing back but success, which its
        # `# Errors` section implies; std leaves out `# Returns` for it.
        unit_ok = (re.match(r"([\w:]*::)?\w*Result\s*<\s*\(\s*\)", ret)
                   or re.fullmatch(r"(std::)?fmt::Result", ret))
        line = line_of(pos)
        # The item starts at its line's first non-blank character.
        doc, attrs = rust_doc(lines, line)
        if any(a.startswith("#[test") or "tokio::test" in a for a in attrs):
            continue
        if any(a < pos < b for a, b in test_spans) and name.startswith("test"):
            continue
        if has_body:
            ob = close_p + 1 + brace
            cb = match_close(code, ob)
            body = [b for b in code[ob + 1:cb].split("\n") if b.strip()]
            if len(body) <= TRIVIAL:
                continue
        params = rust_params(code[open_p + 1:close_p])
        lineno = line + 1
        if not doc:
            probs.add(lineno, name, "no header")
            continue
        secs = rust_sections(doc)
        if not any(s.strip() for s in secs[""]):
            probs.add(lineno, name, "no description")
        if params:
            if "Arguments" not in secs:
                probs.add(lineno, name, "no # Arguments section")
            else:
                entries = []
                for d in secs["Arguments"]:
                    em = re.match(r"(\s*\*\s+`([^`]+)`\s+-\s+)\S", d)
                    if em:
                        entries.append((em.group(2), len(em.group(1)), []))
                    elif d.strip() and entries:
                        ind = len(d) - len(d.lstrip())
                        entries[-1][2].append(ind)
                    elif d.strip():
                        probs.add(lineno, name,
                                  f"malformed # Arguments line: {d.strip()}")
                check_list(probs, lineno, name, entries, params)
        elif "Arguments" in secs:
            probs.add(lineno, name, "# Arguments but no parameters")
        if returns and not unit_ok and "Returns" not in secs:
            probs.add(lineno, name, "no # Returns section")
        if not returns and "Returns" in secs:
            probs.add(lineno, name, "# Returns but returns nothing")
        if re.match(r"([\w:]*::)?\w*Result\b", ret) and "Errors" not in secs:
            probs.add(lineno, name,
                      "returns a Result but has no # Errors section")
        if (re.search(r"\bunsafe\s+(extern\s+\S+\s+)?fn\s*$",
                      code[max(0, pos - 40):pos + 2]) and "Safety" not in secs):
            probs.add(lineno, name, "unsafe but no # Safety section")


# --------------------------------------------------------- C and C++ ---

C_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "do", "else",
              "case", "defined", "catch", "alignof", "decltype",
              "static_assert", "noexcept", "typeof", "__attribute__"}


def strip_c(text: str) -> str:
    """Blanks C and C++ comments, strings, characters and preprocessor
    lines.

    Args:
        text: C or C++ source.

    Returns:
        The source with those replaced by spaces, newlines kept.
    """
    out = []
    i, n = 0, len(text)

    def blank(s: str) -> str:
        return re.sub(r"[^\n]", " ", s)

    at_line_start = True
    while i < n:
        c = text[i]
        if at_line_start and c == "#":
            j = i
            while True:
                k = text.find("\n", j)
                k = n if k < 0 else k
                if k > 0 and text[k - 1] == "\\" and k < n:
                    j = k + 1
                    continue
                break
            out.append(blank(text[i:k]))
            i = k
            continue
        if c == "\n":
            at_line_start = True
        elif not c.isspace():
            at_line_start = False
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(blank(text[i:j]))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(blank(text[i:j]))
            i = j
        elif (m := re.compile(r'R"([^(]*)\(').match(text, i)):
            end = ")" + m.group(1) + '"'
            j = text.find(end, m.end())
            j = n if j < 0 else j + len(end)
            out.append(blank(text[i:j]))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(blank(text[i:j + 1]))
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def c_param_name(p: str) -> str:
    """Names one C or C++ parameter.

    Args:
        p: The parameter's declaration.

    Returns:
        Its name, ``"..."`` for a variadic list, or ``""`` if it has
        none (as in ``void`` or an unnamed parameter).
    """
    p = p.split("=", 1)[0].strip()
    if p == "...":
        return "..."
    if p == "void":
        return ""
    fm = re.search(r"\(\s*[*&]+\s*(\w+)\s*\)\s*\(", p)
    if fm:
        return fm.group(1)
    p = re.sub(r"(\s*\[[^\]]*\])+\s*$", "", p).strip()
    m = re.search(r"(\w+)\s*$", p)
    if not m:
        return ""
    words = re.findall(r"[\w:]+", p)
    if len(words) < 2 and not re.search(r"[*&]", p):
        return ""
    return m.group(1)


C_TYPES = {"void", "char", "short", "int", "long", "float", "double", "bool",
           "signed", "unsigned", "auto"}


def is_expression(p: str) -> bool:
    """Says whether a parenthesized item is a value rather than a type.

    Used to tell a variable's initializer, as in ``T x(y);``, from an
    unnamed parameter, as in ``T f(Y);``.

    Args:
        p: The item, with comments and literals blanked.

    Returns:
        True for a number, a lower-case name that isn't a built-in type
        or a ``_t`` type, or anything with an operator or call in it.
    """
    p = p.strip()
    if not p:
        return True
    if re.search(r"[-+/%^|!?(]|\.(?=\w)", p) or re.match(r"\d", p):
        return True
    m = re.fullmatch(r"(?:\w+::)*(\w+)", p)
    if not m:
        return False
    last = m.group(1)
    return not (last in C_TYPES or last.endswith("_t") or last[0].isupper())


def c_doc(lines: list[str], line: int) -> list[str] | None:
    """Finds the Doxygen comment that ends just above a line.

    Args:
        lines: The file's lines.
        line:  The index of the function's first line.

    Returns:
        The comment's lines with the comment markers removed, or None if
        there is no ``/** ... */`` comment directly above.
    """
    j = line - 1
    while j >= 0 and (lines[j].strip().startswith("template")
                      or lines[j].strip().startswith("[[")):
        j -= 1
    if j < 0 or not lines[j].rstrip().endswith("*/"):
        return None
    end = j
    while j >= 0 and "/*" not in lines[j]:
        j -= 1
    if j < 0 or "/**" not in lines[j]:
        return None
    body = []
    for k in range(j, end + 1):
        s = lines[k]
        if k == j:
            s = s[s.index("/**") + 3:]
        else:
            s = re.sub(r"^\s*\*(?!/)", "", s, count=1)
        if k == end:
            s = s[:s.rindex("*/")]
        body.append(s)
    return body


def check_c_doc(probs: Problems, lineno: int, name: str, doc: list[str],
                params: list[str], returns: bool) -> None:
    """Checks one C or C++ function's Doxygen header.

    Args:
        probs:   Where to record problems.
        lineno:  The line the function starts on.
        name:    The function's name.
        doc:     The header's lines without comment markers.
        params:  The function's parameter names, in order.
        returns: Whether the function returns a value.
    """
    first = next((i for i, d in enumerate(doc) if re.match(r"\s*@", d)),
                 len(doc))
    if not any(d.strip() for d in doc[:first]):
        probs.add(lineno, name, "no description")
    entries: list[tuple[str, int, list[int]]] = []
    ret_col = None
    has_ret = False
    cur = None
    for d in doc[first:]:
        pm = re.match(r"(\s*@param(?:\[[a-z,]+\])?\s+(\S+)\s+)\S", d)
        rm = re.match(r"(\s*@returns?\s+)\S", d)
        if pm:
            entries.append((pm.group(2), len(pm.group(1)), []))
            cur = "param"
        elif rm:
            has_ret = True
            ret_col = len(rm.group(1))
            cur = "return"
        elif re.match(r"\s*@", d):
            cur = None
        elif d.strip() and cur == "param":
            entries[-1][2].append(len(d) - len(d.lstrip()))
    check_list(probs, lineno, name, entries, params)
    if entries and ret_col is not None and ret_col != entries[0][1]:
        probs.add(lineno, name,
                  "@return's description isn't in the parameters' column")
    if returns and not has_ret:
        probs.add(lineno, name, "no @return")
    if not returns and has_ret:
        probs.add(lineno, name, "@return but returns nothing")


def c_function(head: str) -> tuple[str, str, str, str] | None:
    """Reads a C or C++ declaration that may be a function's.

    Args:
        head: The declaration up to its body's brace or its semicolon,
              with comments and literals blanked.

    Returns:
        The function's name, what precedes the name (its return type and
        specifiers), the text between its parentheses, and what follows
        them; or None if the declaration isn't a function's.
    """
    h = re.sub(r"__attribute__\s*\(\(.*?\)\)", lambda m: " " * len(m.group(0)),
               head, flags=re.S)
    h = re.sub(r"^\s*(public|private|protected)\s*:", "", h)
    depth = 0
    for i, c in enumerate(h):
        if c == "<":
            depth += 1
        elif c == ">" and depth:
            depth -= 1
        elif c == "(" and depth == 0:
            break
        elif c in "=;" and depth == 0:
            return None
    else:
        return None
    m = re.search(r"(operator\s*(?:\(\)|[^\s(]+)|~?[A-Za-z_]\w*"
                  r"(?:\s*::\s*~?[A-Za-z_]\w*)*)\s*$", h[:i])
    if not m:
        return None
    name = re.sub(r"\s+", "", m.group(1))
    if name.split("::")[-1] in C_KEYWORDS:
        return None
    close = match_close(h, i)
    if close >= len(h):
        return None
    pre = " ".join(h[:m.start()].split())
    post = h[close + 1:]
    if (not re.fullmatch(r"\s*((const|noexcept(\s*\([^)]*\))?|override|"
                         r"final|volatile|&&|&|throw\s*\(\s*\)|"
                         r"->\s*[\w:<>*&, ]+|=\s*(default|delete|0))"
                         r"\s*)*(:.*)?", post, re.S)):
        return None
    return name, pre, h[i + 1:close], post


def check_c(probs: Problems, text: str, header: bool,
            documented: set[str]) -> None:
    """Checks the headers of the functions in one C or C++ file.

    A function is documented where it is defined or, if it has one, at
    its declaration in the same file.  A function or member function
    defined in a source file may instead be documented where a header
    declares it; headers are checked first.

    Args:
        probs:      Where to record problems.
        text:       The file's contents.
        header:     Whether the file is a header (``.h`` or ``.hpp``).
        documented: The functions documented in the headers checked so
                    far, which a source file needn't document again;
                    a header adds the ones it documents.
    """
    code = strip_c(text)
    lines = text.split("\n")
    line_starts = [0]
    for ln in lines:
        line_starts.append(line_starts[-1] + len(ln) + 1)

    def line_of(pos: int) -> int:
        """Finds the line a position is on.

        Args:
            pos: A position in the file.

        Returns:
            The index of the line holding it.
        """
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    found: dict[str, list] = {}

    def declare(head: str, at: int, body: int | None, cls: str) -> None:
        """Records a declaration if it is a function's.

        Args:
            head: The declaration up to its body's brace or semicolon.
            at:   Where the declaration starts in the file.
            body: How many non-blank lines its body has, or None for a
                  declaration without a body.
            cls:  The name of the class it is in, or ``""``.
        """
        f = c_function(head)
        if f is None:
            return
        name, pre, params_s, post = f
        base = name.split("::")[-1]
        if body is None and re.search(r"=\s*(default|delete)\s*$", post):
            return  # Nothing to document: the compiler writes it.
        spec = re.sub(r"\b(static|inline|extern|virtual|explicit|"
                      r"constexpr|friend|typename|class|struct)\b|"
                      r"\[\[[^\]]*\]\]", " ", pre)
        spec = re.sub(r"\btemplate\s*<.*>", " ", spec).strip()
        # A constructor is named after its class: the class it is in, or
        # the one its qualified name gives.  A name like that at file scope
        # with no return type is a macro.
        parts = name.split("::")
        ctor = (base.startswith("~")
                or (not spec
                    and ((cls and base == cls)
                         or (len(parts) > 1 and parts[-2] == base))))
        if not spec and not ctor:
            return  # A macro, at file scope.
        if "->" in post:
            returns = not re.search(r"->\s*void\b", post)
        else:
            returns = not ctor and spec not in ("void", "const void")
        plist = split_top(params_s)
        params = [x for x in (c_param_name(x) for x in plist) if x]
        if (body is None and plist and not params
                and any(is_expression(x) for x in plist)):
            return  # A variable with an initializer, as in `T x(y);`.
        lead = re.match(r"\s*((public|private|protected)\s*:)?\s*", head).end()
        first = line_of(at + lead)
        key = f"{cls}::{name}" if cls else name
        found.setdefault(key, []).append(
            dict(line=first, params=params, returns=returns, body=body,
                 override=bool(re.search(r"\b(override|final)\b", post)),
                 doc=c_doc(lines, first)))

    def scan(i: int, end: int, cls: str) -> None:
        """Finds the declarations in a scope, entering nested scopes.

        Args:
            i:   Where the scope's contents start.
            end: Where they end.
            cls: The name of the class the scope is, or ``""``.
        """
        start = i
        while i < end:
            c = code[i]
            if c == ";":
                declare(code[start:i], start, None, cls)
                i += 1
                start = i
            elif c == "{":
                head = code[start:i]
                close = match_close(code, i)
                hs = " ".join(head.split())
                if (re.match(r"(.*\s)?(namespace(\s+[\w:]+)?|extern)$", hs)
                        or hs.startswith("extern ")):
                    scan(i + 1, close, "")
                elif (m := re.match(r"(template\s*<.*>\s*)?(class|struct|"
                                    r"union)\s+(\w+)?[^()]*$", hs)):
                    scan(i + 1, close, m.group(3) or "")
                elif "(" in head and "=" not in head.split("(")[0]:
                    body = len([b for b in code[i + 1:close].split("\n")
                                if b.strip()])
                    declare(head, start, body, cls)
                i = close + 1
                start = i
            elif c == "}":
                i += 1
                start = i
            else:
                i += 1

    scan(0, len(code), "")
    for key, recs in found.items():
        decls = [r for r in recs if r["body"] is None]
        defs = [r for r in recs if r["body"] is not None]
        if not header and "::" in key and not decls:
            continue  # A member, documented where its class declares it.

        def partner(r: dict) -> dict | None:
            """Finds the declaration that goes with a definition.

            Args:
                r: The definition.

            Returns:
                The declaration with as many parameters, or None if
                there is none.
            """
            return next((d for d in decls
                         if len(d["params"]) == len(r["params"])), None)

        paired = [partner(r) for r in defs]
        todo = defs + [d for d in decls if not any(d is x for x in paired)]
        for r in todo:
            if r["override"]:
                continue
            mate = partner(r) if r["body"] is not None else None
            doc = r["doc"] or (mate["doc"] if mate else None)
            if (doc is None
                    and (key in documented
                         or (r["body"] is not None and r["body"] <= TRIVIAL))):
                continue
            lineno = r["line"] + 1
            if doc is None:
                probs.add(lineno, key, "no /** header")
                continue
            if header:
                documented.add(key)
            check_c_doc(probs, lineno, key, doc, r["params"], r["returns"])


# -------------------------------------------------------------- Python ---

def py_returns(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Says what a Python function gives back.

    Args:
        fn: The function.

    Returns:
        ``"Yields"`` for a generator, ``"Returns"`` for a function that
        returns a value, and ``""`` for one that returns nothing.
    """
    if fn.returns is not None:
        r = ast.unparse(fn.returns)
        if r in ("None", "NoReturn", "typing.NoReturn", "Never"):
            return ""
        if re.match(r"(typing\.)?(Iterator|Generator|Iterable)\b", r):
            gen = any(isinstance(n, (ast.Yield, ast.YieldFrom))
                      for n in own_nodes(fn))
            return "Yields" if gen else "Returns"
        return "Returns"
    gen = False
    val = False
    for n in own_nodes(fn):
        if isinstance(n, (ast.Yield, ast.YieldFrom)):
            gen = True
        elif (isinstance(n, ast.Return) and n.value is not None
              and not (isinstance(n.value, ast.Constant)
                       and n.value.value is None)):
            val = True
    if gen:
        return "Yields"
    return "Returns" if val else ""


def own_nodes(fn: ast.AST):
    """Walks a function's body without entering nested functions.

    Args:
        fn: The function.

    Yields:
        Each node of the body, outside any function or class defined
        within it.
    """
    todo = list(ast.iter_child_nodes(fn))
    while todo:
        n = todo.pop()
        yield n
        if (not isinstance(n,
                           (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                            ast.ClassDef))):
            todo.extend(ast.iter_child_nodes(n))


def py_sections(doc: str) -> dict[str, list[str]]:
    """Splits a Google style docstring into its sections.

    Args:
        doc: The docstring, as written (not cleaned).

    Returns:
        Each section's lines by heading (``Args``, ``Returns`` and so
        on), the summary under ``""``.
    """
    secs: dict[str, list[str]] = {"": []}
    cur = ""
    lines = doc.split("\n")
    head_ind = None
    for ln in lines:
        m = re.match(r"(\s*)(Args|Arguments|Parameters|Returns|Yields|"
                     r"Raises|Attributes|Examples?|Notes?|Note|"
                     r"Keyword Args|Other Parameters):\s*$", ln)
        if m:
            cur = m.group(2)
            if cur in ("Arguments", "Parameters"):
                cur = "Args"
            head_ind = len(m.group(1))
            secs[cur] = []
        elif (cur and ln.strip() and head_ind is not None
              and len(ln) - len(ln.lstrip()) <= head_ind):
            cur = ""
            secs[""].append(ln)
        else:
            secs[cur].append(ln)
    return secs


def py_args(sec: list[str]) -> list[tuple[str, int, list[int]]]:
    """Reads an Args section.

    Args:
        sec: The section's lines.

    Returns:
        Each documented parameter's name, the column its description
        starts in, and the columns its continuation lines start in.
    """
    lines = [s for s in sec if s.strip()]
    if not lines:
        return []
    ind = min(len(s) - len(s.lstrip()) for s in lines)
    entries: list[tuple[str, int, list[int]]] = []
    for s in lines:
        here = len(s) - len(s.lstrip())
        m = re.match(r"(\s*(\*{0,2}\w+)(?:\s*\([^)]*\))?:\s+)\S", s)
        if here == ind and m:
            entries.append((m.group(2), len(m.group(1)), []))
        elif here == ind and re.match(r"\s*\*{0,2}\w+(\s*\([^)]*\))?:\s*$", s):
            entries.append((s.strip().split(":")[0].split("(")[0].strip(), -1,
                            []))
        elif entries:
            entries[-1][2].append(here)
    return entries


def check_python(probs: Problems, text: str, in_tests: bool) -> None:
    """Checks the headers of the functions in one Python file.

    Args:
        probs:    Where to record problems.
        text:     The file's contents.
        in_tests: Whether the file holds tests.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        probs.add(e.lineno or 0, "<file>", f"doesn't parse: {e.msg}")
        return
    parents: dict[ast.AST, ast.AST] = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[c] = p
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = fn.name
        parent = parents.get(fn)
        method = isinstance(parent, ast.ClassDef)
        if (name.startswith("test")
                or name in ("setUp", "tearDown", "setUpClass", "tearDownClass",
                            "setUpModule", "tearDownModule")):
            continue
        if name.startswith("__") and name.endswith("__") and name != "__init__":
            continue
        decos = [ast.unparse(d) for d in fn.decorator_list]
        if any(d.endswith(("override", "overload")) for d in decos):
            continue
        body = fn.body
        doc = ast.get_docstring(fn, clean=False)
        stmts = body[1:] if doc is not None else body
        if stmts:
            span = stmts[-1].end_lineno - stmts[0].lineno + 1
        else:
            span = 0
        if span <= TRIVIAL:
            continue
        a = fn.args
        params = [x.arg for x in a.posonlyargs + a.args]
        if (method and params and params[0] in ("self", "cls")
                and not any(d == "staticmethod" for d in decos)):
            params = params[1:]
        elif method and params and not any(d == "staticmethod" for d in decos):
            params = params[1:]
        if a.vararg:
            params.append("*" + a.vararg.arg)
        params += [x.arg for x in a.kwonlyargs]
        if a.kwarg:
            params.append("**" + a.kwarg.arg)
        gives = py_returns(fn)
        if name == "__init__":
            gives = ""
            if doc is None and isinstance(parent, ast.ClassDef):
                cdoc = ast.get_docstring(parent, clean=False)
                if cdoc is not None and "Args:" in cdoc:
                    doc = cdoc
                elif not params:
                    continue
        lineno = fn.lineno
        if doc is None:
            probs.add(lineno, name, "no docstring")
            continue
        secs = py_sections(doc)
        if not any(s.strip() for s in secs[""]) and name != "__init__":
            probs.add(lineno, name, "no description")
        # Keep only the documented parameter names that Google style
        # writes without stars too.
        if params:
            if "Args" not in secs:
                probs.add(lineno, name, "no Args section")
            else:
                entries = py_args(secs["Args"])
                if any(e[1] < 0 for e in entries):
                    probs.add(lineno, name,
                              "a parameter has no description on its line")
                    entries = [e for e in entries if e[1] >= 0]
                strip = [e[0] for e in entries]
                want = params
                if ([s.lstrip("*") for s in strip] == [p.lstrip("*")
                                                       for p in params]):
                    want = strip
                check_list(probs, lineno, name, entries, want)
        elif "Args" in secs:
            probs.add(lineno, name, "Args but no parameters")
        if (gives and gives not in secs
                and not (gives == "Yields" and "Returns" in secs)):
            probs.add(lineno, name, f"no {gives} section")
        if not gives and ("Returns" in secs or "Yields" in secs):
            probs.add(lineno, name, "Returns but returns nothing")


# ---------------------------------------------------------------- main ---

def files(args: list[str]) -> list[Path]:
    """Lists the files to check.

    Args:
        args: The files named on the command line, if any.

    Returns:
        Those files, or else every Rust, C, C++ and Python file in git
        and the generated code that exists, sorted.
    """
    if args:
        return [Path(a) for a in args]
    tracked = subprocess.run(["git", "ls-files"], capture_output=True,
                             text=True, check=True).stdout.split()
    out = {Path(t) for t in tracked}
    for pattern in GENERATED:
        out.update(Path(p) for p in glob.glob(pattern))
    keep = {".rs", ".c", ".h", ".cpp", ".hpp", ".py"}
    return sorted(p for p in out
                  if (p.suffix in keep or (not p.suffix and python_script(p)))
                  and p.is_file() and not str(p).startswith(SKIP)
                  and "/corpus/" not in str(p))


def python_script(p: Path) -> bool:
    """Says whether a file is a Python script, by its ``#!`` line.

    Args:
        p: The file.

    Returns:
        True if its first line runs Python.
    """
    try:
        with p.open("rb") as f:
            first = f.readline(200)
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first


def main() -> int:
    """Checks the files and reports what is wrong.

    Returns:
        The exit status: 0 if every header is right, else 1.
    """
    bad = 0
    documented: set[str] = set()
    # Headers first, so that a source file needn't repeat their headers.
    order = sorted(files(sys.argv[1:]),
                   key=lambda p: (p.suffix not in (".h", ".hpp"), str(p)))
    for p in order:
        text = p.read_text(encoding="utf-8", errors="replace")
        probs = Problems(p)
        if p.suffix == ".rs":
            check_rust(probs, text)
        elif p.suffix == ".py" or not p.suffix:
            in_tests = "test" in str(p)
            check_python(probs, text, in_tests)
        else:
            check_c(probs, text, p.suffix in (".h", ".hpp"), documented)
        for f in probs.found:
            print(f, file=sys.stderr)
        bad += len(probs.found)
    if bad:
        print(f"doc_headers: {bad} functions without proper headers",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
