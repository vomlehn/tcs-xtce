//! A string alarm's `matchPattern`, compiled into a deterministic
//! automaton over UTF-8 bytes ([`Dfa`]) that the runtimes walk byte by
//! byte, as the Python compiler's `regex.py` does. A string matches when
//! the whole of it matches.
//!
//! Patterns are parsed by `regex-syntax`: literals, `.`, classes (`[a-z]`,
//! `[^...]`, `\d`, `\w`, `\s` and their negations, which are ASCII),
//! groups, `|`, `*`, `+`, `?` and `{m,n}` (lazy forms match the same
//! strings), `^` at the start and `$` at the end, and `(?i)` at the start,
//! which folds ASCII letters. Anything else is unsupported. The automaton
//! is minimal and its states are numbered in the order they are first
//! reached, so it is the one the Python compiler makes for the same
//! language.

use std::collections::{BTreeSet, HashMap};

use regex_syntax::ast::{self, Ast};

use crate::model::{unsupported, Dfa, Result};
use crate::pyfmt::repr;

/// The last code point.
const MAX_CODE: u32 = 0x10FFFF;
/// The most states either automaton may have.
const MAX_STATES: usize = 20000;
/// The most copies a bounded repeat may add.
const MAX_REPEAT_SPAN: u32 = 1000;

/// `\d`, as ASCII.
const DIGITS: &[(u32, u32)] = &[(0x30, 0x39)];
/// `\w`, as ASCII.
const WORD: &[(u32, u32)] =
    &[(0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A)];
/// `\s`, as ASCII.
const SPACE: &[(u32, u32)] = &[(0x09, 0x0D), (0x20, 0x20)];

/// Sorts and merges code point ranges, dropping the surrogates.
///
/// # Arguments
///
/// * `ranges` - The ranges, each an inclusive (first, last) pair, in any order
///              and possibly overlapping.
///
/// # Returns
///
/// The same code points as sorted, disjoint, non-adjacent ranges.
fn normalize(ranges: &[(u32, u32)]) -> Vec<(u32, u32)> {
    let mut sorted = ranges.to_vec();
    sorted.sort();
    let mut out: Vec<(u32, u32)> = Vec::new();
    for (lo, hi) in sorted {
        for (a, b) in [(lo, hi.min(0xD7FF)), (lo.max(0xE000), hi)] {
            if a > b {
                continue;
            }
            match out.last_mut() {
                Some(last) if a <= last.1 + 1 => last.1 = last.1.max(b),
                _ => out.push((a, b)),
            }
        }
    }
    out
}

/// Complements a set of code point ranges.
///
/// # Arguments
///
/// * `ranges` - The ranges.
///
/// # Returns
///
/// The normalized ranges of every code point but the surrogates that
/// isn't in `ranges`.
fn negate(ranges: &[(u32, u32)]) -> Vec<(u32, u32)> {
    let mut out = Vec::new();
    let mut at = 0;
    for (lo, hi) in normalize(ranges) {
        if lo > at {
            out.push((at, lo - 1));
        }
        at = hi + 1;
    }
    if at <= MAX_CODE {
        out.push((at, MAX_CODE));
    }
    normalize(&out)
}

/// Adds the other case of the ASCII letters in some ranges.
///
/// # Arguments
///
/// * `ranges` - The ranges.
///
/// # Returns
///
/// The normalized ranges, with both cases of each ASCII letter.
fn fold(ranges: &[(u32, u32)]) -> Vec<(u32, u32)> {
    let mut all = ranges.to_vec();
    for &(lo, hi) in ranges {
        for (a, b, up) in [(0x41, 0x5A, true), (0x61, 0x7A, false)] {
            let (x, y) = (lo.max(a), hi.min(b));
            if x <= y {
                all.push(if up {
                    (x + 32, y + 32)
                } else {
                    (x - 32, y - 32)
                });
            }
        }
    }
    normalize(&all)
}

/// Encodes a code point as UTF-8.
///
/// # Arguments
///
/// * `c` - The code point, not a surrogate.
///
/// # Returns
///
/// Its bytes.
fn utf8(c: u32) -> Vec<u8> {
    let ch = char::from_u32(c).expect("a code point that is not a surrogate");
    ch.to_string().into_bytes()
}

/// Encodes a range of code points as UTF-8 byte ranges.
///
/// # Arguments
///
/// * `lo` - The first code point.
/// * `hi` - The last, at least `lo`.
///
/// # Returns
///
/// Sequences of (first, last) byte ranges, one per byte, that together
/// match exactly the UTF-8 encodings of `lo` to `hi`.
fn utf8_sequences(lo: u32, hi: u32) -> Vec<Vec<(u8, u8)>> {
    for limit in [0x7F, 0x7FF, 0xFFFF] {
        if lo <= limit && limit < hi {
            let mut out = utf8_sequences(lo, limit);
            out.extend(utf8_sequences(limit + 1, hi));
            return out;
        }
    }
    if hi < 0x80 {
        return vec![vec![(lo as u8, hi as u8)]];
    }
    let n = utf8(lo).len();
    for i in 1..n {
        let m = (1u32 << (6 * i)) - 1;
        if lo & !m != hi & !m {
            if lo & m != 0 {
                let mut out = utf8_sequences(lo, lo | m);
                out.extend(utf8_sequences((lo | m) + 1, hi));
                return out;
            }
            if hi & m != m {
                let mut out = utf8_sequences(lo, (hi & !m) - 1);
                out.extend(utf8_sequences(hi & !m, hi));
                return out;
            }
        }
    }
    let (a, b) = (utf8(lo), utf8(hi));
    vec![a.into_iter().zip(b).collect()]
}

/// A Thompson automaton: each state has byte-range edges and epsilon
/// edges.
#[derive(Default)]
struct Nfa {
    /// Each state's edges: (first byte, last byte, next state).
    edges: Vec<Vec<(u8, u8, usize)>>,
    /// Each state's epsilon edges.
    eps: Vec<Vec<usize>>,
}

/// A fragment of the automaton: its start and end states.
type Frag = (usize, usize);

impl Nfa {
    /// Adds a state with no edges.
    ///
    /// # Returns
    ///
    /// The new state's number.
    ///
    /// # Errors
    ///
    /// Unsupported, if the automaton has [`MAX_STATES`] states already.
    fn state(&mut self) -> Result<usize> {
        if self.edges.len() >= MAX_STATES {
            return unsupported("a matchPattern too large to compile");
        }
        self.edges.push(Vec::new());
        self.eps.push(Vec::new());
        Ok(self.edges.len() - 1)
    }

    /// Adds a fragment that matches one code point in some ranges.
    ///
    /// # Arguments
    ///
    /// * `ranges` - The code points.
    ///
    /// # Returns
    ///
    /// The fragment.
    ///
    /// # Errors
    ///
    /// Unsupported, if the automaton grows too large.
    fn chars(&mut self, ranges: &[(u32, u32)]) -> Result<Frag> {
        let start = self.state()?;
        let end = self.state()?;
        for (lo, hi) in normalize(ranges) {
            for seq in utf8_sequences(lo, hi) {
                let mut q = start;
                for (k, &(a, b)) in seq.iter().enumerate() {
                    let nxt = if k == seq.len() - 1 {
                        end
                    } else {
                        self.state()?
                    };
                    self.edges[q].push((a, b, nxt));
                    q = nxt;
                }
            }
        }
        Ok((start, end))
    }

    /// Adds a fragment that matches the empty string.
    ///
    /// # Returns
    ///
    /// The fragment.
    ///
    /// # Errors
    ///
    /// Unsupported, if the automaton grows too large.
    fn empty(&mut self) -> Result<Frag> {
        let q = self.state()?;
        Ok((q, q))
    }

    /// Joins fragments one after the other with epsilon edges.
    ///
    /// # Arguments
    ///
    /// * `frags` - The fragments, in order.
    ///
    /// # Returns
    ///
    /// The whole sequence; a new empty fragment for none.
    ///
    /// # Errors
    ///
    /// Unsupported, if the automaton grows too large.
    fn concat(&mut self, frags: &[Frag]) -> Result<Frag> {
        if frags.is_empty() {
            return self.empty();
        }
        for w in frags.windows(2) {
            self.eps[w[0].1].push(w[1].0);
        }
        Ok((frags[0].0, frags[frags.len() - 1].1))
    }

    /// Adds a fragment that matches any one of some fragments.
    ///
    /// # Arguments
    ///
    /// * `frags` - The alternatives.
    ///
    /// # Returns
    ///
    /// The alternation.
    ///
    /// # Errors
    ///
    /// Unsupported, if the automaton grows too large.
    fn alt(&mut self, frags: &[Frag]) -> Result<Frag> {
        let start = self.state()?;
        let end = self.state()?;
        for &(s, e) in frags {
            self.eps[start].push(s);
            self.eps[e].push(end);
        }
        Ok((start, end))
    }

    /// Adds a fragment that matches another zero or more times.
    ///
    /// # Arguments
    ///
    /// * `frag` - The repeated fragment.
    ///
    /// # Returns
    ///
    /// The repetition.
    ///
    /// # Errors
    ///
    /// Unsupported, if the automaton grows too large.
    fn star(&mut self, frag: Frag) -> Result<Frag> {
        let start = self.state()?;
        let end = self.state()?;
        let (s, e) = frag;
        self.eps[start].extend([s, end]);
        self.eps[e].extend([s, end]);
        Ok((start, end))
    }
}

/// Builds the automaton of a parsed pattern.
struct Builder<'a> {
    /// The pattern, for messages.
    pattern: &'a str,
    /// Whether ASCII letters match either case.
    ignore: bool,
    /// The automaton so far.
    nfa: Nfa,
}

impl Builder<'_> {
    /// Says that a construct is not supported.
    ///
    /// # Arguments
    ///
    /// * `what` - The construct.
    ///
    /// # Returns
    ///
    /// Nothing: it fails.
    ///
    /// # Errors
    ///
    /// Unsupported, always, naming the construct.
    fn refuse<T>(&self, what: &str) -> Result<T> {
        unsupported(format!(
            "matchPattern {}: {what} is not supported",
            repr(self.pattern)
        ))
    }

    /// Adds a fragment that matches one code point, folded if the pattern
    /// ignores case.
    ///
    /// # Arguments
    ///
    /// * `ranges` - The code points.
    ///
    /// # Returns
    ///
    /// The fragment.
    ///
    /// # Errors
    ///
    /// Unsupported, if the automaton grows too large.
    fn chars(&mut self, ranges: &[(u32, u32)]) -> Result<Frag> {
        let r = if self.ignore {
            fold(ranges)
        } else {
            ranges.to_vec()
        };
        self.nfa.chars(&r)
    }

    /// Builds the fragment for a sequence of items.
    ///
    /// # Arguments
    ///
    /// * `items` - The items.
    /// * `first` - Whether the sequence starts the pattern, so `^` may begin
    ///             it.
    /// * `last`  - Whether it ends the pattern, so `$` may end it.
    ///
    /// # Returns
    ///
    /// The sequence's fragment.
    ///
    /// # Errors
    ///
    /// Unsupported, for an item that isn't supported or a pattern too
    /// large.
    fn seq(&mut self, items: &[Ast], first: bool, last: bool) -> Result<Frag> {
        let mut frags = Vec::new();
        for (k, a) in items.iter().enumerate() {
            frags.push(self.node(
                a,
                first && k == 0,
                last && k == items.len() - 1,
            )?);
        }
        self.nfa.concat(&frags)
    }

    /// Builds the fragment for one item, which may be a sequence.
    ///
    /// # Arguments
    ///
    /// * `a`     - The item.
    /// * `first` - Whether it starts the pattern.
    /// * `last`  - Whether it ends the pattern.
    ///
    /// # Returns
    ///
    /// The item's fragment.
    ///
    /// # Errors
    ///
    /// Unsupported, for an item that isn't supported or a pattern too
    /// large.
    fn node(&mut self, a: &Ast, first: bool, last: bool) -> Result<Frag> {
        match a {
            Ast::Empty(_) => self.nfa.empty(),
            Ast::Concat(c) => self.seq(&c.asts, first, last),
            Ast::Literal(l) => {
                let c = l.c as u32;
                self.chars(&[(c, c)])
            }
            Ast::Dot(_) => self.nfa.chars(&negate(&[(0x0A, 0x0A)])),
            Ast::ClassPerl(p) => {
                let r = perl(p);
                self.nfa.chars(&if self.ignore { fold(&r) } else { r })
            }
            Ast::ClassBracketed(b) => {
                let mut ranges = Vec::new();
                self.class_set(&b.kind, &mut ranges)?;
                if self.ignore {
                    ranges = fold(&ranges);
                }
                let r = if b.negated { negate(&ranges) } else { ranges };
                self.nfa.chars(&r)
            }
            Ast::ClassUnicode(_) => self.refuse("a Unicode class"),
            Ast::Alternation(alt) => {
                let mut frags = Vec::new();
                for b in &alt.asts {
                    frags.push(self.node(b, first, last)?);
                }
                self.nfa.alt(&frags)
            }
            Ast::Group(g) => {
                if let ast::GroupKind::NonCapturing(f) = &g.kind {
                    if !f.items.is_empty() {
                        return unsupported(format!(
                            "matchPattern {}: flags in a group",
                            repr(self.pattern)
                        ));
                    }
                }
                self.node(&g.ast, first, last)
            }
            Ast::Repetition(r) => self.repeat(r),
            Ast::Assertion(at) => {
                use ast::AssertionKind as K;
                match at.kind {
                    K::StartLine | K::StartText if first => self.nfa.empty(),
                    K::EndLine | K::EndText if last => self.nfa.empty(),
                    _ => self.refuse("an assertion here"),
                }
            }
            Ast::Flags(_) => self.refuse("flags other than (?i) at the start"),
        }
    }

    /// Builds the fragment for a repeat.
    ///
    /// # Arguments
    ///
    /// * `r` - The repetition.
    ///
    /// # Returns
    ///
    /// Its fragment: the least copies, then a star or the optional ones.
    ///
    /// # Errors
    ///
    /// Unsupported, for a repeat too large to compile.
    fn repeat(&mut self, r: &ast::Repetition) -> Result<Frag> {
        use ast::RepetitionKind as K;
        use ast::RepetitionRange as R;
        let (lo, hi) = match &r.op.kind {
            K::ZeroOrOne => (0, Some(1)),
            K::ZeroOrMore => (0, None),
            K::OneOrMore => (1, None),
            K::Range(R::Exactly(n)) => (*n, Some(*n)),
            K::Range(R::AtLeast(n)) => (*n, None),
            K::Range(R::Bounded(m, n)) => (*m, Some(*n)),
        };
        let mut frags = Vec::new();
        for _ in 0..lo {
            frags.push(self.node(&r.ast, false, false)?);
        }
        match hi {
            None => {
                let body = self.node(&r.ast, false, false)?;
                frags.push(self.nfa.star(body)?);
            }
            Some(hi) => {
                if hi - lo > MAX_REPEAT_SPAN {
                    return unsupported(format!(
                        "matchPattern {}: a repeat too large to compile",
                        repr(self.pattern)
                    ));
                }
                for _ in lo..hi {
                    let body = self.node(&r.ast, false, false)?;
                    let none = self.nfa.empty()?;
                    frags.push(self.nfa.alt(&[body, none])?);
                }
            }
        }
        self.nfa.concat(&frags)
    }

    /// Gathers the code points of a bracketed class.
    ///
    /// # Arguments
    ///
    /// * `set`    - The class's set.
    /// * `ranges` - Where its ranges go.
    ///
    /// # Errors
    ///
    /// Unsupported, for set operations and nested, POSIX and Unicode
    /// classes.
    fn class_set(
        &self,
        set: &ast::ClassSet,
        ranges: &mut Vec<(u32, u32)>,
    ) -> Result<()> {
        use ast::ClassSetItem as I;
        let item = match set {
            ast::ClassSet::Item(i) => i,
            ast::ClassSet::BinaryOp(_) => {
                return self.refuse("a class operation")
            }
        };
        let items = match item {
            I::Union(u) => u.items.iter().collect::<Vec<_>>(),
            i => vec![i],
        };
        for i in items {
            match i {
                I::Empty(_) => {}
                I::Literal(l) => ranges.push((l.c as u32, l.c as u32)),
                I::Range(r) => ranges.push((r.start.c as u32, r.end.c as u32)),
                I::Perl(p) => ranges.extend(perl(p)),
                I::Ascii(_) => return self.refuse("a POSIX class"),
                I::Unicode(_) => return self.refuse("a Unicode class"),
                I::Bracketed(_) => return self.refuse("a class in a class"),
                I::Union(_) => unreachable!("unions are flattened above"),
            }
        }
        Ok(())
    }
}

/// Gives the code points of `\d`, `\s` or `\w`, or their negation.
///
/// # Arguments
///
/// * `p` - The class.
///
/// # Returns
///
/// Its ranges.
fn perl(p: &ast::ClassPerl) -> Vec<(u32, u32)> {
    let r = match p.kind {
        ast::ClassPerlKind::Digit => DIGITS,
        ast::ClassPerlKind::Space => SPACE,
        ast::ClassPerlKind::Word => WORD,
    };
    if p.negated {
        negate(r)
    } else {
        r.to_vec()
    }
}

/// Compiles a `matchPattern` into a minimal DFA over UTF-8 bytes.
///
/// # Arguments
///
/// * `pattern` - The pattern; it must match the whole string.
///
/// # Returns
///
/// The automaton.
///
/// # Errors
///
/// Unsupported, if the pattern doesn't parse, uses something not
/// supported or is too large to compile.
pub fn compile_pattern(pattern: &str) -> Result<Dfa> {
    let tree = match ast::parse::Parser::new().parse(pattern) {
        Ok(t) => t,
        Err(e) => {
            return unsupported(format!(
                "matchPattern {}: {}",
                repr(pattern),
                e.kind()
            ))
        }
    };
    let mut items: Vec<Ast> = match &tree {
        Ast::Concat(c) => c.asts.clone(),
        t => vec![t.clone()],
    };
    let mut ignore = false;
    if let Some(Ast::Flags(f)) = items.first() {
        let only_i = f.flags.items.iter().all(|i| {
            matches!(
                i.kind,
                ast::FlagsItemKind::Flag(ast::Flag::CaseInsensitive)
            )
        });
        if !only_i || f.flags.items.is_empty() {
            return unsupported(format!(
                "matchPattern {}: flags other than (?i)",
                repr(pattern)
            ));
        }
        ignore = true;
        items.remove(0);
    }
    let mut b = Builder {
        pattern,
        ignore,
        nfa: Nfa::default(),
    };
    let (start, end) = b.seq(&items, true, true)?;
    determinize(&b.nfa, start, end, pattern)
}

/// Turns an NFA into a minimal DFA by the subset construction.
///
/// # Arguments
///
/// * `nfa`     - The automaton.
/// * `start`   - Its start state.
/// * `final_`  - Its accepting state.
/// * `pattern` - The pattern it came from, for messages.
///
/// # Returns
///
/// The minimal deterministic automaton.
///
/// # Errors
///
/// Unsupported, if the DFA would have more than [`MAX_STATES`] states.
fn determinize(
    nfa: &Nfa,
    start: usize,
    final_: usize,
    pattern: &str,
) -> Result<Dfa> {
    let closure = |qs: &[usize]| -> BTreeSet<usize> {
        let mut seen: BTreeSet<usize> = qs.iter().copied().collect();
        let mut todo: Vec<usize> = qs.to_vec();
        while let Some(q) = todo.pop() {
            for &r in &nfa.eps[q] {
                if seen.insert(r) {
                    todo.push(r);
                }
            }
        }
        seen
    };
    let first = closure(&[start]);
    let mut index: HashMap<BTreeSet<usize>, usize> = HashMap::new();
    index.insert(first.clone(), 0);
    let mut order = vec![first];
    let mut table: Vec<Vec<(u32, u32, usize)>> = Vec::new();
    let mut k = 0;
    while k < order.len() {
        let qs = order[k].clone();
        k += 1;
        let edges: Vec<(u32, u32, usize)> = qs
            .iter()
            .flat_map(|&q| nfa.edges[q].iter())
            .map(|&(a, b, t)| (a as u32, b as u32, t))
            .collect();
        let cuts: BTreeSet<u32> =
            edges.iter().flat_map(|&(a, b, _)| [a, b + 1]).collect();
        let cuts: Vec<u32> = cuts.into_iter().collect();
        let mut row: Vec<(u32, u32, usize)> = Vec::new();
        for w in cuts.windows(2) {
            let (a, b) = (w[0], w[1]);
            let targets: Vec<usize> = edges
                .iter()
                .filter(|&&(lo, hi, _)| lo <= a && b - 1 <= hi)
                .map(|&(_, _, t)| t)
                .collect();
            let dest = closure(&targets);
            if dest.is_empty() {
                continue;
            }
            let nxt = match index.get(&dest) {
                Some(&n) => n,
                None => {
                    if order.len() >= MAX_STATES {
                        return unsupported(format!(
                            "matchPattern {} is too large to compile",
                            repr(pattern)
                        ));
                    }
                    let n = order.len();
                    index.insert(dest.clone(), n);
                    order.push(dest);
                    n
                }
            };
            match row.last_mut() {
                Some(last) if last.2 == nxt && last.1 + 1 == a => {
                    last.1 = b - 1
                }
                _ => row.push((a, b - 1, nxt)),
            }
        }
        table.push(row);
    }
    let accept: Vec<bool> =
        order.iter().map(|qs| qs.contains(&final_)).collect();
    Ok(minimize(&table, &accept, pattern))
}

/// Finds the smallest automaton equivalent to a table by refining the
/// partition of its states (Moore's algorithm).
///
/// # Arguments
///
/// * `table`   - Each state's transitions, as sorted (lo, hi, next) byte
///               ranges; state 0 is the start.
/// * `accept`  - Whether each state accepts.
/// * `pattern` - The pattern the automaton matches.
///
/// # Returns
///
/// The minimal automaton, its states numbered in the order they are first
/// reached from the start, unreachable ones dropped.
fn minimize(
    table: &[Vec<(u32, u32, usize)>],
    accept: &[bool],
    pattern: &str,
) -> Dfa {
    let mut cls: Vec<usize> = accept.iter().map(|&a| a as usize).collect();
    loop {
        let mut sigs: HashMap<(usize, Vec<(u32, u32, usize)>), usize> =
            HashMap::new();
        let mut new = Vec::new();
        for (q, row) in table.iter().enumerate() {
            let mut merged: Vec<(u32, u32, usize)> = Vec::new();
            for &(lo, hi, nxt) in row {
                let c = cls[nxt];
                match merged.last_mut() {
                    Some(last) if last.2 == c && last.1 + 1 == lo => {
                        last.1 = hi
                    }
                    _ => merged.push((lo, hi, c)),
                }
            }
            let n = sigs.len();
            new.push(*sigs.entry((cls[q], merged)).or_insert(n));
        }
        let distinct: BTreeSet<usize> = cls.iter().copied().collect();
        if sigs.len() == distinct.len() {
            break;
        }
        cls = new;
    }
    // Number the classes in the order they are first reached, start first.
    let mut number: HashMap<usize, usize> = HashMap::new();
    number.insert(cls[0], 0);
    let mut order = vec![0];
    let mut k = 0;
    while k < order.len() {
        let q = (0..cls.len())
            .find(|&i| number.get(&cls[i]) == Some(&order[k]))
            .expect("a state of each class");
        k += 1;
        for &(_, _, nxt) in &table[q] {
            if !number.contains_key(&cls[nxt]) {
                let n = number.len();
                number.insert(cls[nxt], n);
                order.push(n);
            }
        }
    }
    let mut reps: HashMap<usize, usize> = HashMap::new();
    for (q, c) in cls.iter().enumerate() {
        if let Some(&n) = number.get(c) {
            reps.entry(n).or_insert(q);
        }
    }
    let mut starts = vec![0i64];
    let mut trans: Vec<(i64, i64, i64)> = Vec::new();
    let mut acc = Vec::new();
    for n in 0..number.len() {
        let q = reps[&n];
        for &(lo, hi, nxt) in &table[q] {
            let m = number[&cls[nxt]] as i64;
            // Only a transition of this state's may be extended.
            let own = trans.len() as i64 > *starts.last().unwrap();
            match trans.last_mut() {
                Some(last) if own && last.2 == m && last.1 + 1 == lo as i64 => {
                    last.1 = hi as i64
                }
                _ => trans.push((lo as i64, hi as i64, m)),
            }
        }
        starts.push(trans.len() as i64);
        acc.push(accept[q]);
    }
    Dfa {
        pattern: pattern.into(),
        starts,
        trans,
        accept: acc,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Runs an automaton over a string, as the runtimes do.
    ///
    /// # Arguments
    ///
    /// * `dfa` - The automaton.
    /// * `s`   - The string.
    ///
    /// # Returns
    ///
    /// Whether the whole of it matches.
    fn matches(dfa: &Dfa, s: &str) -> bool {
        let mut q = 0usize;
        for &b in s.as_bytes() {
            let lo = dfa.starts[q] as usize;
            let hi = dfa.starts[q + 1] as usize;
            match dfa.trans[lo..hi]
                .iter()
                .find(|t| t.0 <= b as i64 && b as i64 <= t.1)
            {
                Some(t) => q = t.2 as usize,
                None => return false,
            }
        }
        dfa.accept[q]
    }

    #[test]
    fn patterns_match_whole_strings() {
        let cases: &[(&str, &[&str], &[&str])] = &[
            ("OK", &["OK"], &["ok", "OKK", ""]),
            ("ERR.*", &["ERR", "ERROR"], &["ER", "xERR"]),
            ("(?i)ok\\d*", &["ok", "OK7", "oK12"], &["ok7a", "k"]),
            ("[A-Z]{2,4}\\d+", &["AB1", "ABCD99"], &["A1", "ABCDE1"]),
            ("^a+b?$", &["a", "aab"], &["b", "abb"]),
            (".é.", &["xéy"], &["xey", "é"]),
            ("[α-ω]+", &["αβω"], &["", "a"]),
            ("(?i)[^a]", &["b", "B"], &["a", "A"]),
            ("", &[""], &["a"]),
        ];
        for (p, yes, no) in cases {
            let d = compile_pattern(p).unwrap();
            for s in *yes {
                assert!(matches(&d, s), "{p} should match {s:?}");
            }
            for s in *no {
                assert!(!matches(&d, s), "{p} should not match {s:?}");
            }
        }
    }

    #[test]
    fn automata_are_minimal() {
        assert_eq!(compile_pattern("(a|b)*abb").unwrap().accept.len(), 4);
        assert_eq!(compile_pattern("a*?b").unwrap().accept.len(), 2);
    }

    #[test]
    fn what_is_not_regular_is_refused() {
        for p in ["(a)\\1", "a(?=b)", "(?<=a)b", "a{30000}", "a\\bb", "(?i:a)"]
        {
            assert!(compile_pattern(p).is_err(), "{p}");
        }
    }
}
