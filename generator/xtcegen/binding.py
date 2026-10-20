"""Map the schema model onto language-neutral data-structure layouts.

Every complex type becomes a record ("struct") whose fields are:

* attributes                       -> ``AttrField``
* simple/mixed text content        -> ``TextField``
* child elements                   -> ``ElemField``
* ``<choice>`` groups              -> ``ChoiceField`` referring to a ``ChoiceType``

Cardinalities are one of ONE (exactly one), OPT (zero or one) and MANY.
The analysis also decides which value-embedded fields must be boxed to break
recursive type cycles, a definition order for languages that need one, and a
finite default value for every required field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union

from .model import (
    UNBOUNDED,
    Choice,
    ComplexRef,
    ComplexType,
    Element,
    EnumRef,
    Schema,
    ScalarRef,
    Sequence,
    TypeRef,
)

ONE, OPT, MANY = "one", "opt", "many"


class BindingError(Exception):
    pass


def cardinality(lo: int, hi: int) -> str:
    if hi == 1:
        return ONE if lo == 1 else OPT
    return MANY


def snake(name: str) -> str:
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s)
    return s.strip("_").lower()


def pascal_ident(value: str) -> str:
    """Identifier for an enumeration value, keeping its spelling recognisable."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", value) if p]
    if not parts:
        return "Empty"
    out = parts[0][0].upper() + parts[0][1:]
    for p in parts[1:]:
        out += ("_" if p[0].isdigit() or out[-1].isdigit() else "") + p[0].upper() + p[1:]
    if out[0].isdigit():
        out = "V" + out
    return out


@dataclass
class AttrField:
    ident: str
    xml: str
    type: TypeRef
    required: bool
    default: Optional[str]
    doc: str


@dataclass
class TextField:
    ident: str
    type: ScalarRef


@dataclass
class ElemField:
    ident: str
    xml: str
    type: TypeRef
    card: str
    min: int
    max: int
    boxed: bool = False
    doc: str = ""
    fixed: Optional[str] = None


@dataclass
class Variant:
    ident: str  # PascalCase variant name
    snake: str
    xml: str
    type: TypeRef
    boxed: bool = False
    doc: str = ""
    fixed: Optional[str] = None


@dataclass
class ChoiceType:
    name: str
    owner: str
    variants: list[Variant]
    default_variant: int = 0


@dataclass
class ChoiceField:
    ident: str
    choice: ChoiceType
    card: str
    min: int
    max: int
    boxed: bool = False


ContentField = Union[ElemField, ChoiceField]


@dataclass
class Record:
    name: str
    doc: str
    abstract: bool
    attrs: list[AttrField] = field(default_factory=list)
    text: Optional[TextField] = None
    content: list[ContentField] = field(default_factory=list)


@dataclass
class Binding:
    schema: Schema
    records: dict[str, Record]
    choices: dict[str, ChoiceType]
    order: list[str]  # records and choices, dependencies first

    def first(self, f: ContentField) -> list[str]:
        if isinstance(f, ElemField):
            return [f.xml]
        return [v.xml for v in f.choice.variants]


# -- content-model normalisation ---------------------------------------------


def _mul(a: int, b: int) -> int:
    if a == UNBOUNDED or b == UNBOUNDED:
        return 0 if (a == 0 or b == 0) else UNBOUNDED
    return a * b


def _normalize(p):
    """Simplify a particle tree without changing the language it accepts."""
    if isinstance(p, Element):
        return p
    items = []
    for it in (_normalize(i) for i in p.items):
        if type(it) is type(p) and it.min == it.max == 1:
            items.extend(it.items)  # splice same-kind 1..1 groups
        else:
            items.append(it)
    if len(items) == 1:
        only = items[0]
        lo, hi = _mul(p.min, only.min), _mul(p.max, only.max)
        if isinstance(only, Element):
            return Element(only.name, only.type, lo, hi, only.fixed, only.doc)
        return type(only)(only.items, lo, hi)
    return type(p)(items, p.min, p.max)


# -- binding construction ----------------------------------------------------


class _Builder:
    def __init__(self, schema: Schema):
        self.schema = schema
        self.records: dict[str, Record] = {}
        self.choices: dict[str, ChoiceType] = {}
        self.names = set(schema.complex) | set(schema.enums) | set(schema.simple)

    def unique_type_name(self, base: str) -> str:
        name, n = base, 2
        while name in self.names:
            name = f"{base}{n}"
            n += 1
        self.names.add(name)
        return name

    def build(self) -> Binding:
        for ct in self.schema.complex.values():
            self.records[ct.name] = self.record(ct)
        b = Binding(self.schema, self.records, self.choices, [])
        _analyze(b)
        return b

    def record(self, ct: ComplexType) -> Record:
        rec = Record(ct.name, ct.doc, ct.abstract)
        used: set[str] = set()

        def ident(base: str) -> str:
            name, n = base, 2
            while name in used:
                name = f"{base}_{n}"
                n += 1
            used.add(name)
            return name

        for a in ct.attributes:
            xml = a.name
            rec.attrs.append(
                AttrField(ident(snake(xml.replace("xml:", "xml_"))), xml, a.type, a.required, a.default, a.doc)
            )
        if ct.text is not None:
            rec.text = TextField(ident("value"), ct.text)
        if ct.content is None:
            return rec
        content = _normalize(ct.content)
        if not isinstance(content, Sequence) or content.min != 1 or content.max != 1:
            content = Sequence([content])
        nchoices = sum(isinstance(i, Choice) for i in content.items)
        cindex = 0
        for item in content.items:
            if isinstance(item, Element):
                card = cardinality(item.min, item.max)
                rec.content.append(
                    ElemField(
                        ident(snake(item.name)),
                        item.name,
                        item.type,
                        card,
                        item.min,
                        item.max,
                        doc=item.doc,
                        fixed=item.fixed,
                    )
                )
            elif isinstance(item, Choice):
                cindex += 1
                suffix = "" if nchoices == 1 else str(cindex)
                stem = ct.name[:-4] if ct.name.endswith("Type") else ct.name
                ch = ChoiceType(self.unique_type_name(f"{stem}Choice{suffix}"), ct.name, [])
                vused: set[str] = set()
                for v in item.items:
                    if not isinstance(v, Element) or v.min != 1 or v.max != 1:
                        raise BindingError(f"{ct.name}: choice alternatives must be single elements")
                    if v.name in vused:
                        raise BindingError(f"{ct.name}: duplicate choice alternative {v.name}")
                    vused.add(v.name)
                    ch.variants.append(Variant(
                            v.name[0].upper() + v.name[1:],
                            snake(v.name),
                            v.name,
                            v.type,
                            doc=v.doc,
                            fixed=v.fixed,
                        ))
                self.choices[ch.name] = ch
                card = cardinality(item.min, item.max)
                base = ("items" if card == MANY else "choice") + suffix
                rec.content.append(ChoiceField(ident(base), ch, card, item.min, item.max))
            else:
                raise BindingError(f"{ct.name}: nested sequences are not supported")
        _check_deterministic(rec)
        return rec


def _check_deterministic(rec: Record) -> None:
    """Verify one-element lookahead is enough to decode the content model."""
    # Decoding is greedy: an item with min < max keeps accepting its first
    # names, so no later item may start with one of them until a fixed-count
    # item has been read.
    open_names: set[str] = set()
    for f in rec.content:
        names = [f.xml] if isinstance(f, ElemField) else [v.xml for v in f.choice.variants]
        clash = open_names.intersection(names)
        if clash:
            raise BindingError(f"{rec.name}: ambiguous content model at {sorted(clash)}")
        if f.min == f.max:
            open_names = set()
        else:
            open_names.update(names)


# -- analysis: boxing, ordering and defaults ---------------------------------


def _edges(b: Binding, node: str) -> list[tuple[str, bool, object]]:
    """Outgoing (target, by_value, holder) edges of a record or choice."""
    out = []
    if node in b.records:
        for f in b.records[node].content:
            if isinstance(f, ElemField):
                if isinstance(f.type, ComplexRef):
                    out.append((f.type.name, f.card != MANY, f))
            else:
                out.append((f.choice.name, f.card != MANY, f))
    else:
        for v in b.choices[node].variants:
            if isinstance(v.type, ComplexRef):
                out.append((v.type.name, True, v))
    return out


def _analyze(b: Binding) -> None:
    nodes = sorted(b.records) + sorted(b.choices)
    # Tarjan's strongly connected components (iterative).
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comp: dict[str, int] = {}
    sccs: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(_edges(b, root)))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w, _, _ in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(_edges(b, w))))
                    advanced = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                members = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp[w] = len(sccs)
                    members.append(w)
                    if w == v:
                        break
                sccs.append(sorted(members))
    # Box by-value edges that stay inside a cycle.
    for n in nodes:
        for target, by_value, holder in _edges(b, n):
            if by_value and comp[target] == comp[n]:
                holder.boxed = True
    # Tarjan emits SCCs in reverse topological order: dependencies first.
    b.order = [m for scc in sccs for m in scc]
    _defaults(b)


def _defaults(b: Binding) -> None:
    """Pick, for each choice, the alternative with the smallest finite default."""
    inf = float("inf")
    cost = {n: inf for n in list(b.records) + list(b.choices)}

    def rec_cost(r: Record) -> float:
        total = 1.0
        for f in r.content:
            if f.min == 0:
                continue
            if isinstance(f, ElemField):
                if isinstance(f.type, ComplexRef):
                    total += f.min * cost[f.type.name]
            else:
                total += f.min * cost[f.choice.name]
        return total

    def var_cost(v: Variant) -> float:
        return cost[v.type.name] if isinstance(v.type, ComplexRef) else 1.0

    changed = True
    while changed:
        changed = False
        for name, r in b.records.items():
            c = rec_cost(r)
            if c < cost[name]:
                cost[name], changed = c, True
        for name, ch in b.choices.items():
            c = min(var_cost(v) for v in ch.variants)
            if c < cost[name]:
                cost[name], changed = c, True
    for name, ch in b.choices.items():
        costs = [var_cost(v) for v in ch.variants]
        ch.default_variant = costs.index(min(costs))
    bad = [n for n, c in cost.items() if c == inf]
    if bad:
        raise BindingError(f"types without a finite instance: {bad}")


def build(schema: Schema) -> Binding:
    return _Builder(schema).build()


def scalar_kind(b: Binding, t: TypeRef) -> Optional[str]:
    return t.kind if isinstance(t, ScalarRef) else None


def is_enum(t: TypeRef) -> bool:
    return isinstance(t, EnumRef)
