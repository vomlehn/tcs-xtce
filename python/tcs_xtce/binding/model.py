"""Language-neutral model of the XTCE schema.

The XSD is parsed (see ``xsd.py``) into the classes below.  Inheritance is
flattened so every complex type carries all of its attributes and its full
content model; emitters then only have to map this model to a language.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

UNBOUNDED = -1

# Builtin scalar kinds that the emitters know how to map.
STRING = "string"
BOOL = "bool"
INT = "int"  # xs:int  -> 32-bit
LONG = "long"  # xs:long, xs:integer -> 64-bit
USHORT = "ushort"  # xs:unsignedShort
DOUBLE = "double"

# XSD builtin name -> scalar kind.  Lexical-only types (dates, durations,
# hexBinary, URIs, tokens) are kept as strings so they round-trip exactly.
BUILTINS = {"string": STRING, "normalizedString": STRING, "token": STRING,
            "anyURI": STRING, "dateTime": STRING, "date": STRING,
            "duration": STRING, "hexBinary": STRING, "boolean": BOOL,
            "int": INT, "long": LONG, "integer": LONG, "unsignedShort": USHORT,
            "double": DOUBLE}


@dataclass
class EnumType:
    """A simple type restricted to a set of string values."""

    name: str
    values: list[str]
    doc: str = ""


@dataclass
class ScalarRef:
    """Reference to a builtin scalar (possibly via a restricted simple type)."""

    kind: str
    simple_name: Optional[str] = None  # the XSD simple type it came from
    builtin: str = "string"  # the underlying XSD builtin type


@dataclass
class EnumRef:
    name: str


@dataclass
class ComplexRef:
    name: str


TypeRef = Union[ScalarRef, EnumRef, ComplexRef]


@dataclass
class Attribute:
    name: str  # XML name
    type: TypeRef
    required: bool
    default: Optional[str] = None
    doc: str = ""


@dataclass
class Element:
    name: str  # XML name
    type: TypeRef
    min: int = 1
    max: int = 1
    fixed: Optional[str] = None
    doc: str = ""


@dataclass
class Sequence:
    items: list["Particle"]
    min: int = 1
    max: int = 1


@dataclass
class Choice:
    items: list["Particle"]
    min: int = 1
    max: int = 1


Particle = Union[Element, Sequence, Choice]


@dataclass
class ComplexType:
    name: str
    abstract: bool = False
    base: Optional[str] = None  # base complex type (before flattening)
    attributes: list[Attribute] = field(default_factory=list)
    content: Optional[Sequence] = None  # always a top-level sequence if any
    text: Optional[ScalarRef] = None  # simpleContent / mixed text value
    doc: str = ""
    anonymous: bool = False


@dataclass
class Schema:
    namespace: str
    version: str
    enums: dict[str, EnumType] = field(default_factory=dict)
    simple: dict[str, ScalarRef] = field(default_factory=dict)
    complex: dict[str, ComplexType] = field(default_factory=dict)
    roots: dict[str, str] = field(default_factory=dict)  # element -> type
