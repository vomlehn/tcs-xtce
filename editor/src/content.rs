//! What children an element may have, as an automaton.
//!
//! A complex type's content -- sequences and choices of elements, each
//! occurring some number of times -- is a regular language over element
//! names, and it is held here as a nondeterministic automaton built from it.
//! Three questions are asked of it:
//!
//! * whether a list of children is one the type allows, and which
//!   declaration each child is of, which is what gives each child its own
//!   type in turn;
//! * which elements may be put in at a place among the children so that
//!   the whole list is still allowed, which is how the editor offers only
//!   what the schema allows;
//! * the shortest list of children the type allows, which is what a new
//!   element is given so that it is complete as it is made.
//!
//! The XTCE schemas obey XML Schema's rule that a content model is
//! unambiguous, so a child's declaration is settled by the children before
//! it; the automaton does not depend on that to accept, only to type.

use std::collections::{BTreeSet, VecDeque};

use crate::schema::{Occurs, Particle, Schema};

/// One state of an automaton.
#[derive(Debug, Clone, Default)]
struct State {
    /// The states reached from this one without reading a child.
    eps: Vec<usize>,
    /// The states reached by reading a child, each with the index in
    /// [`Schema::elements`] of the declaration the child is of.
    edges: Vec<(usize, usize)>,
}

/// The automaton of one complex type's content.
#[derive(Debug, Clone)]
pub struct Automaton {
    /// Every state; a state's index is its number.
    states: Vec<State>,
    /// Where reading starts.
    start: usize,
    /// The one state that accepts.
    accept: usize,
}

/// A set of states, kept closed under moves that read nothing.
pub type States = BTreeSet<usize>;

impl Automaton {
    /// Builds the automaton of a content model.
    ///
    /// # Arguments
    ///
    /// * `content` - The content model, or `None` for a type that has no
    ///               child elements.
    ///
    /// # Returns
    ///
    /// The automaton, which accepts exactly the lists of children
    /// `content` allows.
    pub fn of(content: Option<&Particle>) -> Automaton {
        let mut a = Automaton {
            states: Vec::new(),
            start: 0,
            accept: 0,
        };
        let (s, e) = match content {
            Some(p) => a.particle(p),
            None => {
                let s = a.state();
                (s, s)
            }
        };
        a.start = s;
        a.accept = e;
        a
    }

    /// Adds a state.
    ///
    /// # Returns
    ///
    /// The new state's number.
    fn state(&mut self) -> usize {
        self.states.push(State::default());
        self.states.len() - 1
    }

    /// Adds the states of a particle, as many times as it may occur.
    ///
    /// # Arguments
    ///
    /// * `p` - The particle.
    ///
    /// # Returns
    ///
    /// The states the particle starts at and ends at.
    fn particle(&mut self, p: &Particle) -> (usize, usize) {
        let occurs = match p {
            Particle::Element(_, o)
            | Particle::Sequence(_, o)
            | Particle::Choice(_, o) => *o,
        };
        self.repeat(p, occurs)
    }

    /// Adds the states of one occurrence of a particle.
    ///
    /// # Arguments
    ///
    /// * `p` - The particle.
    ///
    /// # Returns
    ///
    /// The states the occurrence starts at and ends at.
    fn once(&mut self, p: &Particle) -> (usize, usize) {
        match p {
            Particle::Element(d, _) => {
                let s = self.state();
                let e = self.state();
                self.states[s].edges.push((*d, e));
                (s, e)
            }
            Particle::Sequence(parts, _) => {
                let s = self.state();
                let mut at = s;
                for part in parts {
                    let (ps, pe) = self.particle(part);
                    self.states[at].eps.push(ps);
                    at = pe;
                }
                (s, at)
            }
            Particle::Choice(parts, _) => {
                let s = self.state();
                let e = self.state();
                for part in parts {
                    let (ps, pe) = self.particle(part);
                    self.states[s].eps.push(ps);
                    self.states[pe].eps.push(e);
                }
                (s, e)
            }
        }
    }

    /// Adds the states of a particle repeated as `occurs` says: `min`
    /// occurrences in a row, then up to `max - min` more, each of which may
    /// be skipped, or any number more when there is no `max`.
    ///
    /// # Arguments
    ///
    /// * `p`      - The particle.
    /// * `occurs` - How many times it may occur.
    ///
    /// # Returns
    ///
    /// The states the repetition starts at and ends at.
    fn repeat(&mut self, p: &Particle, occurs: Occurs) -> (usize, usize) {
        let s = self.state();
        let mut at = s;
        for _ in 0..occurs.min {
            let (os, oe) = self.once(p);
            self.states[at].eps.push(os);
            at = oe;
        }
        match occurs.max {
            None => {
                let (os, oe) = self.once(p);
                self.states[at].eps.push(os);
                self.states[oe].eps.push(os);
                let e = self.state();
                self.states[at].eps.push(e);
                self.states[oe].eps.push(e);
                (s, e)
            }
            Some(max) => {
                let e = self.state();
                for _ in occurs.min..max {
                    let (os, oe) = self.once(p);
                    self.states[at].eps.push(os);
                    self.states[at].eps.push(e);
                    at = oe;
                }
                self.states[at].eps.push(e);
                (s, e)
            }
        }
    }

    /// Adds to a set every state reached from it without reading a child.
    ///
    /// # Arguments
    ///
    /// * `set` - The states, which are added to.
    fn close(&self, set: &mut States) {
        let mut work: Vec<usize> = set.iter().copied().collect();
        while let Some(s) = work.pop() {
            for &n in &self.states[s].eps {
                if set.insert(n) {
                    work.push(n);
                }
            }
        }
    }

    /// The states before any child is read.
    ///
    /// # Returns
    ///
    /// The start state and those reached from it without reading.
    pub fn initial(&self) -> States {
        let mut set = States::from([self.start]);
        self.close(&mut set);
        set
    }

    /// Reads one child.
    ///
    /// # Arguments
    ///
    /// * `schema` - The schema the automaton was built from, which names
    ///              the declarations.
    /// * `from`   - The states before the child.
    /// * `name`   - The child's name, without its prefix.
    ///
    /// # Returns
    ///
    /// The states after it, empty if the child is not allowed there, and
    /// the declaration it is of, if it is allowed.
    pub fn step(
        &self,
        schema: &Schema,
        from: &States,
        name: &str,
    ) -> (States, Option<usize>) {
        let mut next = States::new();
        let mut decl = None;
        for &s in from {
            for &(d, to) in &self.states[s].edges {
                if schema.elements[d].name == name {
                    next.insert(to);
                    decl.get_or_insert(d);
                }
            }
        }
        self.close(&mut next);
        (next, decl)
    }

    /// Whether a set of states includes the accepting one.
    ///
    /// # Arguments
    ///
    /// * `set` - The states.
    ///
    /// # Returns
    ///
    /// True if the children read so far are a whole list the type allows.
    pub fn accepts(&self, set: &States) -> bool {
        set.contains(&self.accept)
    }

    /// Reads a list of children.
    ///
    /// # Arguments
    ///
    /// * `schema` - The schema the automaton was built from.
    /// * `from`   - The states before the first child.
    /// * `names`  - The children's names, without their prefixes.
    ///
    /// # Returns
    ///
    /// The states after them, empty if they are not allowed.
    pub fn run<'a>(
        &self,
        schema: &Schema,
        from: &States,
        names: impl IntoIterator<Item = &'a str>,
    ) -> States {
        let mut at = from.clone();
        for n in names {
            at = self.step(schema, &at, n).0;
            if at.is_empty() {
                break;
            }
        }
        at
    }

    /// The declarations a child may be of when read from a set of states.
    ///
    /// # Arguments
    ///
    /// * `from` - The states.
    ///
    /// # Returns
    ///
    /// The declarations' indexes, without repeats, in the order the
    /// schema gives them.
    pub fn next_decls(&self, from: &States) -> Vec<usize> {
        let mut out = Vec::new();
        for &s in from {
            for &(d, _) in &self.states[s].edges {
                if !out.contains(&d) {
                    out.push(d);
                }
            }
        }
        out
    }

    /// The fewest children that take a set of states to acceptance.
    ///
    /// # Arguments
    ///
    /// * `from` - The states.
    ///
    /// # Returns
    ///
    /// The declarations of those children, in order, or `None` if no list
    /// of children reaches acceptance from there.
    pub fn shortest(&self, from: &States) -> Option<Vec<usize>> {
        // Breadth first over single states, each move reading one child or
        // none: moves that read none cost nothing, so they go to the front.
        let mut prev: Vec<Option<(usize, Option<usize>)>> =
            vec![None; self.states.len()];
        let mut seen = vec![false; self.states.len()];
        let mut queue = VecDeque::new();
        for &s in from {
            seen[s] = true;
            queue.push_back(s);
        }
        let mut end = None;
        while let Some(s) = queue.pop_front() {
            if s == self.accept {
                end = Some(s);
                break;
            }
            for &n in &self.states[s].eps {
                if !seen[n] {
                    seen[n] = true;
                    prev[n] = Some((s, None));
                    queue.push_front(n);
                }
            }
            for &(d, n) in &self.states[s].edges {
                if !seen[n] {
                    seen[n] = true;
                    prev[n] = Some((s, Some(d)));
                    queue.push_back(n);
                }
            }
        }
        let mut at = end?;
        let mut out = Vec::new();
        while let Some((p, d)) = prev[at] {
            if let Some(d) = d {
                out.push(d);
            }
            at = p;
        }
        out.reverse();
        Some(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::Version;

    /// Builds the automaton of a named complex type of XTCE 1.3.
    ///
    /// # Arguments
    ///
    /// * `name` - The type's name.
    ///
    /// # Returns
    ///
    /// The schema and the automaton.
    fn of(name: &str) -> (&'static Schema, Automaton) {
        let s = Version::V1_3.schema();
        let ct = s.complex_type(name).expect("a type of that name");
        (s, Automaton::of(ct.content.as_ref()))
    }

    #[test]
    fn accepts_what_the_type_allows() {
        let (s, a) = of("SpaceSystemType");
        let i = a.initial();
        assert!(a.accepts(&a.run(s, &i, [])));
        assert!(a.accepts(&a.run(s, &i, ["Header", "TelemetryMetaData"])));
        assert!(a.accepts(&a.run(
            s,
            &i,
            ["TelemetryMetaData", "CommandMetaData", "SpaceSystem"]
        )));
        assert!(!a.accepts(&a.run(s, &i, ["TelemetryMetaData", "Header"])));
        assert!(a.run(s, &i, ["Bogus"]).is_empty());
    }

    #[test]
    fn finds_the_shortest_complete_content() {
        let (s, a) = of("SequenceContainerType");
        let need = a.shortest(&a.initial()).unwrap();
        let names: Vec<&str> =
            need.iter().map(|&d| s.elements[d].name.as_str()).collect();
        assert_eq!(names, ["EntryList"]);
    }
}
