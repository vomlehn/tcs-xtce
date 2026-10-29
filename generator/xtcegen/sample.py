"""Generate schema-valid sample XTCE documents that exercise the bindings.

Samples are produced from the same binding model as the libraries.  Choice
alternatives and enumeration values are rotated across samples until every
alternative and every element of every type has been emitted at least once,
so round-tripping the samples covers the whole generated API.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from .binding import MANY, ONE, OPT, Binding, ChoiceField, ElemField, Record
from .model import ComplexRef, EnumRef, ScalarRef, TypeRef

MAX_SAMPLES = 64
EXPANSIONS_PER_TYPE = 2  # full expansions of a type per sample

PATTERN_VALUES = {"BinaryType": ["0b101"], "HexadecimalType": ["0x1F"],
                  "OctalType": ["0o17"],
                  "ByteOrderArbitraryType": ["0,1", "1,0"],
                  "ByteOrderType": ["mostSignificantByteFirst", "1,0"],
                  "EpochType": ["TAI", "2024-01-02", "1970-01-01T00:00:00Z"],
                  "FixedIntegerValueType": ["42", "0x2A", "0o52", "0b101010"]}

LEXICAL = {"dateTime": ["2024-01-02T03:04:05Z", "2024-06-30T23:59:59.5+01:00"],
           "date": ["2024-01-02"], "duration": ["PT1.5S", "P1DT2H"],
           "hexBinary": ["0A1B", "ff00"],
           "anyURI": ["http://example.com/xtce", "urn:x:y"],
           "boolean": ["true", "false"], "int": ["7", "-3"],
           "long": ["42", "-9000000000"], "integer": ["17"],
           "unsignedShort": ["65535", "8"],
           "double": ["1.5", "0.25", "42.0", "1e-05", "2.5e+20"]}

# Sets whose children are all selected by an xs:key on @name ("Set/*"), so
# children without a name attribute make an instance invalid.
KEYED_SETS = {"MessageSetType"}

# Plain strings, including characters that need escaping.
STRINGS = ["text", 'a < b & "c" > \'d\'', "multi\nline", "üñïçødé"]


class _Gen:
    def __init__(self, b: Binding, seed: int, done: set[tuple[str, str]]):
        """Starts one sample document.

        Args:
            b:    The binding.
            seed: The sample's number, which rotates the values and
                  alternatives it picks.
            done: The (type, element) slots earlier samples covered.
        """
        self.b = b
        self.done = done
        self.seed = seed
        self.counter = 0
        self.choice_turn: dict[str, int] = {}
        self.enum_turn: dict[str, int] = {}
        self.expanded: dict[str, int] = {}
        self.covered: set[tuple[str, str]] = set()
        self.out: list[str] = []

    def next(self) -> int:
        """Counts one more value, so that successive values differ."""
        self.counter += 1
        return self.counter

    def scalar(self, t: TypeRef, owner: str, name: str) -> str:
        """Picks a schema-valid value for an attribute, a simple element or
        text content.

        Args:
            t:     The value's simple type.
            owner: The name of the type that holds it.
            name:  The attribute's or element's name; names and references
                   get identifier-like values.

        Returns:
            The value's lexical form, unescaped: an enumeration's values in
            turn, values that match a pattern type, small integers and
            doubles for restricted types, or rotating plain strings.
        """
        if isinstance(t, EnumRef):
            values = self.b.schema.enums[t.name].values
            k = self.enum_turn.get(t.name, self.seed)
            self.enum_turn[t.name] = k + 1
            return values[k % len(values)]
        assert isinstance(t, ScalarRef)
        n = self.next()
        sn = t.simple_name or ""
        if sn in PATTERN_VALUES:
            vals = PATTERN_VALUES[sn]
            return vals[(n + self.seed) % len(vals)]
        if ("Name" in sn
                or name in ("name", "ref", "parameterRef", "argumentRef")):
            return f"n{n}"
        if sn and t.builtin in ("int", "long", "integer"):
            return str(n % 1000 + 1)  # restricted (e.g. positive) integers
        if sn and t.builtin == "double":
            return ["1.5", "42.0", "0.25"][n % 3]  # restricted (e.g. percent)
        if t.builtin in LEXICAL:
            vals = LEXICAL[t.builtin]
            return vals[(n + self.seed) % len(vals)]
        return STRINGS[(n + self.seed) % len(STRINGS)] + str(n)

    def element(self, tag: str, t: TypeRef, path: tuple[str, ...],
                minimal: bool, fixed=None) -> None:
        """Appends an element to the sample.

        Args:
            tag:     The element's name.
            t:       Its type.
            path:    The names of the types it is nested in, to stop
                     recursion.
            minimal: Whether to write only what the schema requires.
            fixed:   The element's fixed value, if the schema gives one.
        """
        if isinstance(t, ComplexRef):
            self.record(self.b.records[t.name], tag, path, minimal)
        else:
            value = (fixed if fixed is not None
                     else self.scalar(t, path[-1], tag))
            self.out.append(f"<{tag}>{escape(value)}</{tag}>")

    def record(self, r: Record, tag: str, path: tuple[str, ...], minimal: bool,
               root=False) -> None:
        """Appends an element of a complex type to the sample, with its
        attributes and content.

        A type already on the path, or already expanded EXPANSIONS_PER_TYPE
        times, is written minimally.

        Args:
            r:       The type's record.
            tag:     The element's name.
            path:    The names of the types it is nested in.
            minimal: Whether to write only what the schema requires.
            root:    Whether it is the document root, which declares the
                     namespace.
        """
        if not minimal:
            seen = self.expanded.get(r.name, 0)
            if r.name in path or seen >= EXPANSIONS_PER_TYPE:
                minimal = True
            else:
                self.expanded[r.name] = seen + 1
        path = path + (r.name,)
        attrs = []
        if root:
            attrs.append(f'xmlns="{self.b.schema.namespace}"')
        for a in r.attrs:
            if a.required or not minimal:
                attrs.append(f"{a.xml}="
                             f"{quoteattr(self.scalar(a.type, r.name, a.xml))}")
        head = f"<{tag}" + "".join(" " + a for a in attrs)
        if r.text is not None:
            value = ("m/s" if r.name == "UnitType"
                     else self.scalar(r.text.type, r.name, "value"))
            self.out.append(f"{head}>{escape(value)}</{tag}>")
            return
        start = len(self.out)
        self.out.append(head + ">")
        for f in r.content:
            count = f.min if minimal else max(f.min, 1)
            if r.name in KEYED_SETS and f.max == 1:
                count = f.min  # every child must carry the key's @name
            if f.card == MANY and not minimal and f.max != 1:
                count = max(count, min(2, f.max) if f.max > 0 else 2)
            if count == 0:
                continue
            for _ in range(count):
                if isinstance(f, ElemField):
                    self.covered.add((r.name, f.xml))
                    self.element(f.xml, f.type, path, minimal, f.fixed)
                else:
                    self.choice(f, path, minimal)
        if len(self.out) == start + 1:
            self.out[start] = head + "/>"  # no whitespace in empty content
        else:
            self.out.append(f"</{tag}>")

    def choice(self, f: ChoiceField, path: tuple[str, ...],
               minimal: bool) -> None:
        """Appends one alternative of a choice: the default one when minimal,
        otherwise one no sample has covered yet, or the next in turn.

        Args:
            f:       The choice field.
            path:    The names of the types it is nested in.
            minimal: Whether to write only what the schema requires.
        """
        c = f.choice
        if minimal:
            v = c.variants[c.default_variant]
        else:
            # Prefer alternatives no earlier sample has covered yet.
            fresh = [v for v in c.variants
                     if (c.name, v.xml) not in self.done | self.covered]
            if fresh:
                v = fresh[0]
            else:
                k = self.choice_turn.get(c.name, self.seed)
                self.choice_turn[c.name] = k + 1
                v = c.variants[k % len(c.variants)]
        self.covered.add((c.name, v.xml))
        self.element(v.xml, v.type, path, minimal, v.fixed)


def _all_targets(b: Binding, root: str) -> set[tuple[str, str]]:
    """Finds every element slot of every type reachable from the root.

    Args:
        b:    The binding.
        root: The name of the root element's type.

    Returns:
        A (type, element name) pair for each element of each record and
        each alternative of each choice, except the single children of
        keyed sets.
    """
    targets: set[tuple[str, str]] = set()
    seen: set[str] = set()
    todo = [root]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        if n in b.records:
            for f in b.records[n].content:
                if isinstance(f, ElemField):
                    if not (n in KEYED_SETS and f.max == 1):
                        targets.add((n, f.xml))
                    if isinstance(f.type, ComplexRef):
                        todo.append(f.type.name)
                else:
                    todo.append(f.choice.name)
        else:
            for v in b.choices[n].variants:
                targets.add((n, v.xml))
                if isinstance(v.type, ComplexRef):
                    todo.append(v.type.name)
    return targets


def generate(b: Binding) -> list[tuple[str, str]]:
    """Makes sample documents that together cover the schema.

    Args:
        b: The binding.

    Returns:
        ``(file name, xml text)`` pairs: up to MAX_SAMPLES samples, each
        covering something earlier ones didn't, then COVERAGE.txt, which
        reports what they cover.
    """
    root_tag, root_type = next(iter(b.schema.roots.items()))
    targets = _all_targets(b, root_type)
    covered: set[tuple[str, str]] = set()
    samples = []
    for seed in range(MAX_SAMPLES):
        g = _Gen(b, seed, covered)
        g.record(b.records[root_type], root_tag, (), False, root=True)
        new = g.covered - covered
        if not new and seed > 0:
            continue
        covered |= g.covered
        text = ('<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(g.out)
                + "\n")
        samples.append((f"sample_{len(samples):02d}.xml", text))
        if covered >= targets:
            break
    missing = sorted(targets - covered)
    report = [f"samples: {len(samples)}",
              f"covered: {len(covered)}/{len(targets)}"]
    report += [f"uncovered: {t}.{e}" for t, e in missing]
    samples.append(("COVERAGE.txt", "\n".join(report) + "\n"))
    return samples
