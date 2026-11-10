//! Making new elements that are complete as they are made.
//!
//! The editor never lets a document hold an element its version's schema
//! does not allow, so an element is put in with everything the schema
//! requires of it already there: its required attributes, each with a
//! value of the right kind, and the fewest children that complete it, each
//! made the same way.  The values are placeholders a person then changes,
//! chosen to be valid: an enumeration's first value, a number, a name not
//! yet used.
//!
//! The same code, steered by a random number generator rather than always
//! taking the shortest way, makes whole documents of any version, which is
//! how the converter is tested on versions no shipped document is in.

use crate::content::States;
use crate::schema::{Builtin, Schema, SimpleRef, TypeRef};
use crate::tree::{Attr, Element, Id, Node};

/// What new elements are made with: where their ids and names come from.
#[derive(Debug)]
pub struct Maker<'a> {
    /// The schema the elements are of.
    pub schema: &'a Schema,
    /// The prefix the version's namespace is written with, `""` for none.
    pub prefix: String,
    /// The last id given out; the next element made has the one after.
    pub last_id: Id,
    /// How many names have been made, so each new one differs.
    pub names: u64,
    /// The random choices, when making samples; `None` makes each element
    /// as small as it can be.
    pub random: Option<Random>,
    /// How deep in the document the element being made is, so that a
    /// sample stops growing.
    depth: usize,
}

/// A small generator of random numbers, the same on every machine, so a
/// test that fails can be run again exactly.
#[derive(Debug, Clone)]
pub struct Random {
    /// Its state.
    state: u64,
}

impl Random {
    /// Makes a generator.
    ///
    /// # Arguments
    ///
    /// * `seed` - Where its sequence starts.
    ///
    /// # Returns
    ///
    /// The generator.
    pub fn new(seed: u64) -> Random {
        Random {
            state: seed.wrapping_mul(6364136223846793005).wrapping_add(1),
        }
    }

    /// Gives a number below a bound.
    ///
    /// # Arguments
    ///
    /// * `below` - The bound, at least 1.
    ///
    /// # Returns
    ///
    /// A number from 0 to `below - 1`.
    pub fn below(&mut self, below: usize) -> usize {
        // A 64-bit linear congruential generator, its high bits used.
        self.state = self
            .state
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        ((self.state >> 33) as usize) % below.max(1)
    }
}

impl<'a> Maker<'a> {
    /// Makes a maker of minimal elements.
    ///
    /// # Arguments
    ///
    /// * `schema`  - The schema.
    /// * `prefix`  - The namespace prefix to write names with, `""` for
    ///               none.
    /// * `last_id` - The last id the document has given out.
    ///
    /// # Returns
    ///
    /// The maker.
    pub fn new(schema: &'a Schema, prefix: &str, last_id: Id) -> Maker<'a> {
        Maker {
            schema,
            prefix: prefix.to_string(),
            last_id,
            names: 0,
            random: None,
            depth: 0,
        }
    }

    /// Makes an element of a declaration, complete.
    ///
    /// # Arguments
    ///
    /// * `decl` - The declaration, by its index in [`Schema::elements`].
    ///
    /// # Returns
    ///
    /// The element, with the required attributes and children it needs,
    /// or `None` if the schema allows no complete element of it.
    pub fn element(&mut self, decl: usize) -> Option<Element> {
        if self.depth > 60 {
            return None;
        }
        self.last_id += 1;
        let d = &self.schema.elements[decl];
        let name = if self.prefix.is_empty() {
            d.name.clone()
        } else {
            format!("{}:{}", self.prefix, d.name)
        };
        let mut e = Element::new(self.last_id, &name);
        match d.ty {
            TypeRef::Simple(t) => {
                let v = match &d.fixed {
                    Some(f) => f.clone(),
                    None => self.value(t, &d.name)?,
                };
                set_text(&mut e, &v);
            }
            TypeRef::Complex(ci) => {
                let ct = &self.schema.complex[ci];
                if ct.is_abstract {
                    return None;
                }
                for a in &ct.attrs {
                    let wanted = a.required || self.chance(4);
                    if !wanted || a.name.contains(':') {
                        continue;
                    }
                    let v = match &a.fixed {
                        Some(f) => f.clone(),
                        None => self.value(a.ty, &a.name)?,
                    };
                    e.attrs.push(Attr::new(&a.name, &v));
                }
                if let Some(t) = ct.text {
                    let v = match &d.fixed {
                        Some(f) => f.clone(),
                        None => self.value(t, &d.name)?,
                    };
                    set_text(&mut e, &v);
                } else {
                    self.depth += 1;
                    let kids = self.children(ci);
                    self.depth -= 1;
                    for k in kids? {
                        e.children.push(Node::Element(k));
                    }
                    e.empty = e.children.is_empty();
                }
            }
        }
        Some(e)
    }

    /// Whether to take an optional choice, when making samples.
    ///
    /// # Arguments
    ///
    /// * `one_in` - The odds: one in this many.
    ///
    /// # Returns
    ///
    /// True with those odds when making samples, and never otherwise.
    fn chance(&mut self, one_in: usize) -> bool {
        match &mut self.random {
            Some(r) => r.below(one_in) == 0,
            None => false,
        }
    }

    /// Makes the children of an element of a complex type: the fewest
    /// that complete it, or a random list the type allows when making
    /// samples.
    ///
    /// # Arguments
    ///
    /// * `ci` - The type, by its index in [`Schema::complex`].
    ///
    /// # Returns
    ///
    /// The children, or `None` if one of them cannot be made.
    fn children(&mut self, ci: usize) -> Option<Vec<Element>> {
        let a = self.schema.automaton(ci);
        let mut at: States = a.initial();
        let mut out = Vec::new();
        // A sample wanders, taking a random allowed child while it is
        // shallow and short, then completes the list the shortest way.
        let wander = self.random.is_some() && self.depth < 8;
        if wander {
            for _ in 0..6 {
                let next = a.next_decls(&at);
                if next.is_empty() || (a.accepts(&at) && self.chance(2)) {
                    break;
                }
                let r = self.random.as_mut().expect("wandering");
                let d = next[r.below(next.len())];
                let Some(k) = self.element(d) else {
                    continue;
                };
                at = a.step(self.schema, &at, &self.schema.elements[d].name).0;
                out.push(k);
            }
        }
        for d in a.shortest(&at)? {
            out.push(self.element(d)?);
        }
        Some(out)
    }

    /// Makes a value of a simple type.
    ///
    /// # Arguments
    ///
    /// * `ty`   - The type.
    /// * `what` - The name of the attribute or element the value is for,
    ///            which says whether it is a name and must differ from
    ///            others.
    ///
    /// # Returns
    ///
    /// A value the type allows, or `None` if none of those tried is.
    pub fn value(&mut self, ty: SimpleRef, what: &str) -> Option<String> {
        let mut tries: Vec<String> = Vec::new();
        let options = self.schema.enumeration(ty);
        if !options.is_empty() {
            let i = match &mut self.random {
                Some(r) => r.below(options.len()),
                None => 0,
            };
            tries.push(options[i].clone());
        }
        if what == "name" || what.ends_with("Name") {
            self.names += 1;
            tries.push(format!("new{}", self.names));
        }
        if what.ends_with("Ref") {
            tries.push("new".to_string());
        }
        tries.extend(
            [
                "1",
                "8",
                "0",
                "true",
                "00",
                "PT1S",
                "2026-01-01",
                "2026-01-01T00:00:00Z",
                "1.0",
                "0,1",
                "0x0",
                "a",
                "",
            ]
            .map(str::to_string),
        );
        let _ = Builtin::String;
        tries
            .into_iter()
            .find(|v| self.schema.check_value(ty, v).is_ok())
    }
}

/// Gives an element text and nothing else.
///
/// # Arguments
///
/// * `e` - The element.
/// * `v` - The text, unescaped.
fn set_text(e: &mut Element, v: &str) {
    e.children = if v.is_empty() {
        Vec::new()
    } else {
        vec![Node::Text(crate::tree::escape(v, '\0'))]
    };
    e.empty = e.children.is_empty();
}

/// Makes a whole document of a version at random, valid for its schema.
///
/// The schemas' keys pick out more than they mean to -- 1.2's message key
/// takes in a MessageSet's LongDescription, which has no name -- so a
/// document a key rejects is made again from the next choices.
///
/// # Arguments
///
/// * `version` - The version.
/// * `seed`    - Where the random choices start.
///
/// # Returns
///
/// The document's text.
///
/// # Panics
///
/// Panics if a hundred tries make nothing valid, which would be a mistake
/// in the maker.
pub fn sample(version: crate::schema::Version, seed: u64) -> String {
    let schema = version.schema();
    let mut m = Maker::new(schema, "", 0);
    m.random = Some(Random::new(seed));
    for _ in 0..100 {
        let Some(mut root) = m.element(schema.root) else {
            continue;
        };
        root.attrs
            .insert(0, Attr::new("xmlns", version.namespace()));
        crate::edit::lay_out(&mut root, "", "  ");
        let mut out =
            String::from("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
        crate::tree::write_node(&Node::Element(root), &mut out);
        out.push('\n');
        let ok = crate::tree::Document::parse(&out)
            .map(|d| !crate::validate::check(&d).has_errors())
            .unwrap_or(false);
        if ok {
            return out;
        }
    }
    panic!("no valid XTCE {version} sample from seed {seed}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::Version;
    use crate::tree::Document;
    use crate::validate::check;

    #[test]
    fn makes_complete_elements() {
        let s = Version::V1_3.schema();
        let mut m = Maker::new(s, "xtce", 0);
        let root = m.element(s.root).unwrap();
        let mut root = root;
        root.attrs
            .insert(0, Attr::new("xmlns:xtce", Version::V1_3.namespace()));
        let mut text = String::new();
        crate::tree::write_node(&Node::Element(root), &mut text);
        let doc = Document::parse(&text).unwrap();
        let r = check(&doc);
        assert!(!r.has_errors(), "{:?}", r.problems);
    }

    #[test]
    fn makes_valid_samples_of_every_version() {
        for v in Version::ALL {
            for seed in 0..15 {
                let text = sample(v, seed);
                let doc = Document::parse(&text).unwrap();
                let r = check(&doc);
                assert!(
                    !r.has_errors(),
                    "{v} seed {seed}: {:?}\n{text}",
                    r.problems.first()
                );
            }
        }
    }
}
