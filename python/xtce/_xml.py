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
    """An element: local name, attributes, child elements, and direct text."""

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
    """Parse bytes-like ``data`` into a :class:`Node` tree."""
    parser = expat.ParserCreate(namespace_separator=" ")
    stack: list[Node] = []
    root: list[Node] = []

    def start(name, attrs):
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
    """Walks the child elements of a node with one element of lookahead."""

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
        if self.i < self.n:
            c = self.node.children[self.i]
            return XtceError(f"expected <{what}>, found <{c.name}>", c.line)
        return XtceError(f"expected <{what}> in <{self.node.name}>", self.node.line)

    def too_few(self, what: str, lo: int, found: int) -> XtceError:
        return XtceError(
            f"expected at least {lo} <{what}> elements in <{self.node.name}>, found {found}",
            self.node.line,
        )

    def end(self) -> None:
        if self.i < self.n:
            c = self.node.children[self.i]
            raise XtceError(f"unexpected element <{c.name}>", c.line)
        if any(t.strip() for t in self.node.text):
            raise XtceError(f"unexpected text in <{self.node.name}>", self.node.line)


def text_of(node: Node) -> str:
    if node.children:
        c = node.children[0]
        raise XtceError(f"unexpected element <{c.name}> in text content", c.line)
    return "".join(node.text)


def check_attrs(node: Node, allowed: frozenset) -> None:
    for k in node.attrs:
        if k not in allowed and not k.startswith("xml:"):
            raise XtceError(f"unexpected attribute {k!r} on <{node.name}>", node.line)


def req_attr(node: Node, name: str, parse_fn: Callable):
    try:
        raw = node.attrs[name]
    except KeyError:
        raise XtceError(f"missing required attribute {name!r} on <{node.name}>", node.line) from None
    return value(node, raw, parse_fn)


def opt_attr(node: Node, name: str, parse_fn: Callable):
    raw = node.attrs.get(name)
    return None if raw is None else value(node, raw, parse_fn)


def value(node: Node, raw: str, parse_fn: Callable):
    try:
        return parse_fn(raw)
    except ValueError as e:
        raise XtceError(str(e), node.line) from None


def leaf(node: Node, parse_fn: Callable):
    for k in node.attrs:
        if not k.startswith("xml:"):
            raise XtceError(f"unexpected attribute {k!r} on <{node.name}>", node.line)
    return value(node, text_of(node), parse_fn)


# -- scalar values -------------------------------------------------------------

_INT = re.compile(r"[+-]?[0-9]+\Z")
_DOUBLE = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?\Z")


def p_str(s: str) -> str:
    return s


def p_bool(s: str) -> bool:
    t = s.strip()
    if t in ("true", "1"):
        return True
    if t in ("false", "0"):
        return False
    raise ValueError(f"invalid boolean {s!r}")


def _p_int(lo: int, hi: int, kind: str) -> Callable[[str], int]:
    def parse_int(s: str) -> int:
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
    """Shortest round-trip form; matches the Rust and C++ libraries."""
    if v != v:
        return "NaN"
    if v == math.inf:
        return "INF"
    if v == -math.inf:
        return "-INF"
    return repr(float(v))


# -- writing -------------------------------------------------------------------

_TEXT_ESC = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", "\r": "&#13;"})
_ATTR_ESC = str.maketrans(
    {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "\n": "&#10;", "\r": "&#13;", "\t": "&#9;"}
)

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
        if self.open:
            self.parts.append(">")
            self.open = False
        self.parts.append(value.translate(_TEXT_ESC))
        self.last = _TEXT

    def end(self, name: str) -> None:
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
        if self.indent:
            self.parts.append("\n")
        buf.load("".join(self.parts).encode("utf-8"))
        self.parts.clear()


def check_count(n: int, lo: int, hi: int, what: str) -> None:
    if n < lo or (hi >= 0 and n > hi):
        mx = "unbounded" if hi < 0 else str(hi)
        raise XtceError(f"{what}: {n} occurrences, expected {lo}..{mx}")
