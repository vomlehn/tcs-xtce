"""The resolved model of a mission, as canonical text.

``python3 -m tcs_xtce INPUT... OUTPUT --dump-model`` writes it.  The Rust
model, ``tcs-xtce-model``, writes the same text for the same documents, so
that the two can be compared line for line while the compiler moves to
Rust: see docs/rust-migration-design.rst.

The text has one value to a line, indented two spaces for each level:

- ``none``, ``true`` and ``false``; integers in decimal; floats as Python's
  ``repr`` writes them (``1.0``, ``1e+16``, ``nan``, ``inf``); strings in
  double quotes, with ``\\``, ``"``, newline, carriage return and tab
  escaped as in C and any other control character as ``\\xHH``.
- A record (a model class) is its class name and ``{``, then each field
  as ``name: value`` in the order the class declares them, then ``}``.
- A list or tuple is ``[``, each element, then ``]``; ``[]`` when empty.
- A dictionary is ``{``, each entry as ``key => value`` in its order, then
  ``}``; ``{}`` when empty.
- A record that compares by identity (a dataclass with ``eq=False``, such
  as Node, Local and Hook) and is reached more than once is numbered where
  it is first written, ``Node #3 {``, and written as ``Node #3`` wherever
  it is reached again, so that sharing is shown and the links between
  nodes don't loop. Any other record is a value, written in full wherever
  it is reached: whether two values are one object is an accident of how
  they were copied, which the Rust model need not repeat. A value record
  reached again while it is being written, through records inside it, is
  written there as its class name and ``^``, ``VType ^``: the one above.
- An element of the XTCE document, which the model keeps where it is
  bound only later (a ParameterInstanceRef in a type's calibrator, say),
  is ``xtce`` and its class name: what it says is the document's, not the
  compiler's.

Numbers are given in the order records are first written, walking the
mission's fields in order and each record's fields depth first.
"""

from __future__ import annotations

import dataclasses
import math

from tcs_xtce.compiler.model import Cycle, Mission, Unsupported


def string(s: str) -> str:
    """Writes a string as the canonical text does.

    Args:
        s: The string.

    Returns:
        It in double quotes, escaped.
    """
    out = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def scalar(v) -> str | None:
    """Writes a value that takes no lines of its own.

    Args:
        v: The value.

    Returns:
        Its text, or None if it is a record, list, tuple or dictionary.
    """
    if v is None:
        return "none"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        return repr(v)
    if isinstance(v, str):
        return string(v)
    if isinstance(v, (bytes, bytearray)):
        return "bytes " + (v.hex() if v else "(empty)")
    return None


def identity(v) -> bool:
    """Says whether a value is a record that compares by identity.

    Args:
        v: The value.

    Returns:
        True for an instance of a dataclass declared with ``eq=False``.
    """
    return (dataclasses.is_dataclass(v) and not isinstance(v, type)
            and not type(v).__dataclass_params__.eq)


def element(v) -> bool:
    """Says whether a value is an element of an XTCE document.

    Args:
        v: The value.

    Returns:
        True for an instance of a class of the XML binding.
    """
    return type(v).__module__ == "tcs_xtce.generated"


class Dumper:
    """Writes a mission's model as canonical text.

    Attributes:
        seen:    How many times each record that compares by identity
                 is reached, by its id(), from the first pass.
        number:  The number given to each shared record written so far,
                 by its id().
        writing: The ids of the records being written, so that a value
                 record reached again inside itself is written as one.
        lines:   The text so far, one line to an element.
    """

    def __init__(self) -> None:
        """Makes a dumper with nothing counted or written yet."""
        self.seen: dict[int, int] = {}
        self.number: dict[int, int] = {}
        self.writing: set[int] = set()
        self.lines: list[str] = []

    def count(self, v) -> None:
        """Counts how often each record is reached from a value.

        Args:
            v: The value, walked as dumping walks it.
        """
        if element(v):
            return
        if dataclasses.is_dataclass(v) and not isinstance(v, type):
            if identity(v):
                n = self.seen.get(id(v), 0)
                self.seen[id(v)] = n + 1
                if n:
                    return
            elif id(v) in self.writing:
                return
            self.writing.add(id(v))
            for f in dataclasses.fields(v):
                self.count(getattr(v, f.name))
            self.writing.discard(id(v))
        elif isinstance(v, (list, tuple)):
            for x in v:
                self.count(x)
        elif isinstance(v, dict):
            for k, x in v.items():
                self.count(k)
                self.count(x)

    def put(self, depth: int, head: str, v) -> None:
        """Writes a value on lines of its own.

        Args:
            depth: Its indentation, in levels.
            head:  What goes before it on its first line: a field name and
                   ``: ``, a key and `` => ``, or nothing.
            v:     The value.

        Raises:
            TypeError: If the value is of a kind the text has no form for,
                       so that nothing in the model is left out unseen.
        """
        pad = "  " * depth
        s = scalar(v)
        if s is not None:
            self.lines.append(f"{pad}{head}{s}")
            return
        if element(v):
            self.lines.append(f"{pad}{head}xtce {type(v).__name__}")
            return
        if dataclasses.is_dataclass(v) and not isinstance(v, type):
            name = type(v).__name__
            if id(v) in self.number:
                self.lines.append(f"{pad}{head}{name} #{self.number[id(v)]}")
                return
            if not identity(v) and id(v) in self.writing:
                self.lines.append(f"{pad}{head}{name} ^")
                return
            if self.seen.get(id(v), 0) > 1:
                self.number[id(v)] = len(self.number) + 1
                name += f" #{self.number[id(v)]}"
            self.lines.append(f"{pad}{head}{name} {{")
            self.writing.add(id(v))
            for f in dataclasses.fields(v):
                self.put(depth + 1, f"{f.name}: ", getattr(v, f.name))
            self.writing.discard(id(v))
            self.lines.append(f"{pad}}}")
        elif isinstance(v, (list, tuple)):
            if not v:
                self.lines.append(f"{pad}{head}[]")
                return
            self.lines.append(f"{pad}{head}[")
            for x in v:
                self.put(depth + 1, "", x)
            self.lines.append(f"{pad}]")
        elif isinstance(v, dict):
            if not v:
                self.lines.append(f"{pad}{head}{{}}")
                return
            self.lines.append(f"{pad}{head}{{")
            for k, x in v.items():
                self.put(depth + 1, f"{self.key(k)} => ", x)
            self.lines.append(f"{pad}}}")
        else:
            raise TypeError("the model dump has no form for "
                            f"{type(v).__name__}: {v!r}")

    def key(self, k) -> str:
        """Writes a dictionary key, which must fit on one line.

        Args:
            k: The key: a scalar, or a record reached elsewhere first.

        Returns:
            Its text.

        Raises:
            TypeError: If the key is a record not yet written, or of a
                       kind the text has no one-line form for.
        """
        s = scalar(k)
        if s is not None:
            return s
        if dataclasses.is_dataclass(k) and id(k) in self.number:
            return f"{type(k).__name__} #{self.number[id(k)]}"
        if isinstance(k, tuple) and all(scalar(x) is not None for x in k):
            return "(" + ", ".join(scalar(x) for x in k) + ")"
        raise TypeError("the model dump has no one-line form for the key "
                        f"{type(k).__name__}: {k!r}")


def dump(mission: Mission) -> str:
    """Writes a mission's resolved model as canonical text.

    Args:
        mission: The model, as the compiler resolved it.

    Returns:
        The text, ending in a newline.
    """
    d = Dumper()
    d.count(mission)
    d.put(0, "", mission)
    return "\n".join(d.lines) + "\n"


@dataclasses.dataclass
class Failed:
    """A type that does not resolve, as ``dump_types`` writes it.

    Attributes:
        error:   ``Unsupported`` or ``Cycle``, the exception's class.
        message: What it says.
    """

    error: str
    message: str


@dataclasses.dataclass
class Types:
    """Every type of a mission, each resolved alone, as ``dump_types``
    writes them.

    Attributes:
        parameter: Each parameter type's resolved type, or why it fails,
                   by qualified name, in the documents' order.
        argument:  Each argument type's, likewise.
        warnings:  The warnings resolving them gave, in order.
    """

    parameter: dict
    argument: dict
    warnings: list


def dump_types(compiler) -> str:
    """Writes every parameter and argument type of a mission, each
    resolved alone, as canonical text: the first stage of the model, which
    the Rust model is compared on before its containers are.

    Args:
        compiler: The Compiler, with the mission's documents indexed and
                  nothing compiled yet.

    Returns:
        The text of a Types, ending in a newline.
    """
    from tcs_xtce.compiler.compile import qname

    def each(table: dict) -> dict:
        """Resolves each type of a table, in its order.

        Args:
            table: ``ptypes`` or ``atypes``.

        Returns:
            Each type's VType, or Failed, by qualified name.
        """
        out = {}
        for key in table:
            try:
                out[qname(key)] = compiler.vtype(key, table)
            except (Unsupported, Cycle) as e:
                out[qname(key)] = Failed(type(e).__name__, str(e))
        return out

    types = Types(each(compiler.ptypes), each(compiler.atypes), [])
    types.warnings = list(compiler.warnings)
    d = Dumper()
    d.count(types)
    d.put(0, "", types)
    return "\n".join(d.lines) + "\n"


@dataclasses.dataclass
class Containers:
    """Every telemetry container of a mission, each compiled as
    ``tm_node`` compiles it, as ``dump_containers`` writes them.

    Attributes:
        container: Each container's Node, or Failed, by qualified name, in
                   the documents' order.
        warnings:  The warnings compiling them gave, in order.
    """

    container: dict
    warnings: list


def dump_containers(compiler) -> str:
    """Writes every telemetry container of a mission, each compiled as
    ``tm_node`` compiles it, before the mission is finished, as canonical
    text: the second stage of the model, which the Rust model is compared
    on before its commands are.

    Args:
        compiler: The Compiler, with the mission's documents indexed and
                  nothing compiled yet.

    Returns:
        The text of a Containers, ending in a newline.
    """
    from tcs_xtce.compiler.compile import qname

    out = {}
    for key in compiler.containers:
        try:
            out[qname(key)] = compiler.tm_node(key)
        except (Unsupported, Cycle) as e:
            out[qname(key)] = Failed(type(e).__name__, str(e))
    c = Containers(out, list(compiler.warnings))
    d = Dumper()
    d.count(c)
    d.put(0, "", c)
    return "\n".join(d.lines) + "\n"


@dataclasses.dataclass
class Commands:
    """Every meta-command and message of a mission, each compiled as
    ``tc_node`` and ``msg_node`` compile it, as ``dump_commands`` writes
    them.

    Attributes:
        command:  Each meta-command's Node, or Failed, by qualified name, in
                  the documents' order.
        message:  Each message's, likewise.
        warnings: The warnings compiling the containers, meta-commands and
                  messages gave, in order.
    """

    command: dict
    message: dict
    warnings: list


def dump_commands(compiler) -> str:
    """Writes every meta-command and message of a mission, each compiled
    as ``tc_node`` and ``msg_node`` compile it after the telemetry
    containers, before the mission is finished, as canonical text: the
    third stage of the model, which the Rust model is compared on before
    the whole mission is.

    Args:
        compiler: The Compiler, with the mission's documents indexed and
                  nothing compiled yet.

    Returns:
        The text of a Commands, ending in a newline.
    """
    from tcs_xtce.compiler.compile import qname

    def each(table: dict, build) -> dict:
        """Compiles each entry of a table, in its order.

        Args:
            table: ``mcs`` or ``messages``.
            build: ``tc_node`` or ``msg_node``.

        Returns:
            Each one's Node, or Failed, by qualified name.
        """
        out = {}
        for key in table:
            try:
                out[qname(key)] = build(key)
            except (Unsupported, Cycle) as e:
                out[qname(key)] = Failed(type(e).__name__, str(e))
        return out

    # As compile does: the containers first, which messages derive from.
    for key in compiler.containers:
        try:
            compiler.tm_node(key)
        except (Unsupported, Cycle):
            pass
    command = each(compiler.mcs, compiler.tc_node)
    message = each(compiler.messages, compiler.msg_node)
    c = Commands(command, message, list(compiler.warnings))
    d = Dumper()
    d.count(c)
    d.put(0, "", c)
    return "\n".join(d.lines) + "\n"
