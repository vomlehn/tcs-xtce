================
tcs-xtcee Design
================

How ``tcs-xtcee``, the XTCE editor, works inside, and why. How to use it is
in the `tcs-xtcee manual <tcs-xtcee.html>`__.

The editor
==========

The editor is a Rust program in ``editor/``, its windows in Slint, as
the TCS ground programs' are.  Its parts, each a module of the
``tcs-xtcee`` crate:

``tree``
   The document as it was written.  The XML binding reads a document into
   typed structures and writes it in its own hand, losing comments and
   layout; an editor that did that would turn a one-attribute change into
   a rewrite of the file.  So the editor keeps every piece of the text --
   the whitespace inside a tag, each attribute's quote character, the
   character references in a value, the text between elements -- and
   writes it back byte for byte.  A comment is a node among its siblings,
   so it stays where it was put.  Elements and comments carry ids that
   do not change while the document is open, so that an edit can name
   what it changes without depending on where it is.

``schema`` and ``content``
   Each version's schema, read from the file OMG published, which the
   program does not carry, OMG not allowing it to be redistributed: it is
   read the first time a document of its version is opened, made or
   converted to, from ``references/`` in a checkout or the user's data
   directory, where ``fetch`` downloads it once the person agrees.  What
   is read is element and attribute declarations, complex types with
   their bases merged in, simple types with their enumerations, patterns
   and ranges, and keys.  The part of XML Schema the XTCE schemas use is
   read, and anything else is refused, so that a schema using more of the
   language cannot be held to less than it says.  A complex type's
   content is an automaton over element names, which answers three
   questions: whether a list of children is allowed, and which
   declaration each child is of; which elements may go in at a place; and
   the shortest list that completes an element.

``validate``
   A document against its version's schema, the version chosen by the
   root's namespace and no other.  It agrees with ``xmllint`` on every
   shipped document and on random documents of every version and their
   conversions (``make editor-check``); two quirks of the schemas are
   followed as a validator follows them: 1.2's Derivation is of an
   abstract type, so no 1.2 document may hold one, and 1.2's message key
   takes in a MessageSet's LongDescription, which has no name.

``edit`` and ``session``
   Every change is one of three ops -- replacing some of an element's
   children, setting an attribute, renaming an element -- each holding
   what it replaces as well as what it puts, so its inverse is read off
   rather than worked out again.  The session makes a change only if it
   gives no element or attribute an error it did not have and adds no
   error; otherwise it undoes the ops it made and says why.  The comments
   written just before an element, with no blank line between, belong to
   it: they go where it goes and are taken out with it.  The undo
   histories are in memory only, as asked: the file is what lasts.

``refs``
   The references between definitions, resolved by the compiler's rule
   (``resolve`` in ``compile.py``), so the editor and the compiler agree
   about what a mission means; its tests hold it to the shipped missions,
   which the compiler builds.

``convert``
   A version at a time.  The rules were made by comparing the three
   schemas element by element and attribute by attribute -- 1,330
   differences between 1.1 and 1.2, 377 between 1.2 and 1.3, most of
   them type names that never appear in a document -- and reading the
   schemas' own annotations, the only place the changes are explained:
   the specifications have no history of changes.  A rename or a rewrite
   the schemas show is a rule; what the older version cannot say is
   found by checking the result against its schema and taken out, and
   reported as lost; an element the older version requires and that may
   be empty is put in; a value neither document holds is asked for.  The
   rest is refused, saying why.

``codec`` and ``tools``
   The packet layout, encoding and decoding, and the compiler's check
   are the compiler's: the editor runs it, from Python, rather than
   working out a field's size again, which would be a second statement of
   the same rules.  The digest is the ``tcs-xtce`` crate's.

``make``
   New elements made complete, and random valid documents of any version
   for the tests, from the content automata.

A document opened in an older version is converted to 1.3 only in memory
when the compiler is asked about it.  The editor never changes a
document's version except when asked to convert it.

See Also
========

`The tcs-xtce design documents <design.html>`__
    What each of tcs-xtce's design documents covers.

`The tcs-xtce design <tcs-xtce-design.html>`__
    The code generator and the runtimes, whose Python package the editor's
    packet layout, encode and decode window and check use.

`The tcs-xtcee manual <tcs-xtcee.html>`__
    Using the editor.
