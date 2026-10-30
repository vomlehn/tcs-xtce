"""Compile a string alarm's ``matchPattern`` into a DFA over UTF-8 bytes.

The generated code can't count on a regular expression library in every
language, so the generator turns each pattern into a deterministic
automaton (``model.Dfa``) that the runtimes walk byte by byte.  A string
alarm matches when the whole string matches, as Java's ``matches()`` does
for Yamcs.

Patterns use Python's syntax, parsed with its ``re`` parser: literals,
``.``, classes (``[a-z]``, ``[^...]``, ``\\d``, ``\\w``, ``\\s`` and their
negations, which are ASCII), groups, ``|``, ``*``, ``+``, ``?`` and
``{m,n}`` (lazy forms match the same strings), ``^`` at the start and
``$`` at the end, and ``(?i)``, which folds ASCII letters.  Anything that
isn't regular, such as backreferences and lookarounds, raises
``Unsupported``.
"""

from __future__ import annotations

try:
    from re import _constants as C
    from re import _parser as sre_parse
except ImportError:  # Python before 3.11
    import sre_constants as C  # type: ignore
    import sre_parse  # type: ignore

from .model import Dfa, Unsupported

MAX_CODE = 0x10FFFF
MAX_STATES = 20000

DIGITS = [(0x30, 0x39)]
WORD = [(0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A)]
SPACE = [(0x09, 0x0D), (0x20, 0x20)]
CATEGORIES = {
    C.CATEGORY_DIGIT: (DIGITS, False), C.CATEGORY_NOT_DIGIT: (DIGITS, True),
    C.CATEGORY_WORD: (WORD, False), C.CATEGORY_NOT_WORD: (WORD, True),
    C.CATEGORY_SPACE: (SPACE, False), C.CATEGORY_NOT_SPACE: (SPACE, True)}


def normalize(ranges: list) -> list:
    """Sorts and merges code point ranges, dropping the surrogates (which
    UTF-8 can't hold).

    Args:
        ranges: The ranges, each an inclusive (first, last) pair of code
                points, in any order and possibly overlapping.

    Returns:
        The same code points as sorted, disjoint, non-adjacent ranges.
    """
    out: list = []
    for lo, hi in sorted(ranges):
        for a, b in ((lo, min(hi, 0xD7FF)), (max(lo, 0xE000), hi)):
            if a > b:
                continue
            if out and a <= out[-1][1] + 1:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
    return out


def negate(ranges: list) -> list:
    """Complements a set of code point ranges.

    Args:
        ranges: The ranges, each an inclusive (first, last) pair.

    Returns:
        The normalized ranges of every code point up to ``MAX_CODE``,
        except the surrogates, that isn't in ``ranges``.
    """
    out, at = [], 0
    for lo, hi in normalize(ranges):
        if lo > at:
            out.append((at, lo - 1))
        at = hi + 1
    if at <= MAX_CODE:
        out.append((at, MAX_CODE))
    return normalize(out)


def fold(ranges: list) -> list:
    """Adds the other case of the ASCII letters in ``ranges``.

    Args:
        ranges: The ranges, each an inclusive (first, last) pair of code
                points.

    Returns:
        The normalized ranges, with both cases of each ASCII letter.
    """
    extra = []
    for lo, hi in ranges:
        for a, b, d in ((0x41, 0x5A, 32), (0x61, 0x7A, -32)):
            x, y = max(lo, a), min(hi, b)
            if x <= y:
                extra.append((x + d, y + d))
    return normalize(ranges + extra)


def utf8(c: int) -> bytes:
    return chr(c).encode("utf-8")


def utf8_sequences(lo: int, hi: int) -> list:
    """Encodes a range of code points as UTF-8 byte ranges.

    Args:
        lo: The first code point.
        hi: The last code point, at least ``lo``.

    Returns:
        A list of byte range sequences, each a list of (first, last)
        per byte, that together match exactly the UTF-8 encodings of
        ``lo`` to ``hi``.
    """
    for limit in (0x7F, 0x7FF, 0xFFFF):
        if lo <= limit < hi:
            return utf8_sequences(lo, limit) + utf8_sequences(limit + 1, hi)
    if hi < 0x80:
        return [[(lo, hi)]]
    n = len(utf8(lo))
    for i in range(1, n):
        m = (1 << (6 * i)) - 1
        if lo & ~m != hi & ~m:
            if lo & m:
                return (utf8_sequences(lo, lo | m)
                        + utf8_sequences((lo | m) + 1, hi))
            if hi & m != m:
                return (utf8_sequences(lo, (hi & ~m) - 1)
                        + utf8_sequences(hi & ~m, hi))
    a, b = utf8(lo), utf8(hi)
    return [list(zip(a, b))]


class Nfa:
    """A Thompson automaton: each state has byte-range edges and epsilon
    edges."""

    def __init__(self) -> None:
        self.edges: list[list] = []
        self.eps: list[list] = []

    def state(self) -> int:
        """Adds a state with no edges.

        Returns:
            The new state's number.

        Raises:
            Unsupported: The automaton already has ``MAX_STATES`` states.
        """
        if len(self.edges) >= MAX_STATES:
            raise Unsupported("a matchPattern too large to compile")
        self.edges.append([])
        self.eps.append([])
        return len(self.edges) - 1

    def chars(self, ranges: list) -> tuple:
        """Adds a fragment that matches one code point in ``ranges``.

        Args:
            ranges: The code points, as inclusive (first, last) pairs.

        Returns:
            The fragment's (start, end) states.
        """
        start, end = self.state(), self.state()
        for lo, hi in normalize(ranges):
            for seq in utf8_sequences(lo, hi):
                q = start
                for k, (a, b) in enumerate(seq):
                    nxt = end if k == len(seq) - 1 else self.state()
                    self.edges[q].append((a, b, nxt))
                    q = nxt
        return start, end

    def empty(self) -> tuple:
        q = self.state()
        return q, q

    def concat(self, frags: list) -> tuple:
        """Joins fragments one after the other with epsilon edges.

        Args:
            frags: The fragments' (start, end) states, in order.

        Returns:
            The (start, end) states of the whole sequence; a new empty
            fragment's when ``frags`` is empty.
        """
        if not frags:
            return self.empty()
        for (_, e), (s, _) in zip(frags, frags[1:]):
            self.eps[e].append(s)
        return frags[0][0], frags[-1][1]

    def alt(self, frags: list) -> tuple:
        """Adds a fragment that matches any one of ``frags``.

        Args:
            frags: The alternatives' (start, end) states.

        Returns:
            The (start, end) states of the alternation.
        """
        start, end = self.state(), self.state()
        for s, e in frags:
            self.eps[start].append(s)
            self.eps[e].append(end)
        return start, end

    def star(self, frag: tuple) -> tuple:
        """Adds a fragment that matches ``frag`` zero or more times.

        Args:
            frag: The repeated fragment's (start, end) states.

        Returns:
            The (start, end) states of the repetition.
        """
        start, end = self.state(), self.state()
        s, e = frag
        self.eps[start] += [s, end]
        self.eps[e] += [s, end]
        return start, end


def compile_pattern(pattern: str) -> Dfa:
    """Compiles a ``matchPattern`` into a minimal DFA over UTF-8 bytes.

    Args:
        pattern: The pattern, in Python's syntax; it must match the
                 whole string.

    Returns:
        The automaton.

    Raises:
        Unsupported: The pattern doesn't parse, uses something that
                     isn't supported (see the module docstring) or is too
                     large to compile.
    """
    try:
        tree = sre_parse.parse(pattern)
    except Exception as e:  # re.error, or worse
        raise Unsupported(f"matchPattern {pattern!r}: {e}") from None
    ignore = bool(tree.state.flags & C.SRE_FLAG_IGNORECASE)
    nfa = Nfa()

    def chars(ranges: list) -> tuple:
        return nfa.chars(fold(ranges) if ignore else ranges)

    def seq(items, first: bool, last: bool) -> tuple:
        """Builds the fragment for a sequence of parsed pattern items.

        Args:
            items: The items, each an (op, argument) pair from the parser.
            first: Whether the sequence starts the pattern, so ``^`` may
                   begin it.
            last:  Whether the sequence ends the pattern, so ``$`` may
                   end it.

        Returns:
            The sequence's (start, end) states.
        """
        items = list(items)
        frags = []
        for k, (op, av) in enumerate(items):
            frags.append(node(op, av, first and k == 0,
                              last and k == len(items) - 1))
        return nfa.concat(frags)

    def node(op, av, first: bool, last: bool) -> tuple:
        """Builds the fragment for one parsed pattern item.

        Args:
            op:    The parser's opcode.
            av:    Its argument.
            first: Whether the item starts the pattern.
            last:  Whether the item ends the pattern.

        Returns:
            The item's (start, end) states.

        Raises:
            Unsupported: The item isn't supported, or is a repeat too
                         large to compile.
        """
        if op is C.LITERAL:
            return chars([(av, av)])
        if op is C.NOT_LITERAL:
            return chars(negate([(av, av)]))
        if op is C.ANY:
            return nfa.chars(negate([(0x0A, 0x0A)]))
        if op is C.IN:
            ranges, neg = [], False
            for iop, iav in av:
                if iop is C.NEGATE:
                    neg = True
                elif iop is C.LITERAL:
                    ranges.append((iav, iav))
                elif iop is C.RANGE:
                    ranges.append(iav)
                elif iop is C.CATEGORY and iav in CATEGORIES:
                    r, n = CATEGORIES[iav]
                    ranges += negate(r) if n else r
                else:
                    raise Unsupported(f"matchPattern {pattern!r}: {iop} in a "
                                      "class")
            if ignore:
                ranges = fold(ranges)
            return nfa.chars(negate(ranges) if neg else ranges)
        if op is C.BRANCH:
            return nfa.alt([seq(b, first, last) for b in av[1]])
        if op is C.SUBPATTERN:
            group_flags = av[1] | av[2]
            if group_flags:
                raise Unsupported(f"matchPattern {pattern!r}: flags in a group")
            return seq(av[3], first, last)
        if op in (C.MAX_REPEAT, C.MIN_REPEAT):
            lo, hi, body = av
            frags = [seq(body, False, False) for _ in range(lo)]
            if hi is C.MAXREPEAT:
                frags.append(nfa.star(seq(body, False, False)))
            else:
                if hi - lo > 1000:
                    raise Unsupported(f"matchPattern {pattern!r}: a repeat too "
                                      "large to compile")
                for _ in range(hi - lo):
                    frags.append(nfa.alt([seq(body, False, False),
                                          nfa.empty()]))
            return nfa.concat(frags)
        if op is C.AT:
            if ((av is C.AT_BEGINNING and first)
                    or (av in (C.AT_END, C.AT_END_STRING) and last)
                    or (av is C.AT_BEGINNING_STRING and first)):
                return nfa.empty()
        raise Unsupported(f"matchPattern {pattern!r}: {op} {av!r} is not "
                          "supported")

    start, final = seq(tree, True, True)
    return determinize(nfa, start, final, pattern)


def determinize(nfa: Nfa, start: int, final: int, pattern: str) -> Dfa:
    """Turns an NFA into a minimal DFA by the subset construction.

    Args:
        nfa:     The automaton.
        start:   Its start state.
        final:   Its accepting state.
        pattern: The pattern it came from, for messages.

    Returns:
        The minimized deterministic automaton.

    Raises:
        Unsupported: The DFA would have more than ``MAX_STATES`` states.
    """
    def closure(qs) -> frozenset:
        """Finds the states reachable by epsilon edges.

        Args:
            qs: The states to start from.

        Returns:
            Those states and every state reachable from them by epsilon
            edges.
        """
        seen, todo = set(qs), list(qs)
        while todo:
            for r in nfa.eps[todo.pop()]:
                if r not in seen:
                    seen.add(r)
                    todo.append(r)
        return frozenset(seen)

    first = closure([start])
    index = {first: 0}
    order = [first]
    table: list[list] = []
    k = 0
    while k < len(order):
        qs = order[k]
        k += 1
        edges = [e for q in qs for e in nfa.edges[q]]
        cuts = sorted({a for a, _, _ in edges} | {b + 1 for _, b, _ in edges})
        row = []
        for a, b in zip(cuts, cuts[1:]):
            dest = closure([t for lo, hi, t in edges if lo <= a
                            and b - 1 <= hi])
            if not dest:
                continue
            if dest not in index:
                if len(order) >= MAX_STATES:
                    raise Unsupported(f"matchPattern {pattern!r} is too large "
                                      "to compile")
                index[dest] = len(order)
                order.append(dest)
            nxt = index[dest]
            if row and row[-1][2] == nxt and row[-1][1] == a - 1:
                row[-1] = (row[-1][0], b - 1, nxt)
            else:
                row.append((a, b - 1, nxt))
        table.append(row)
    return minimize(table, [final in qs for qs in order], pattern)


def minimize(table: list, accept: list, pattern: str) -> Dfa:
    """Finds the smallest automaton equivalent to ``table`` by refining
    the partition of its states (Moore's algorithm).

    Args:
        table:   Each state's transitions, as sorted (lo, hi, next)
                 byte ranges; state 0 is the start.
        accept:  Whether each state accepts.
        pattern: The pattern the automaton matches.

    Returns:
        The minimal automaton, its states numbered in the order they are
        first reached from the start, unreachable ones dropped.
    """
    cls = [int(a) for a in accept]
    while True:
        sigs = {}
        new = []
        for q, row in enumerate(table):
            merged: list = []
            for lo, hi, nxt in row:
                c = cls[nxt]
                if merged and merged[-1][2] == c and merged[-1][1] == lo - 1:
                    merged[-1] = (merged[-1][0], hi, c)
                else:
                    merged.append((lo, hi, c))
            new.append(sigs.setdefault((cls[q], tuple(merged)), len(sigs)))
        if len(sigs) == len(set(cls)):
            break
        cls = new
    # Number the classes in the order they are first reached, start first.
    number = {cls[0]: 0}
    order = [0]
    k = 0
    while k < len(order):
        q = next(i for i, c in enumerate(cls) if number.get(c) == order[k])
        k += 1
        for _, _, nxt in table[q]:
            if cls[nxt] not in number:
                number[cls[nxt]] = len(number)
                order.append(number[cls[nxt]])
    reps = {}
    for q, c in enumerate(cls):
        if c in number:
            reps.setdefault(number[c], q)
    starts, trans, acc = [0], [], []
    for n in range(len(number)):
        q = reps[n]
        for lo, hi, nxt in table[q]:
            m = number[cls[nxt]]
            if (trans and len(trans) > starts[-1] and trans[-1][2] == m
                    and trans[-1][1] == lo - 1):
                trans[-1] = (trans[-1][0], hi, m)
            else:
                trans.append((lo, hi, m))
        starts.append(len(trans))
        acc.append(accept[q])
    return Dfa(pattern, starts, trans, acc)


def matches(dfa: Dfa, data: bytes) -> bool:
    """Runs an automaton over some bytes, as the runtimes do, for tests.

    Args:
        dfa:  The automaton.
        data: The string, as UTF-8.

    Returns:
        Whether the whole of ``data`` matches.
    """

    q = 0
    for b in data:
        for lo, hi, nxt in dfa.trans[dfa.starts[q]:dfa.starts[q + 1]]:
            if lo <= b <= hi:
                q = nxt
                break
        else:
            return False
    return dfa.accept[q]
