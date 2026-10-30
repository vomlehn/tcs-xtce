"""XML reading and writing support used by the generated bindings.

Reading uses the C ``expat`` parser directly over a managed buffer and builds
a light node tree that records line numbers for error messages.  Writing
appends text pieces into a reusable list that is encoded once into a
managed buffer.
"""

from __future__ import annotations

import math
import re
import xml.parsers.expat as expat
from typing import Callable, Optional

from ._buffer import ManagedBuffer

XML_NS = "http://www.w3.org/XML/1998/namespace"


class XtceError(ValueError):
    """Raised for malformed or schema-invalid XTCE input or output."""

    def __init__(self, message: str, line: int = 0) -> None:
        super().__init__(f"{line}: {message}" if line else message)
        self.message = message
        self.line = line


# -- reading -------------------------------------------------------------------


class Node:
    """An element: local name, attributes, child elements, and direct text.

    Args:
        name:  The element's local name.
        attrs: Its attributes by name, ``xml:`` ones with that prefix.
        line:  The line it starts on, for error messages.
    """

    __slots__ = ("name", "attrs", "children", "text", "line")

    def __init__(self, name: str, attrs: dict, line: int) -> None:
        self.name = name
        self.attrs = attrs
        self.children: list[Node] = []
        self.text: list[str] = []
        self.line = line


def _local(name: str) -> str:
    # expat reports namespaced names as "uri local".
    i = name.rfind(" ")
    return name if i < 0 else name[i + 1 :]


def parse(data) -> Node:
    """Parses bytes-like ``data`` into a :class:`Node` tree.

    Args:
        data: The XML.

    Returns:
        The root element.

    Raises:
        XtceError: The XML isn't well formed.
    """
    parser = expat.ParserCreate(namespace_separator=" ")
    stack: list[Node] = []
    root: list[Node] = []

    def start(name, attrs):
        """Opens a new element below the current one.

        Keeps attributes in no namespace and ``xml:`` ones; drops other
        namespaced attributes.

        Args:
            name:  The element's name as expat reports it, ``"uri local"``
                   when it has a namespace.
            attrs: Its attributes by name, named the same way.
        """
        clean = {}
        for k, val in attrs.items():
            if " " in k:
                uri, local = k.rsplit(" ", 1)
                if uri == XML_NS:
                    clean["xml:" + local] = val
                # Other namespaced attributes (xsi:...) are ignored.
            else:
                clean[k] = val
        node = Node(_local(name), clean, parser.CurrentLineNumber)
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)

    def end(name):
        stack.pop()

    def chars(text):
        if stack:
            stack[-1].text.append(text)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.buffer_text = True
    try:
        parser.Parse(data, True)
    except expat.ExpatError as e:
        raise XtceError(expat.errors.messages[e.code], e.lineno) from None
    return root[0]


class Cursor:
    """Walks the child elements of a node with one element of lookahead.

    Args:
        node: The element whose children are walked.

    Raises:
        XtceError: The element mixes child elements with text.
    """

    __slots__ = ("node", "i", "n")

    def __init__(self, node: Node) -> None:
        if node.children and any(t.strip() for t in node.text):
            raise XtceError(f"unexpected text in <{node.name}>", node.line)
        self.node = node
        self.i = 0
        self.n = len(node.children)

    def peek(self) -> Optional[str]:
        return self.node.children[self.i].name if self.i < self.n else None

    def take(self) -> Node:
        c = self.node.children[self.i]
        self.i += 1
        return c

    def expected(self, what: str) -> XtceError:
        """Makes the error for a missing child element.

        Args:
            what: The name of the element that was expected.

        Returns:
            An error naming the element found instead, or the parent if
            there are no more children.
        """
        if self.i < self.n:
            c = self.node.children[self.i]
            return XtceError(f"expected <{what}>, found <{c.name}>", c.line)
        return XtceError(f"expected <{what}> in <{self.node.name}>",
                         self.node.line)

    def too_few(self, what: str, lo: int, found: int) -> XtceError:
        return XtceError(f"expected at least {lo} <{what}> elements in "
                         f"<{self.node.name}>, found {found}", self.node.line)

    def end(self) -> None:
        """Checks that every child has been taken.

        Raises:
            XtceError: A child element is left, or the node has text.
        """
        if self.i < self.n:
            c = self.node.children[self.i]
            raise XtceError(f"unexpected element <{c.name}>", c.line)
        if any(t.strip() for t in self.node.text):
            raise XtceError(f"unexpected text in <{self.node.name}>",
                            self.node.line)


def text_of(node: Node) -> str:
    """Gets an element's text content.

    Args:
        node: The element, which must have no children.

    Returns:
        Its text, all pieces joined.

    Raises:
        XtceError: The element has a child element.
    """
    if node.children:
        c = node.children[0]
        raise XtceError(f"unexpected element <{c.name}> in text content",
                        c.line)
    return "".join(node.text)


def check_attrs(node: Node, allowed: frozenset) -> None:
    """Checks that an element has only allowed attributes.

    ``xml:`` attributes are always allowed.

    Args:
        node:    The element.
        allowed: The names of the attributes it may have.

    Raises:
        XtceError: The element has another attribute.
    """
    for k in node.attrs:
        if k not in allowed and not k.startswith("xml:"):
            raise XtceError(f"unexpected attribute {k!r} on <{node.name}>",
                            node.line)


def req_attr(node: Node, name: str, parse_fn: Callable):
    """Reads an attribute that must be present.

    Args:
        node:     The element.
        name:     The attribute's name.
        parse_fn: Converts the attribute's text to its value, raising
                  ValueError if it is invalid.

    Returns:
        The attribute's value.

    Raises:
        XtceError: The attribute is missing or invalid.
    """
    try:
        raw = node.attrs[name]
    except KeyError:
        raise XtceError(f"missing required attribute {name!r} on <{node.name}>",
                        node.line) from None
    return value(node, raw, parse_fn)


def opt_attr(node: Node, name: str, parse_fn: Callable):
    raw = node.attrs.get(name)
    return None if raw is None else value(node, raw, parse_fn)


def value(node: Node, raw: str, parse_fn: Callable):
    """Converts the text of an attribute or element with a parser.

    Args:
        node:     The element the text belongs to, for error messages.
        raw:      The text.
        parse_fn: Converts the text, raising ValueError if it is invalid.

    Returns:
        What ``parse_fn`` returns.

    Raises:
        XtceError: ``parse_fn`` raised ValueError.
    """
    try:
        return parse_fn(raw)
    except ValueError as e:
        raise XtceError(str(e), node.line) from None


def leaf(node: Node, parse_fn: Callable):
    """Reads an element that holds only a value in its text.

    Args:
        node:     The element, which may have only ``xml:`` attributes.
        parse_fn: Converts its text, raising ValueError if it is invalid.

    Returns:
        The element's value.

    Raises:
        XtceError: The element has another attribute, a child element or
                   invalid text.
    """
    for k in node.attrs:
        if not k.startswith("xml:"):
            raise XtceError(f"unexpected attribute {k!r} on <{node.name}>",
                            node.line)
    return value(node, text_of(node), parse_fn)


# -- scalar values -------------------------------------------------------------

_INT = re.compile(r"[+-]?[0-9]+\Z")
_DOUBLE = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?\Z")


def p_str(s: str) -> str:
    return s


def p_bool(s: str) -> bool:
    """Parses an XML Schema boolean.

    Args:
        s: The text, ``true``, ``false``, ``1`` or ``0`` with optional
           surrounding white space.

    Returns:
        The boolean.

    Raises:
        ValueError: The text is none of those.
    """
    t = s.strip()
    if t in ("true", "1"):
        return True
    if t in ("false", "0"):
        return False
    raise ValueError(f"invalid boolean {s!r}")


def _p_int(lo: int, hi: int, kind: str) -> Callable[[str], int]:
    """Makes a parser for integers in a range.

    Args:
        lo:   The smallest value allowed.
        hi:   The largest value allowed.
        kind: The type's name, for error messages.

    Returns:
        A function that parses a decimal integer and checks its range,
        raising ValueError if it is invalid.
    """
    def parse_int(s: str) -> int:
        """Parses a decimal integer and checks its range.

        Args:
            s: The text, with optional sign and surrounding white space.

        Returns:
            The integer.

        Raises:
            ValueError: The text isn't an integer or is out of range.
        """
        t = s.strip()
        if not _INT.match(t):
            raise ValueError(f"invalid {kind} {s!r}")
        v = int(t)
        if not lo <= v <= hi:
            raise ValueError(f"invalid {kind} {s!r}: out of range")
        return v

    return parse_int


p_i32 = _p_int(-(2**31), 2**31 - 1, "i32")
p_i64 = _p_int(-(2**63), 2**63 - 1, "i64")
p_u16 = _p_int(0, 2**16 - 1, "u16")


def p_double(s: str) -> float:
    """Parses an XML Schema double.

    Args:
        s: The text, a decimal or ``INF``, ``+INF``, ``-INF`` or ``NaN``,
           with optional surrounding white space.

    Returns:
        The value.

    Raises:
        ValueError: The text isn't a double.
    """
    t = s.strip()
    if t in ("INF", "+INF"):
        return math.inf
    if t == "-INF":
        return -math.inf
    if t == "NaN":
        return math.nan
    if not _DOUBLE.match(t):
        raise ValueError(f"invalid double {s!r}")
    return float(t)


def f_str(v: str) -> str:
    return v


def f_bool(v: bool) -> str:
    return "true" if v else "false"


def f_int(v: int) -> str:
    return str(v)


def f_double(v: float) -> str:
    """Formats a double in the shortest form that reads back the same.

    Matches the Rust and C++ libraries.

    Args:
        v: The value.

    Returns:
        Its text: ``NaN``, ``INF``, ``-INF`` or Python's ``repr``.
    """
    if v != v:
        return "NaN"
    if v == math.inf:
        return "INF"
    if v == -math.inf:
        return "-INF"
    return repr(float(v))


# -- writing -------------------------------------------------------------------

_TEXT_ESC = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;",
                           "\r": "&#13;"})
_ATTR_ESC = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;",
                           '"': "&quot;", "\n": "&#10;", "\r": "&#13;",
                           "\t": "&#9;"})

_NOTHING, _START, _TEXT, _END = range(4)


class Writer:
    """Builds XML text; :meth:`flush` encodes it into a managed buffer.

    The list of pieces is kept between documents so its storage is reused.
    """

    __slots__ = ("parts", "depth", "open", "last", "indent", "root_ns")

    def __init__(self, indent: bool = True) -> None:
        self.parts: list[str] = []
        self.indent = indent
        self.reset()

    def reset(self) -> None:
        """Empties the writer for a new document, keeping its storage."""
        self.parts.clear()
        self.depth = 0
        self.open = False
        self.last = _NOTHING
        self.root_ns: Optional[str] = None

    def declaration(self) -> None:
        self.parts.append('<?xml version="1.0" encoding="UTF-8"?>')
        self.last = _END

    def _newline(self) -> None:
        if self.indent and self.last != _NOTHING:
            self.parts.append("\n" + "  " * self.depth)

    def start(self, name: str) -> None:
        """Starts an element, closing the previous start tag if it is open.

        The first element after :attr:`root_ns` is set gets it as its
        ``xmlns``.

        Args:
            name: The element's name.
        """
        if self.open:
            self.parts.append(">")
            self.open = False
        self._newline()
        self.parts.append("<" + name)
        self.open = True
        self.depth += 1
        self.last = _START
        if self.root_ns is not None:
            self.attr("xmlns", self.root_ns)
            self.root_ns = None

    def attr(self, name: str, value: str) -> None:
        self.parts.append(f' {name}="{value.translate(_ATTR_ESC)}"')

    def text(self, value: str) -> None:
        """Writes escaped text into the current element.

        Args:
            value: The text.
        """
        if self.open:
            self.parts.append(">")
            self.open = False
        self.parts.append(value.translate(_TEXT_ESC))
        self.last = _TEXT

    def end(self, name: str) -> None:
        """Ends an element, as ``/>`` if it has no content.

        Args:
            name: The element's name.
        """
        self.depth -= 1
        if self.open:
            self.parts.append("/>")
            self.open = False
        else:
            if self.last == _END:
                self._newline()
            self.parts.append(f"</{name}>")
        self.last = _END

    def leaf(self, name: str, value: str) -> None:
        self.start(name)
        self.text(value)
        self.end(name)

    def flush(self, buf: ManagedBuffer) -> None:
        """Encodes the XML into a buffer and empties the writer.

        Args:
            buf: The buffer, whose contents are replaced.
        """
        if self.indent:
            self.parts.append("\n")
        buf.load("".join(self.parts).encode("utf-8"))
        self.parts.clear()


def check_count(n: int, lo: int, hi: int, what: str) -> None:
    if n < lo or (hi >= 0 and n > hi):
        mx = "unbounded" if hi < 0 else str(hi)
        raise XtceError(f"{what}: {n} occurrences, expected {lo}..{mx}")
