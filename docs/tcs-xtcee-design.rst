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

``transfer``
   Deleting, moving and copying several rows at once.  A window has a
   selected row, the one Insert, Paste and the other one-row items act
   on, and the rows selected together, which Cut, Copy, Delete, Move and
   a drag act on; a click makes the two the same row.  What is acted on
   is the elements and comments among the rows selected together, less
   any inside another of them, in document order, each element taking
   the comments that belong to it.  Each act is one change, its ops made
   one after another on a copy of each document it changes, so that each
   op is made for the document as the ops before it left it.

   Move up and Move down move a run of sibling elements next to each
   other by moving the element on the other side of the run round it,
   which leaves the run's ids, and so its selection, as they were; rows
   that are not such a run cannot be moved up or down.

   Rows are copied as XTCE text: the elements as written, each given the
   ``xmlns`` declarations, from around it, of the prefixes it uses and
   does not declare, so that the text means the same wherever it goes.
   Text is pasted in the document's namespace prefix, with the
   declarations the place already has, or no longer needs, taken out.
   Text that declares no namespace is taken to be in the document's:
   that is what a piece of a file copied in a text editor is, and the
   schema check that every change gets judges the result.  Text in
   another XTCE version's namespace is refused rather than converted
   silently.  Whether the clipboard's first element may go before,
   after or in the selected row is asked as Insert asks it, of the
   content automaton at that place; that greys the Paste items, and the
   paste itself is checked whole, as every change is.

   XTCE 1.3's schema keys give each parameter, type, container and
   command of a SpaceSystem a name of its own, so a copy pasted beside
   what it copies is refused, as any change that adds a schema error is.
   Two definitions of one name that the schema allows, such as a
   telemetry and a command parameter, are pasted, and the duplicate-name
   warning ``refs`` gives marks the copy; the editor does not rename
   anything itself, since a new name would also have to be given to
   references in what is pasted.

   A move -- a cut then a paste, or a drag -- takes the rows out and puts
   them in as one change, in one document or two.  In one document they
   keep their ids; into another they go by way of the copy's text, which
   puts them in its prefix, and are given its ids.  Rows cut and then
   pasted are a move only while the cut is still the last change to
   their document and the clipboard still holds their text: the paste
   then undoes the cut and makes the move in its place, so that the
   history holds one step, named "move ... before, after or into ...".
   Otherwise a paste is a copy.

   A move keeps references whole.  Every reference that resolved before
   the move is resolved again, by the compiler's rule, in the documents
   as the move leaves them, with what moved followed to its new place;
   one that would name something else, or nothing, is written again in
   the same change, as Rename everywhere writes references: by the
   definition's name alone if that finds it from where the reference is,
   or else by its path from the root, an absolute reference staying
   absolute, any member part kept.  This covers references in every open
   document and in what moved, and a definition moved in front of
   another of the same name, which would otherwise take its references.
   A delete that would leave a reference outside what is deleted naming
   nothing asks first.

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

The windows
===========

The windows are Slint, their behaviour in ``gui``.  The clipboard is the
desktop's, through the ``arboard`` crate, which Slint's own text fields
use; the editor reads it once a second, so that text another program put
there decides which Paste items are offered.  The tests and the
screenshots use a clipboard kept in memory and never touch the desktop's.
Ctrl+X, Ctrl+C and Ctrl+V are keys of the outline, given the keyboard
when a row is pressed on, and not shortcuts of the menu: Slint gives a
menu's shortcuts the keys before a text field, which would take them
from fields being edited.

A drag by a row's grip is followed in the window, which works out from
the pointer's height in a row whether the rows would go before it, after
it or in it; the editor is asked what that would do, by the same check a
paste makes, and the answer is shown beside the pointer and decides
whether a line, an arrowhead and the words, or a cross and the reason,
are drawn.  A drop is made by the code a paste of cut rows uses.

See Also
========

`The tcs-xtce design documents <design.html>`__
    What each of tcs-xtce's design documents covers.

`The tcs-xtce design <tcs-xtce-design.html>`__
    The code generator and the runtimes, whose Python package the editor's
    packet layout, encode and decode window and check use.

`The tcs-xtcee manual <tcs-xtcee.html>`__
    Using the editor.
