"""Documentation headers for the functions the emitters write.

Every generated function gets a header in its language's convention:
rustdoc for Rust, Doxygen for C++ and Google style docstrings for Python.
A header is built from a summary, the parameters with what each means,
what the function returns and how it fails.  Descriptions are filled to
``WIDTH`` columns, and each list's descriptions start in one column.

The phrases that say what a reader or writer of a schema type accepts
and how it fails are built here too, so that the three languages say the
same thing.
"""

from __future__ import annotations

import re
from typing import Callable, Optional, Sequence

from .binding import MANY, Binding, ChoiceType, ElemField, Record
from .model import UNBOUNDED, ComplexRef
from .wrap import WIDTH

# (name, description) pairs: parameters, or exceptions raised.
Entries = Sequence[tuple[str, str]]


def fill(text: str, width: int) -> list[str]:
    """Fills text into lines at word boundaries.

    Args:
        text:  The text; runs of whitespace become single spaces.
        width: The widest a line may be.  A word longer than this gets a
               line of its own.

    Returns:
        The lines, at least one.
    """
    words = text.split()
    if not words:
        return [""]
    lines = [words[0]]
    for word in words[1:]:
        if len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def _hang(head: str, text: str, width: int) -> list[str]:
    """Fills text after a label, continuing in the column it starts in.

    Args:
        head:  The label, padded to the column the text starts in.
        text:  The text that follows the label.
        width: The widest a line may be, label included.

    Returns:
        The lines: the label and the text's first line, then the rest
        indented to the label's width.
    """
    body = fill(text, max(width - len(head), 20))
    return [head + body[0]] + [" " * len(head) + b for b in body[1:]]


def _entries(labels: list[str], texts: list[str], sep: str,
             width: int) -> list[str]:
    """Lays out a list of labelled descriptions in one column.

    Args:
        labels: Each entry's label.
        texts:  Each entry's description.
        sep:    What separates the longest label from its description.
        width:  The widest a line may be.

    Returns:
        The lines of every entry.
    """
    col = max(len(label) for label in labels)
    out: list[str] = []
    for label, text in zip(labels, texts):
        out += _hang(label.ljust(col) + sep, text, width)
    return out


def rust(summary: str, params: Entries = (), returns: Optional[str] = None,
         errors: Optional[str] = None, indent: str = "") -> list[str]:
    """Formats a rustdoc header.

    Args:
        summary: What the function does.
        params:  Each parameter, other than a receiver, with what it
                 means.
        returns: What the function returns, or None if nothing.
        errors:  When the function returns an error, for one returning a
                 ``Result``, or None.
        indent:  The indentation of the function.

    Returns:
        The header's lines, each starting with ``indent`` and ``///``.
    """
    lead = indent + "/// "
    width = WIDTH - len(lead)
    out = fill(summary, width)
    if params:
        out += ["", "# Arguments", ""]
        out += _entries([f"* `{n}`" for n, _ in params], [d for _, d in params],
                        " - ", width)
    for title, text in (("Returns", returns), ("Errors", errors)):
        if text:
            out += ["", f"# {title}", ""] + fill(text, width)
    return [(lead + line).rstrip() for line in out]


def doxygen(summary: str, params: Entries = (), returns: Optional[str] = None,
            throws: Entries = (), indent: str = "") -> list[str]:
    """Formats a Doxygen header.

    Args:
        summary: What the function does.
        params:  Each parameter with what it means.
        returns: What the function returns, or None if nothing.
        throws:  Each exception the function throws, with when.
        indent:  The indentation of the function.

    Returns:
        The header's lines: ``/**``, the summary, then the ``@param``,
        ``@return`` and ``@throws`` entries, their descriptions in one
        column, and ``*/``.
    """
    width = WIDTH - len(indent) - len("/** ")
    clean = lambda s: s.replace("*/", "* /")  # noqa: E731
    body = fill(clean(summary), width)
    labels = [f"@param {n}" for n, _ in params]
    texts = [d for _, d in params]
    if returns:
        labels.append("@return")
        texts.append(returns)
    if labels or throws:
        body.append("")
    if labels:
        body += _entries(labels, [clean(t) for t in texts], "  ", width)
    for e, d in throws:
        # An exception's name can be long, so its text isn't in the
        # parameters' column.
        body += _hang(f"@throws {e} ", clean(d), width)
    lines = [indent + "/** " + body[0]]
    lines += [(indent + " * " + b).rstrip() for b in body[1:]]
    if len(lines[-1]) + 3 <= WIDTH:
        lines[-1] += " */"
    else:
        lines.append(indent + " */")
    return lines


def google(summary: str, params: Entries = (), returns: Optional[str] = None,
           raises: Entries = (), indent: str = "") -> list[str]:
    """Formats a Google style docstring.

    Args:
        summary: What the function does.
        params:  Each parameter, other than ``self`` or ``cls``, with what
                 it means.
        returns: What the function returns, or None if nothing.
        raises:  Each exception the function raises, with when.
        indent:  The indentation of the function's body.

    Returns:
        The docstring's lines, a raw string if the text holds a
        backslash.
    """
    texts = [summary, returns or ""] + [d for _, d in params]
    texts += [d for _, d in raises]
    quote = 'r"""' if any("\\" in t for t in texts) else '"""'
    width = WIDTH - len(indent) - len(quote)
    if not (params or returns or raises):
        one = fill(summary, width - 3)
        if len(one) == 1:
            return [f"{indent}{quote}{one[0]}\"\"\""]
    body = fill(summary, width)
    inner = WIDTH - len(indent) - 4
    sections = []
    if params:
        sections.append(("Args",
                         _entries([f"{n}:" for n, _ in params],
                                  [d for _, d in params], " ", inner)))
    if returns:
        sections.append(("Returns", fill(returns, inner)))
    if raises:
        sections.append(("Raises",
                         _entries([f"{e}:" for e, _ in raises],
                                  [d for _, d in raises], " ", inner)))
    for title, lines in sections:
        body += ["", f"{title}:"] + ["    " + line for line in lines]
    out = [indent + quote + body[0]]
    out += [(indent + b).rstrip() for b in body[1:]]
    return out + [indent + '"""']


# -- phrases ------------------------------------------------------------------


def series(items: list[str], conj: str = "and") -> str:
    """Joins items as English does.

    Args:
        items: The items, at least one.
        conj:  The word before the last item.

    Returns:
        ``a``, ``a and b`` or ``a, b and c``.
    """
    if len(items) < 3:
        return f" {conj} ".join(items)
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


def sentence(clause: str) -> str:
    """Makes a sentence of a clause: a capital first letter and a stop."""
    return clause[:1].upper() + clause[1:] + "."


def first_sentence(text: str) -> str:
    """Finds the first sentence of a schema annotation.

    Args:
        text: The annotation, with its whitespace already collapsed.

    Returns:
        Its first sentence, with its final stop, or ``""`` if it has
        none.
    """
    m = re.match(r"(.+?[.!?])(?=\s+[A-Z(]|$)", text.strip())
    return m.group(1) if m else text.strip()


def element_names(b: Binding) -> dict[str, list[str]]:
    """Finds the element names the schema gives each complex type.

    Args:
        b: The binding.

    Returns:
        For each record's name, the names of the elements of that type,
        sorted.
    """
    uses: dict[str, set[str]] = {n: set() for n in b.records}
    for tag, t in b.schema.roots.items():
        uses[t].add(tag)
    for r in b.records.values():
        for f in r.content:
            if isinstance(f, ElemField) and isinstance(f.type, ComplexRef):
                uses[f.type.name].add(f.xml)
    for c in b.choices.values():
        for v in c.variants:
            if isinstance(v.type, ComplexRef):
                uses[v.type.name].add(v.xml)
    return {n: sorted(s) for n, s in uses.items()}


def _counted(f) -> bool:
    """Says whether a writer checks how many items a field holds.

    Args:
        f: A content field of a record.

    Returns:
        Whether it is repeated with bounds narrower than none to
        unbounded.
    """
    return f.card == MANY and not (f.min == 0 and f.max == UNBOUNDED)


def write_failures(b: Binding, choices_fail: bool,
                   counts: bool = True) -> dict[str, str]:
    """Finds which types' writers can fail, and why.

    Args:
        b:            The binding.
        choices_fail: Whether writing a choice fails when it holds an
                      alternative it doesn't have, as in Python.
        counts:       Whether writing a repeated element with more or
                      fewer items than the schema allows fails.

    Returns:
        For each record or choice whose writer can fail: ``"own"`` if it
        can fail itself, otherwise ``"nested"`` if writing what it holds
        can fail.
    """
    fails: dict[str, str] = {}
    if choices_fail:
        fails.update((n, "own") for n in b.choices)
    for name, r in b.records.items():
        if counts and any(_counted(f) for f in r.content):
            fails[name] = "own"
    changed = True
    while changed:
        changed = False
        for name, r in b.records.items():
            if name in fails:
                continue
            for f in r.content:
                inner = (f.type.name if isinstance(f, ElemField)
                         and isinstance(f.type, ComplexRef) else None
                         if isinstance(f, ElemField) else f.choice.name)
                if inner in fails:
                    fails[name], changed = "nested", True
                    break
        for name, c in b.choices.items():
            if name in fails:
                continue
            if (any(isinstance(v.type, ComplexRef) and v.type.name in fails
                    for v in c.variants)):
                fails[name], changed = "nested", True
    return fails


def write_error(how: str, what: str) -> str:
    """Says when writing a value fails because of a repeated element.

    Args:
        how:  ``"own"`` or ``"nested"``, from ``write_failures``.
        what: What the text calls the value being written.

    Returns:
        The condition, as a clause starting "if".
    """
    who = (f"{what}, or a value nested in it," if how == "own"
           else f"a value nested in {what}")
    return (f"if {who} has a repeated element that occurs fewer or more times "
            "than the schema allows")


def type_phrase(b: Binding, r: Record, names: list[str],
                q: Callable[[str], str]) -> str:
    """Names the schema type of a record for a summary.

    Args:
        b:     The binding.
        r:     The record.
        names: The names of the elements of its type.
        q:     Quotes a name as code.

    Returns:
        ``the XTCE type `T` ``, or for an anonymous type, the element
        that has it.
    """
    if r.abstract:
        return f"a type derived from the abstract XTCE type {q(r.name)}"
    if names and b.schema.complex[r.name].anonymous:
        return f"an XTCE {q(names[0])} element"
    return f"the XTCE type {q(r.name)}"


def tag_param(names: list[str], q: Callable[[str], str]) -> str:
    """Describes the element-name parameter of a record's writer.

    Args:
        names: The names the schema gives elements of the record's type.
        q:     Quotes a name as code.

    Returns:
        The description.
    """
    if not names:
        return "The element's name."
    return ("The element's name; the schema names elements of this type "
            f"{series([q(n) for n in names], 'or')}.")


def read_error(b: Binding, r: Record, q: Callable[[str], str],
               streaming: bool) -> str:
    """Says when reading a record's element fails.

    Args:
        b:         The binding.
        r:         The record.
        q:         Quotes a name as code.
        streaming: Whether the reader parses the XML as it reads it, so
                   that malformed XML is an error here too.

    Returns:
        The condition, as a clause starting "if".
    """
    whys = ["the element has an attribute its type doesn't declare"
            + (" (other than a namespace declaration or a prefixed one)"
               if streaming else "")]
    req = [q(a.xml) for a in r.attrs if a.required]
    if req:
        word = "attribute" if len(req) == 1 else "attributes"
        whys.append(f"lacks the required {word} {series(req)}")
    if r.attrs and r.text is not None:
        whys.append("has an attribute value or text that doesn't parse")
    elif r.attrs:
        whys.append("has an attribute value that doesn't parse")
    elif r.text is not None:
        whys.append("has text that doesn't parse")
    if r.text is not None:
        whys.append("has child elements")
    elif r.content:
        parts = []
        for f in r.content:
            if isinstance(f, ElemField):
                parts.append(q(f.xml))
            else:
                parts.append(f"alternatives of {q(f.choice.name)}"
                             if f.card == MANY
                             else f"an alternative of {q(f.choice.name)}")
        whys.append("has children that don't follow its content model "
                    f"({', '.join(parts)}, in that order and in the numbers "
                    "the schema allows) or that are themselves invalid")
    else:
        whys.append("has children or text")
    text = "if " + series(whys, "or")
    if streaming:
        text += "; or if the XML is malformed"
    return text


def choice_names(c: ChoiceType, q: Callable[[str], str],
                 conj: str = "or") -> str:
    """Lists the element names of a choice's alternatives.

    Args:
        c:    The choice.
        q:    Quotes a name as code.
        conj: The word before the last name.

    Returns:
        The names, joined as English does.
    """
    return series([q(v.xml) for v in c.variants], conj)
