"""Lay out Python statements in the compact aligned style.

Continuation lines line up under the bracket they continue, items are
packed, and closers stay at the end of the last line.  A bracket whose
contents would need more lines aligned than hung, or that holds comments,
uses a compact hanging indent instead.  Long values after ``=`` or
``return``, and long ``if`` conditions, are put in parentheses so that they
can break, and long string literals are split at spaces and tags.

``align`` applies this to every statement that spans several lines.  It is
how the generators lay out the Python they write, after ``wrap`` has
broken lines that are too long, and it matches the style of the
hand-written Python.

The layout engine is a pretty-printer in the style of Wadler's "prettier
printer", with Prettier's ``fill`` and two additions: ``align``, which
indents to the current column, and ``choose``, which tries an aligned and
a hanging layout and keeps the better.
"""

import argparse
import ast
import io
import keyword
import re
import sys
import tokenize

WIDTH = 80
OPEN, CLOSE = "([{", ")]}"
FLAT, BREAK = 0, 1

# -- documents -----------------------------------------------------------------
# str | ("cat", [d]) | ("line", flat, pre, post) | ("hard",) | ("group", d)
# | ("align", d) | ("nest", n, d) | ("fill", [content, sep, content, ...])
# | ("choose", aligned, hanging)

HARD = ("hard",)


def line(flat=" ", pre="", post=""):
    """A place to break: ``flat`` when the line doesn't break, otherwise
    ``pre``, a newline, the indentation and ``post``."""
    return ("line", flat, pre, post)


def cat(*ds):
    return ("cat", [d for d in ds if d != ""])


def fits(items, rest, rem):
    """Whether ``items`` (indent, mode, document) laid out from here, then
    what follows on the stack ``rest`` up to its next break, take at most
    ``rem`` columns."""
    items = list(items)
    ri = len(rest) - 1
    while rem >= 0:
        if not items:
            if ri < 0:
                return True
            items.append(rest[ri])
            ri -= 1
        ind, mode, d = items.pop()
        if isinstance(d, str):
            if "\n" in d:
                # A triple-quoted string's lines end where they end.
                if rem < len(d.split("\n")[0]):
                    return False
                rem = WIDTH - len(d.rsplit("\n", 1)[1])
                continue
            rem -= len(d)
            continue
        k = d[0]
        if k in ("cat", "fill", "fillrest"):
            items.extend((ind, mode, x) for x in reversed(d[1]))
        elif k == "line":
            if mode == BREAK:
                return True
            rem -= len(d[1])
        elif k == "hard":
            return mode == BREAK
        elif k in ("group", "align"):
            items.append((ind, mode, d[1]))
        elif k == "choose":
            items.append((ind, mode, d[2] if d[1] is None else d[1]))
        elif k == "prefer":
            items.append((ind, mode, d[1]))
        elif k in ("nest", "alignplus"):
            items.append((ind, mode, d[2]))
    return False


def render(doc, col, ind):
    """The text of ``doc``, starting at column ``col`` with indentation
    ``ind``."""
    out = []
    stack = [(ind, BREAK, doc)]
    while stack:
        ind, mode, d = stack.pop()
        if isinstance(d, str):
            out.append(d)
            col = len(d.rsplit("\n", 1)[1]) if "\n" in d else col + len(d)
            continue
        k = d[0]
        if k == "cat":
            stack.extend((ind, mode, x) for x in reversed(d[1]))
        elif k == "line":
            if mode == FLAT:
                out.append(d[1])
                col += len(d[1])
            else:
                out.append(d[2] + "\n" + " " * ind + d[3])
                col = ind + len(d[3])
        elif k == "hard":
            out.append("\n" + " " * ind)
            col = ind
        elif k == "group":
            if mode == FLAT or fits([(ind, FLAT, d[1])], stack, WIDTH - col):
                stack.append((ind, FLAT, d[1]))
            else:
                stack.append((ind, BREAK, d[1]))
        elif k == "align":
            stack.append((col, mode, d[1]))
        elif k == "alignplus":
            stack.append((col + d[1], mode, d[2]))
        elif k == "nest":
            stack.append((ind + d[1], mode, d[2]))
        elif k == "fill":
            parts = d[1]
            if mode == FLAT:
                stack.extend((ind, FLAT, x) for x in reversed(parts))
                continue
            last = len(parts) == 1
            cf = fits([(ind, FLAT, cat(parts[0], closing(parts, 1)))],
                      stack if last else [], WIDTH - col)
            if not last:
                stack.append((ind, BREAK, ("fillrest", parts[1:])))
            stack.append((ind, FLAT if cf else BREAK, parts[0]))
        elif k == "fillrest":
            # Decided after the content before it is placed, so it knows
            # where that content ended.
            parts = d[1]
            sep, second = parts[0], parts[1]
            last = len(parts) == 2
            rest = stack if last else []
            close = closing(parts, 2)
            if sep[0] != "hard" and fits([(ind, FLAT, cat(sep, second, close))],
                                         rest, WIDTH - col):
                sm = cm = FLAT
            else:
                sm = BREAK
                post = len(sep[3]) if sep[0] == "line" else 0
                cm = FLAT if fits([(ind, FLAT, cat(second, close))], rest,
                                  WIDTH - ind - post) else BREAK
            if not last:
                stack.append((ind, BREAK, ("fillrest", parts[2:])))
            stack.append((ind, cm, second))
            stack.append((ind, sm, sep))
        elif k == "prefer":
            if mode == FLAT:
                stack.append((ind, FLAT, d[1]))
            else:
                tail = tail_len(stack)
                wa = measure(d[1], col, ind, tail)[1]
                wb = measure(d[2], col, ind, tail)[1]
                use = d[1] if wa <= WIDTH or wa <= wb else d[2]
                stack.append((ind, BREAK, use))
        elif k == "choose":
            if mode == FLAT:
                stack.append((ind, FLAT, d[2] if d[1] is None else d[1]))
            else:
                stack.append((ind, BREAK, pick(d, col, ind, stack)))
    return "".join(out)


def tail_len(stack):
    """How far what follows on the stack runs before it can break."""
    n, items, ri = 0, [], len(stack) - 1
    while True:
        if not items:
            if ri < 0:
                return n
            items.append(stack[ri])
            ri -= 1
        ind, mode, d = items.pop()
        if isinstance(d, str):
            if "\n" in d:
                return n + len(d.split("\n")[0])
            n += len(d)
            continue
        k = d[0]
        if k in ("cat", "fill", "fillrest"):
            items.extend((ind, mode, x) for x in reversed(d[1]))
        elif k == "line":
            if mode == BREAK:
                return n + len(d[2])
            n += len(d[1])
        elif k == "hard":
            return n
        elif k in ("group", "align"):
            items.append((ind, mode, d[1]))
        elif k in ("nest", "alignplus"):
            items.append((ind, mode, d[2]))
        elif k == "choose":
            items.append((ind, mode, d[2] if d[1] is None else d[1]))
        elif k == "prefer":
            items.append((ind, mode, d[1]))


def closing(parts, i):
    """What a break at parts[i] leaves at the end of the line before it."""
    if i < len(parts) and parts[i][0] == "line":
        return parts[i][2]
    return ""


def measure(doc, col, ind, tail):
    """The lines, the widest line and the hanging breaks of ``doc`` laid out
    from column ``col``, followed by ``tail`` columns that can't break."""
    text = " " * col + render(cat(doc, " " * tail), col, ind)
    lines = text.split("\n")
    # Lines that end by opening a bracket: hanging breaks.
    hangs = sum(1 for x in lines[:-1] if x.rstrip()[-1:] in OPEN)
    # A first continuation line that starts right of a later one: the
    # first break is inside an inner bracket, a staircase.
    cols = [len(x) - len(x.lstrip(" ")) for x in lines[1:]]
    deep = 1 if len(cols) > 1 and cols[0] > min(cols[1:]) else 0
    return len(lines), max(len(x) for x in lines), hangs, deep


def pick(d, col, ind, stack):
    """The layout of a ``choose`` to use: aligned, unless hanging fits where
    aligned doesn't or takes fewer lines without a staircase."""
    al, hg = d[1], d[2]
    if al is None:
        return hg
    tail = tail_len(stack)
    na, wa, ha, da = measure(al, col, ind, tail)
    nh, wh, hh, _ = measure(hg, col, ind, tail)
    a_ok, h_ok = wa <= WIDTH, wh <= WIDTH
    # A hanging break nested in an aligned layout makes a staircase, and a
    # first break inside an inner bracket a smaller one.  The hanging
    # layout's own break doesn't count against it, and its lines step back
    # to its indent by design.
    a_score = na + 2 * ha + da
    h_score = nh + 2 * (hh - 1)
    if a_ok and (not h_ok or a_score <= h_score):
        return al
    if h_ok:
        return hg
    return al if wa <= wh else hg


# -- tokens --------------------------------------------------------------------


class Atom:
    """A token, or a whole f-string, with where it was."""

    def __init__(self, kind, text, row, col, erow, ecol):
        self.kind, self.text = kind, text
        self.row, self.col, self.erow, self.ecol = row, col, erow, ecol

    def __repr__(self):
        return f"Atom({self.kind}, {self.text!r})"


def atoms_of(src):
    """The tokens of ``src``, with each f-string as one STRING atom."""
    lines = src.splitlines(keepends=True)
    offs = [0]
    for ln in lines:
        offs.append(offs[-1] + len(ln))

    def sl(a, b):
        return src[offs[a[0] - 1] + a[1]:offs[b[0] - 1] + b[1]]

    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    FS = getattr(tokenize, "FSTRING_START", -1)
    FE = getattr(tokenize, "FSTRING_END", -1)
    atoms, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t.type == FS:
            nest, j = 0, i
            while True:
                if toks[j].type == FS:
                    nest += 1
                elif toks[j].type == FE:
                    nest -= 1
                    if nest == 0:
                        break
                j += 1
            e = toks[j]
            atoms.append(Atom("STRING", sl(t.start, e.end), *t.start, *e.end))
            i = j + 1
            continue
        atoms.append(Atom(tokenize.tok_name[t.type], t.string, *t.start,
                          *t.end))
        i += 1
    return atoms


# -- strings -------------------------------------------------------------------


def parse_str(text):
    """(prefix, quote, body) of a string literal; None if triple-quoted."""
    i = 0
    while text[i] not in "\"'":
        i += 1
    prefix, quote = text[:i], text[i]
    if text[i:i + 3] in ('"""', "'''"):
        return None
    return prefix, quote, text[i + 1:-1]


def fields(body):
    """Whether an f-string body has replacement fields."""
    return "{" in body.replace("{{", "").replace("}}", "")


def requote(text, quote):
    """The literal with the other quote character, or None."""
    p = parse_str(text)
    if p is None:
        return None
    pre, q, body = p
    if q == quote:
        return text
    if quote in body or "\\" + q in body:
        return None
    return pre + quote + body + quote


def newline_end(text):
    """Whether a literal ends with a newline escape."""
    p = parse_str(text)
    if p is None or "r" in p[0].lower():
        return False
    m = re.search(r"(\\*)n$", p[2])
    return bool(m and len(m.group(1)) % 2)


def mergeable(a, b):
    """Whether literal ``b`` can join literal ``a`` as one literal."""
    pa, pb = parse_str(a), parse_str(b)
    if pa is None or pb is None:
        return False
    fa = pa[0].lower().replace("f", "")
    fb = pb[0].lower().replace("f", "")
    if fa != fb or "b" in fa or "r" in fa or requote(b, pa[1]) is None:
        return False
    # A piece ending in a newline escape ends a line of text on purpose.
    m = re.search(r"(\\*)n$", pa[2])
    return not (m and len(m.group(1)) % 2)


def quotes_for(text):
    return {q for q in "\"'" if requote(text, q) is not None}


def merge(pieces):
    qs = set("\"'")
    for p in pieces:
        qs &= quotes_for(p)
    quote = '"' if '"' in qs else "'"
    pieces = [requote(p, quote) for p in pieces]
    fs = any("f" in parse_str(p)[0].lower() for p in pieces)
    pre = parse_str(pieces[0])[0].lower().replace("f", "")
    body = ""
    for p in pieces:
        pp, _, b = parse_str(p)
        if fs and "f" not in pp.lower():
            b = b.replace("{", "{{").replace("}", "}}")
        body += b
    return (pre + ("f" if fs else "")), quote, body


def cut_ok(body, k, raw, is_f):
    """Whether a literal's body can be split before index ``k``: not inside
    an escape sequence or an f-string's replacement field."""
    if k <= 0 or k >= len(body):
        return False
    j, slashes = k - 1, 0
    while j >= 0 and body[j] == "\\":
        slashes += 1
        j -= 1
    if slashes and (raw or slashes % 2):
        return False
    tail = body[max(0, k - 10):k]
    if (re.search(r"\\(x[0-9a-fA-F]{0,1}|u[0-9a-fA-F]{0,3}|U[0-9a-fA-F]{0,7}"
                  r"|N\{[^}]*)$", tail)):
        return False
    if is_f:
        depth, i = 0, 0
        while i < k:
            if depth == 0 and body.startswith(("{{", "}}"), i):
                i += 2
                continue
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        if depth or i > k:
            return False
    return True


def string_breaks(pre, quote, body):
    """A literal that breaks into adjacent literals at word and tag
    boundaries.  Every piece keeps the prefix; f prefixes that pieces don't
    need are dropped afterwards (see drop_needless_f)."""
    raw, is_f = "r" in pre, "f" in pre
    good = [k for k in range(1, len(body))
            if ((body[k - 1] == " " and body[k] != " ") or body[k] == "<"
                or body[k - 1] == "|") and cut_ok(body, k, raw, is_f)]
    more = sorted(set(good)
                  | {k for k in range(1, len(body)) if is_f
                     and (body[k] == "{" or body[k - 1] == "}")
                     and cut_ok(body, k, raw, is_f)})
    a = pieces_doc(pre, quote, body, good)
    if more == good:
        return a
    # Breaking at a replacement field reads worse than at a space.
    return ("prefer", a, pieces_doc(pre, quote, body, more))


def pieces_doc(pre, quote, body, cuts):
    bounds = [0] + cuts + [len(body)]
    chunks = [body[a:b] for a, b in zip(bounds, bounds[1:])]
    parts = []
    for i, c in enumerate(chunks):
        if i:
            parts.append(line("", quote, pre + quote))
        if i == 0:
            c = pre + quote + c
        if i == len(chunks) - 1:
            c += quote
        parts.append(c)
    return parts[0] if len(parts) == 1 else ("fill", parts)


def drop_needless_f(text):
    """In each run of adjacent literals that is an f-string, drop the f from
    pieces without fields.  The run's value and AST are unchanged."""
    atoms = [a for a in atoms_of(text) if a.kind not in ("NL", "COMMENT")]
    runs, cur = [], []
    for a in atoms:
        if a.kind == "STRING" and parse_str(a.text) is not None:
            cur.append(a)
        else:
            if len(cur) > 1:
                runs.append(cur)
            cur = []
    lines = text.split("\n")
    edits = []
    for run in runs:
        ps = [parse_str(a.text) for a in run]
        if not any("f" in p[0].lower() and fields(p[2]) for p in ps):
            continue
        for a, (pre, q, body) in zip(run, ps):
            if "f" in pre.lower() and not fields(body):
                body = body.replace("{{", "{").replace("}}", "}")
                pre = pre.replace("f", "").replace("F", "")
                edits.append((a, pre + q + body + q))
    for a, new in sorted(edits, key=lambda e: (e[0].row, -e[0].col)):
        ln = lines[a.row - 1]
        lines[a.row - 1] = ln[:a.col] + new + ln[a.ecol:]
    return "\n".join(lines)


# -- statements ----------------------------------------------------------------

BREAK_BEFORE = {"and", "or", "if", "else", "for"}
BINOPS = {"+", "-", "*", "/", "//", "%", "|", "&"}
COMPOUND = {"def", "class", "if", "elif", "while", "for", "with", "async",
            "except"}


class Group:
    """A bracket and the elements between its commas."""

    def __init__(self, open_atom):
        self.open = open_atom
        self.elems = [[]]
        self.close = None
        self.trailing = False
        self.call = False
        self.binding = None  # inside "for ... in" or "lambda ...:"
        self.open_comment = None


def is_comment(p):
    return not isinstance(p, Group) and p.kind == "COMMENT"


def tree_of(atoms):
    """Nest atoms into Groups; returns the top-level list of parts."""
    top = []
    stack = [top]
    groups = []
    prev = None
    for a in atoms:
        if a.kind == "OP" and a.text in OPEN:
            g = Group(a)
            stack[-1].append(g)
            groups.append(g)
            stack.append(g.elems[-1])
        elif a.kind == "OP" and a.text in CLOSE:
            g = groups.pop()
            stack.pop()
            if not g.elems[-1]:
                g.elems.pop()
                if g.elems:
                    g.trailing = True
            g.close = a
        elif (groups and stack[-1] is groups[-1].elems[-1]
              and ((a.kind == "NAME" and a.text in ("for", "lambda", "in"))
                   or (a.kind == "OP" and a.text == ":"))):
            g = groups[-1]
            if a.text in ("for", "lambda"):
                g.binding = a.text
            elif (a.text, g.binding) in (("in", "for"), (":", "lambda")):
                g.binding = None
            stack[-1].append(a)
        elif (a.kind == "OP" and a.text == "," and groups
              and groups[-1].binding is None):
            g = groups[-1]
            stack.pop()
            g.elems.append([])
            stack.append(g.elems[-1])
        elif (a.kind == "COMMENT" and groups and groups[-1].open_comment is None
              and (a.row == groups[-1].open.row
                   or (not stack[-1] and len(groups[-1].elems) == 1
                       and a.text.startswith("# noqa")))):
            # A comment on the opener's line stays on it.
            groups[-1].open_comment = a
        elif (a.kind == "COMMENT" and groups and not stack[-1]
              and prev.text == "," and prev.row == a.row):
            # A comment after a comma belongs to the element before it.
            groups[-1].elems[-2].append(a)
        else:
            stack[-1].append(a)
        prev = a
    return top


def first_atom(p):
    return p.open if isinstance(p, Group) else p


def last_atom(p):
    return p.close if isinstance(p, Group) else p


def gap(prev, nxt):
    """The space between two parts: as written when they were on one line,
    otherwise what Python style puts there."""
    a, b = last_atom(prev), first_atom(nxt)
    if b.kind == "COMMENT":
        return "  "
    if a.erow == b.row:
        return " " * max(0, b.col - a.ecol)
    if (a.text in OPEN or b.text in CLOSE or b.text in (",", ".", ":")
            or a.text == "."):
        return ""
    return " "


def is_value_end(p):
    a = last_atom(p)
    if a.kind == "NAME":
        return (not keyword.iskeyword(a.text)
                or a.text in ("None", "True", "False"))
    return a.kind in ("NUMBER", "STRING") or a.text in CLOSE


def breaks_before(parts, i):
    """0 if the element can't break before parts[i]; 1 before a keyword
    (comprehension, conditional or boolean), which binds loosest; 2 before
    a binary operator."""
    p = parts[i]
    if isinstance(p, Group) or i == 0:
        return 0
    q = parts[i - 1]
    if p.kind == "NAME" and p.text in BREAK_BEFORE:
        return 0 if isinstance(q, Atom) and q.text == "async" else 1
    if p.kind == "NAME" and p.text == "async":
        return 1
    return 2 if p.kind == "OP" and p.text in BINOPS and is_value_end(q) else 0


class Builder:
    def __init__(self, hang, plus=None):
        self.hang = hang  # hanging indent for top-level brackets
        # The bracket of "if (", whose contents line up with the body
        # unless continuation lines go in further.
        self.plus = plus

    def group_doc(self, g, top):
        n = len(g.elems)
        if n == 0:
            return g.open.text + g.close.text
        only = g.elems[0][0] if n == 1 and len(g.elems[0]) == 1 else None
        if (isinstance(only, Group) and not g.trailing
                and (only.open_comment
                     or any(is_comment(p) for e in only.elems for p in e))):
            # f({ with comments inside: the inner bracket hangs from the
            # line, not from the outer bracket.
            only.call = False
            return cat(g.open.text, self.group_doc(only, top), g.close.text)
        # A one-element tuple needs its comma.
        keep = g.trailing and g.open.text == "(" and n == 1 and not g.call
        commented = any(is_comment(p) for e in g.elems for p in e)
        contents = []
        tail_comment = False
        for i, e in enumerate(g.elems):
            trail = []
            while e and is_comment(e[-1]):
                trail.insert(0, e.pop())
            d = self.elem_doc(e)
            if g.open is self.plus and d[0] == "align":
                d = ("alignplus", 4, d[1])
            if i < n - 1 or keep:
                d = cat(d, ",")
            for c in trail:
                d = cat(d, "  " + c.text)
            contents.append(d)
            if i < n - 1:
                contents.append(HARD if commented else line(" "))
            elif trail:
                tail_comment = True
        body = ("fill", contents)
        if g.open_comment:
            return cat(g.open.text, "  " + g.open_comment.text,
                       ("nest", self.hang if top else 4, cat(HARD, body)),
                       HARD if tail_comment else "", g.close.text)
        hg = cat(g.open.text,
                 ("nest", self.hang if top else 4, cat(line(""), body)),
                 HARD if tail_comment else "", g.close.text)
        al = (None if commented
              else cat(g.open.text, ("align", body), g.close.text))
        return ("group", ("choose", al, hg))

    def seq_doc(self, parts, top=False):
        """Parts on one line; brackets inside may break."""
        out = []
        prev = None
        i = 0
        while i < len(parts):
            p = parts[i]
            if prev is not None:
                out.append(gap(prev, p))
            if isinstance(p, Group):
                p.call = prev is not None and is_value_end(prev)
                out.append(self.group_doc(p, top))
                prev = p
                i += 1
            elif p.kind == "STRING":
                j = i
                while (j + 1 < len(parts) and isinstance(parts[j + 1], Atom)
                       and parts[j + 1].kind == "STRING"):
                    j += 1
                out.append(self.strings_doc(parts[i:j + 1], not top))
                prev = parts[j]
                i = j + 1
            else:
                out.append(p.text)
                prev = p
                i += 1
        return cat(*out)

    def strings_doc(self, atoms, breakable):
        texts = [a.text for a in atoms]
        if not breakable:
            return " ".join(texts)
        runs = [[texts[0]]]
        qs = quotes_for(texts[0]) if parse_str(texts[0]) else set()
        for t in texts[1:]:
            tq = quotes_for(t) if parse_str(t) else set()
            if mergeable(runs[-1][-1], t) and qs & tq:
                runs[-1].append(t)
                qs &= tq
            else:
                runs.append([t])
                qs = tq
        docs, ends = [], []
        for r in runs:
            ends.append(newline_end(r[-1]))
            if parse_str(r[0]) is None:
                docs.append(" ".join(r))
            else:
                docs.append(string_breaks(*merge(r)))
        if len(docs) == 1:
            return docs[0] if isinstance(docs[0], str) else ("align", docs[0])
        parts = []
        for i, d in enumerate(docs):
            if i:
                # A literal that ends a line of text ends the source line.
                parts.append(HARD if ends[i - 1] else line(" "))
            parts.append(d)
        return ("align", ("fill", parts))

    def elem_doc(self, parts):
        """An element of a bracket: may break before boolean, conditional
        and binary operators, and between string literals."""
        parts = list(parts)
        lead = []
        while parts and is_comment(parts[0]):
            lead.append(parts.pop(0))
        runs, seps = [[]], []
        for i, p in enumerate(parts):
            if is_comment(p):
                runs[-1].append(p)
                runs.append([])
                seps.append(HARD)
                continue
            if runs[-1] and breaks_before(parts, i) == 1:
                runs.append([])
                seps.append(line(" "))
            runs[-1].append(p)
        if not runs[-1] and len(runs) > 1:
            runs.pop()
            seps.pop()
        if len(runs) == 1:
            doc = self.terms_doc(runs[0])
        else:
            contents = [self.terms_doc(runs[0])]
            for s, r in zip(seps, runs[1:]):
                contents += [s, self.terms_doc(r)]
            doc = ("align", ("fill", contents))
        for c in reversed(lead):
            doc = cat(c.text, HARD, doc)
        return doc

    def terms_doc(self, parts):
        """Parts that may break before binary operators, which bind more
        tightly than the keywords elem_doc breaks before."""
        runs = [[]]
        for i, p in enumerate(parts):
            if runs[-1] and not is_comment(p) and breaks_before(parts, i) == 2:
                runs.append([])
            runs[-1].append(p)
        if len(runs) == 1:
            return self.seq_doc(parts)
        contents = [self.seq_doc(runs[0])]
        for r in runs[1:]:
            contents += [line(" "), self.seq_doc(r)]
        return ("align", ("fill", contents))


# -- statements of a file -------------------------------------------------


def statements(atoms):
    """(atoms, NEWLINE atom) per logical line, without layout tokens."""
    cur = []
    for a in atoms:
        if a.kind in ("NL", "INDENT", "DEDENT", "ENCODING", "ENDMARKER"):
            continue
        if a.kind == "COMMENT" and not cur:
            continue
        if a.kind == "NEWLINE":
            if cur:
                yield cur, a
            cur = []
            continue
        cur.append(a)


def parenthesize_with(stmt):
    """``with a, b:`` as ``with (a, b):``, which can break between items."""
    depth, commas, colon = 0, False, None
    for a in stmt[1:]:
        if a.text in OPEN:
            depth += 1
        elif a.text in CLOSE:
            depth -= 1
        elif depth == 0 and a.text == ",":
            commas = True
        elif depth == 0 and a.text == ":":
            colon = a
    if not commas or colon is None:
        return stmt
    w = stmt[0]
    op = Atom("OP", "(", w.row, w.ecol + 1, w.row, w.ecol + 1)
    cl = Atom("OP", ")", colon.row, colon.col, colon.row, colon.col)
    i = stmt.index(colon)
    return [w, op] + stmt[1:i] + [cl] + stmt[i:]


ASSIGN = {"=", "+=", "-=", "*=", "/=", "//=", "%=", "|=", "&=", "^=", "<<=",
          ">>=", "@=", "**="}
SPLITS = BINOPS | {",", "if", "and", "or", "not", "in", "is", "==", "!=", "<",
                   ">", "<=", ">="}


def parenthesize_value(stmt):
    """``x = a + b`` as ``x = (a + b)``, which can break between terms; None
    if the value has no operator outside brackets to break at."""
    depth, start = 0, None
    if stmt[0].text in ("return", "yield"):
        start = 1
    for i, a in enumerate(stmt):
        if a.text in OPEN:
            depth += 1
        elif a.text in CLOSE:
            depth -= 1
        elif depth == 0 and a.kind == "OP" and a.text in ASSIGN:
            start = i + 1
    if start is None or start >= len(stmt):
        return None
    depth, split = 0, False
    for a in stmt[start:]:
        if a.text in OPEN:
            depth += 1
        elif a.text in CLOSE:
            depth -= 1
        elif depth == 0 and a.text in SPLITS:
            split = True
        elif depth == 0 and a.kind in ("COMMENT",):
            return None
    if not split:
        return None
    first, last = stmt[start], stmt[-1]
    op = Atom("OP", "(", first.row, first.col, first.row, first.col)
    cl = Atom("OP", ")", last.erow, last.ecol, last.erow, last.ecol)
    return stmt[:start] + [op] + stmt[start:] + [cl]


def parenthesize_condition(stmt):
    """``if a and b:`` as ``if (a and b):``; None if the condition is
    already one bracket or the statement isn't if, elif or while."""
    if stmt[0].text not in ("if", "elif", "while"):
        return None
    depth, colon = 0, None
    for i, a in enumerate(stmt):
        if a.text in OPEN:
            depth += 1
        elif a.text in CLOSE:
            depth -= 1
        elif depth == 0 and a.text == ":":
            colon = i
            break
    if colon is None or colon < 2:
        return None
    body = stmt[1:colon]
    if body[0].text in OPEN and matching(body) == len(body) - 1:
        return None
    first, last = body[0], body[-1]
    op = Atom("OP", "(", first.row, first.col, first.row, first.col)
    cl = Atom("OP", ")", last.erow, last.ecol, last.erow, last.ecol)
    return stmt[:1] + [op] + body + [cl] + stmt[colon:]


def matching(atoms):
    """The index of the bracket that closes atoms[0]."""
    depth = 0
    for i, a in enumerate(atoms):
        if a.text in OPEN:
            depth += 1
        elif a.text in CLOSE:
            depth -= 1
            if depth == 0:
                return i
    return -1


def fits_width(indent, text):
    """Whether every line of a statement's text fits in ``WIDTH``."""
    return max(len(x) for x in (indent + text).split("\n")) <= WIDTH


def score(text):
    """Lines, with each hanging break counting as two more."""
    lines = text.split("\n")
    return (len(lines) + 2
            * sum(1 for x in lines[:-1] if x.rstrip()[-1:] in OPEN))


def lay_out(stmt, indent):
    """The text of the statement ``stmt`` (its atoms), indented by
    ``indent``."""
    hang = 8 if stmt[0].text in COMPOUND else 4

    def run(stmt):
        plus = (stmt[1] if stmt[0].text == "if" and stmt[1].text == "("
                else None)
        return render(Builder(hang, plus).seq_doc(tree_of(stmt), top=True),
                      len(indent), len(indent))

    text = run(stmt)
    # Brackets added around a value or condition can break, which helps
    # when it now spans lines or doesn't fit.
    breaks = "\n" in text or not fits_width(indent, text)
    if stmt[0].text == "with" and breaks:
        text = run(parenthesize_with(stmt))
    elif breaks:
        alt = parenthesize_value(stmt) or parenthesize_condition(stmt)
        if alt is not None:
            t2 = run(alt)
            if fits_width(indent, t2) and (not fits_width(indent, text)
                                           or score(t2) <= score(text)):
                text = t2
    return drop_needless_f(indent + text)


def align(src: str) -> str:
    """``src`` with every statement that spans several lines laid out in the
    compact aligned style.  A statement that would come out wider than
    ``WIDTH`` when it fits now is left as it is, and so is ``src`` if it
    isn't a whole module.  Raises ``ValueError`` if the layout would change
    what the code means, which would be a bug here."""
    try:
        before = ast.dump(ast.parse(src))
    except SyntaxError:
        return src
    lines = src.splitlines(keepends=True)
    edits = []
    for stmt, nl in statements(atoms_of(src)):
        srow, erow = stmt[0].row, nl.row
        if srow == erow or len(stmt) == 1:
            continue
        trailing = None
        if stmt[-1].kind == "COMMENT" and stmt[-1].row == nl.row:
            trailing = stmt.pop()
        indent = lines[srow - 1][:stmt[0].col]
        try:
            text = lay_out(stmt, indent)
        except (SyntaxError, tokenize.TokenError):
            continue  # a statement this can't lay out stays as it is
        if trailing:
            text += "  " + trailing.text
        old = lines[srow - 1:erow]
        if (max(len(x) for x in text.split("\n")) > WIDTH
                and max(len(x.rstrip("\n")) for x in old) <= WIDTH):
            continue
        edits.append((srow, erow, text + "\n"))
    for srow, erow, text in reversed(edits):
        lines[srow - 1:erow] = [text]
    new = "".join(lines)
    if ast.dump(ast.parse(new)) != before:
        raise ValueError("aligning the code would change its meaning")
    return new


def main(argv=None) -> int:
    """``python3 -m xtcegen.pyalign [--check] FILE...``: lay out each file,
    or with ``--check`` report the files whose layout would change."""
    ap = argparse.ArgumentParser(
        prog="pyalign", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="change nothing; fail if a file would change")
    ap.add_argument("files", nargs="+", metavar="FILE")
    args = ap.parse_args(argv)
    changed = []
    for name in args.files:
        with open(name, encoding="utf-8") as f:
            src = f.read()
        new = align(src)
        if new != src:
            changed.append(name)
            if not args.check:
                with open(name, "w", encoding="utf-8") as f:
                    f.write(new)
    if args.check and changed:
        for name in changed:
            print(f"pyalign: {name} is not laid out", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
