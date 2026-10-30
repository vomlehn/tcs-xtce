"""Parse the XTCE ``SpaceSystem.xsd`` into the language-neutral model.

Only the XSD constructs used by XTCE are supported; anything else raises
``XsdError`` so that a future schema change is noticed instead of silently
producing incomplete bindings.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional

from .model import (BUILTINS, STRING, UNBOUNDED, Attribute, Choice, ComplexRef,
                    ComplexType, Element, EnumRef, EnumType, Schema, ScalarRef,
                    Sequence, TypeRef)

XS = "{http://www.w3.org/2001/XMLSchema}"


class XsdError(Exception):
    pass


def _local(qname: str) -> tuple[Optional[str], str]:
    """Splits a qualified XML name.

    Args:
        qname: The name, with or without a prefix.

    Returns:
        The prefix, or None if there is none, and the local name.
    """
    if ":" in qname:
        prefix, name = qname.split(":", 1)
        return prefix, name
    return None, qname


def _doc(node: ET.Element) -> str:
    """Reads a schema node's documentation annotation.

    Args:
        node: The XSD element, such as an xs:complexType.

    Returns:
        The text of its xs:annotation/xs:documentation with runs of
        whitespace collapsed to single spaces, or ``""`` if it has none.
    """
    d = node.find(f"{XS}annotation/{XS}documentation")
    if d is None or d.text is None:
        return ""
    return re.sub(r"\s+", " ", d.text).strip()


def _occurs(node: ET.Element) -> tuple[int, int]:
    """Reads a particle's occurrence bounds.

    Args:
        node: The xs:element, xs:sequence or xs:choice.

    Returns:
        Its minOccurs and maxOccurs, each 1 by default; ``unbounded`` is
        UNBOUNDED.
    """
    lo = int(node.get("minOccurs", "1"))
    hi_s = node.get("maxOccurs", "1")
    hi = UNBOUNDED if hi_s == "unbounded" else int(hi_s)
    return lo, hi


def _strip_type(name: str) -> str:
    """``name`` without a ``Type`` suffix, unless that is all it is."""
    return name[:-4] if name.endswith("Type") and len(name) > 4 else name


class _Parser:
    def __init__(self, root: ET.Element):
        """Starts parsing a schema.

        Args:
            root: The schema's xs:schema element.
        """
        self.root = root
        self.ns = root.get("targetNamespace", "")
        self.schema = Schema(namespace=self.ns, version=root.get("version", ""))
        self.simple_nodes: dict[str, ET.Element] = {}
        self.complex_nodes: dict[str, ET.Element] = {}
        self.element_nodes: dict[str, ET.Element] = {}
        self.raw: dict[str, ComplexType] = {}  # before flattening

    # -- simple types -----------------------------------------------------

    def simple_ref(self, name: str) -> TypeRef:
        """Resolves a named simple type, an XSD builtin or one the schema
        defines, defining it first if need be.

        Args:
            name: The type's name, possibly with a prefix.

        Returns:
            An EnumRef for an enumeration, otherwise a ScalarRef.

        Raises:
            XsdError: If the schema doesn't define the type and it isn't a
                      supported builtin, or its definition is unsupported.
        """
        prefix, local = _local(name)
        if (prefix is None and local in BUILTINS
                and local not in self.simple_nodes):
            return ScalarRef(BUILTINS[local], builtin=local)
        if local in self.schema.enums:
            return EnumRef(local)
        if local in self.schema.simple:
            return self.schema.simple[local]
        node = self.simple_nodes.get(local)
        if node is None:
            if local in BUILTINS:
                return ScalarRef(BUILTINS[local], builtin=local)
            raise XsdError(f"unknown simple type {name}")
        return self.define_simple(local, node)

    def define_simple(self, name: str, node: ET.Element) -> TypeRef:
        """Defines a simple type: an enumeration, a restriction of another
        simple type, or a union or list, kept as a string.

        Args:
            name: The type's name, or the name made up for an anonymous one.
            node: Its xs:simpleType.

        Returns:
            An EnumRef for an enumeration, otherwise a ScalarRef.

        Raises:
            XsdError: If it is a restriction without a base or isn't a
                      restriction, union or list.
        """
        restriction = node.find(f"{XS}restriction")
        if restriction is not None:
            values = [e.get("value", "")
                      for e in restriction.findall(f"{XS}enumeration")]
            if values:
                self.schema.enums[name] = EnumType(name, values, _doc(node))
                return EnumRef(name)
            base = restriction.get("base")
            if base is None:
                raise XsdError(f"restriction without base in {name}")
            inner = self.simple_ref(base)
            if isinstance(inner, EnumRef):
                # A restriction of an enum without new values: same enum.
                return inner
            ref = ScalarRef(inner.kind, name, inner.builtin)
            self.schema.simple[name] = ref
            return ref
        if (node.find(f"{XS}union") is not None
                or node.find(f"{XS}list") is not None):
            # Unions mix enumerations and patterns; keep the lexical form.
            ref = ScalarRef(STRING, name)
            self.schema.simple[name] = ref
            return ref
        raise XsdError(f"unsupported simpleType {name}")

    def type_of(self, node: ET.Element, owner: str) -> TypeRef:
        """Gives the type of an xs:element or xs:attribute, named or
        anonymous; an anonymous type is defined under a made-up name.

        Args:
            node:  The xs:element or xs:attribute.
            owner: The name of the complex type it is in, which the name of an
                   anonymous type starts with.

        Returns:
            A reference to the type; a string for an untyped node.

        Raises:
            XsdError: If a simple type it uses can't be resolved.
        """
        t = node.get("type")
        if t is not None:
            _, local = _local(t)
            if local in self.complex_nodes:
                return ComplexRef(local)
            return self.simple_ref(t)
        name = node.get("name") or ""
        anon_name = _strip_type(owner) + name + "Type"
        st = node.find(f"{XS}simpleType")
        if st is not None:
            return self.define_simple(self.unique(anon_name), st)
        ct = node.find(f"{XS}complexType")
        if ct is not None:
            cname = self.unique(anon_name)
            self.raw[cname] = self.parse_complex(cname, ct, anonymous=True)
            return ComplexRef(cname)
        if node.tag == f"{XS}element":
            return ScalarRef(STRING)  # untyped element: anyType text
        return ScalarRef(STRING)

    def unique(self, name: str) -> str:
        """Makes a type name that no schema type or made-up one has.

        Args:
            name: The name wanted.

        Returns:
            ``name``, or ``name`` followed by the smallest number from 2 that
            makes it unique.
        """
        base, n = name, 2
        taken = lambda s: (  # noqa: E731
            s in self.complex_nodes or s in self.simple_nodes or s in self.raw
            or s in self.schema.enums or s in self.schema.simple)
        while taken(name):
            name = f"{base}{n}"
            n += 1
        return name

    # -- complex types ----------------------------------------------------

    def parse_attribute(self, node: ET.Element, owner: str) -> Attribute:
        """Parses an xs:attribute.

        Args:
            node:  The xs:attribute.
            owner: The name of the complex type it is in.

        Returns:
            The attribute; one referenced with ``ref`` (only xml:base is) is
            a string.

        Raises:
            XsdError: If it has neither a name nor a ref.
        """
        ref = node.get("ref")
        if ref is not None:
            # Only xml:base is referenced by XTCE.
            return Attribute(ref, ScalarRef(STRING),
                             node.get("use") == "required")
        name = node.get("name")
        if name is None:
            raise XsdError(f"attribute without name in {owner}")
        return Attribute(name=name, type=self.type_of(node, owner),
                         required=node.get("use") == "required",
                         default=node.get("default"), doc=_doc(node))

    def parse_particle(self, node: ET.Element, owner: str):
        """Parses a particle and, for a group, the particles in it.

        Args:
            node:  The xs:element, xs:sequence or xs:choice.
            owner: The name of the complex type it is in.

        Returns:
            An Element, Sequence or Choice.

        Raises:
            XsdError: If it is another kind of particle, or holds one.
        """
        lo, hi = _occurs(node)
        tag = node.tag
        if tag == f"{XS}element":
            ref = node.get("ref")
            if ref is not None:
                _, local = _local(ref)
                target = self.element_nodes[local]
                return Element(local, self.type_of(target, owner), lo, hi,
                               doc=_doc(node))
            return Element(node.get("name", ""), self.type_of(node, owner), lo,
                           hi, fixed=node.get("fixed"), doc=_doc(node))
        if tag in (f"{XS}sequence", f"{XS}choice"):
            items = [self.parse_particle(c, owner) for c in node
                     if c.tag not in (f"{XS}annotation",)]
            cls = Sequence if tag == f"{XS}sequence" else Choice
            return cls(items, lo, hi)
        raise XsdError(f"unsupported particle {tag} in {owner}")

    def parse_body(self, ct: ComplexType, node: ET.Element) -> None:
        """Parses the attributes and the content model directly under a node
        into a complex type.

        Args:
            ct:   The complex type, updated in place.
            node: The xs:complexType or xs:extension.

        Raises:
            XsdError: If the node holds xs:anyAttribute, xs:group or
                      xs:attributeGroup.
        """
        for child in node:
            if child.tag == f"{XS}attribute":
                ct.attributes.append(self.parse_attribute(child, ct.name))
            elif child.tag in (f"{XS}sequence", f"{XS}choice"):
                p = self.parse_particle(child, ct.name)
                ct.content = (p if isinstance(p, Sequence)
                              and p.min == p.max == 1 else Sequence([p]))
            elif child.tag == f"{XS}annotation":
                pass
            elif (child.tag in (f"{XS}anyAttribute", f"{XS}group",
                                f"{XS}attributeGroup")):
                raise XsdError(f"unsupported {child.tag} in {ct.name}")

    def parse_complex(self, name: str, node: ET.Element,
                      anonymous: bool = False) -> ComplexType:
        """Parses an xs:complexType, before inheritance is flattened.

        Args:
            name:      The type's name, or the name made up for an anonymous
                       one.
            node:      The xs:complexType.
            anonymous: Whether the type is anonymous.

        Returns:
            The complex type, with its base type's name if it extends one.

        Raises:
            XsdError: If it restricts complex or simple content, mixes text
                      with elements or uses an unsupported construct.
        """
        ct = ComplexType(name=name, abstract=node.get("abstract") == "true",
                         doc=_doc(node), anonymous=anonymous)
        cc = node.find(f"{XS}complexContent")
        sc = node.find(f"{XS}simpleContent")
        if cc is not None:
            ext = cc.find(f"{XS}extension")
            if ext is None:
                raise XsdError("complexContent restriction unsupported in "
                               f"{name}")
            _, ct.base = _local(ext.get("base", ""))
            self.parse_body(ct, ext)
        elif sc is not None:
            ext = sc.find(f"{XS}extension")
            if ext is None:
                raise XsdError("simpleContent restriction unsupported in "
                               f"{name}")
            base = self.simple_ref(ext.get("base", ""))
            if not isinstance(base, ScalarRef):
                base = ScalarRef(STRING)
            ct.text = base
            self.parse_body(ct, ext)
        else:
            self.parse_body(ct, node)
            if node.get("mixed") == "true":
                if ct.content is not None:
                    raise XsdError("mixed content with elements unsupported "
                                   f"in {name}")
                ct.text = ScalarRef(STRING)
        return ct

    # -- driver -----------------------------------------------------------

    def run(self) -> Schema:
        """Parses the whole schema: its simple types, complex types and root
        elements, then flattens inheritance.

        Returns:
            The schema model.

        Raises:
            XsdError: If the schema uses an unsupported construct or a root
                      element isn't of a complex type.
        """
        for node in self.root:
            name = node.get("name")
            if node.tag == f"{XS}simpleType":
                self.simple_nodes[name] = node
            elif node.tag == f"{XS}complexType":
                self.complex_nodes[name] = node
            elif node.tag == f"{XS}element":
                self.element_nodes[name] = node
            elif node.tag in (f"{XS}annotation", f"{XS}import"):
                pass
            else:
                raise XsdError(f"unsupported top-level {node.tag}")
        for name, node in self.simple_nodes.items():
            if name not in self.schema.enums and name not in self.schema.simple:
                self.define_simple(name, node)
        for name, node in self.complex_nodes.items():
            self.raw[name] = self.parse_complex(name, node)
        for name, node in self.element_nodes.items():
            t = self.type_of(node, name)
            if not isinstance(t, ComplexRef):
                raise XsdError(f"root element {name} must be complex")
            self.schema.roots[name] = t.name
        for name in sorted(self.raw):
            self.schema.complex[name] = self.flatten(name)
        return self.schema

    def flatten(self, name: str) -> ComplexType:
        """Flattens a complex type's inheritance: its base types' attributes
        and content come first.

        Args:
            name: The type's name.

        Returns:
            The type with every attribute and its full content model.
        """
        ct = self.raw[name]
        if ct.base is None:
            return ct
        base = self.flatten(ct.base)
        items = []
        if base.content is not None:
            items.extend(base.content.items)
        if ct.content is not None:
            items.extend(ct.content.items)
        return ComplexType(name=ct.name, abstract=ct.abstract, base=ct.base,
                           attributes=base.attributes + ct.attributes,
                           content=Sequence(items) if items else None,
                           text=ct.text or base.text, doc=ct.doc,
                           anonymous=ct.anonymous)


def parse(path: str) -> Schema:
    """Parses an XTCE schema.

    Args:
        path: The schema file, such as SpaceSystem.xsd.

    Returns:
        The schema model.

    Raises:
        XsdError: If the schema uses a construct that isn't supported.
    """
    return _Parser(ET.parse(path).getroot()).run()
