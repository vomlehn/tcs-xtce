//! The editor's smaller tools: comparing what two documents mean, their
//! digest, ready-made parts to put in, and where the specification
//! describes an element.

use std::collections::BTreeMap;

use crate::convert::{convert, Answers};
use crate::schema::Version;
use crate::tree::{Document, Element};

/// An element reduced to what it means, as the digest reads it: no
/// comments, no layout, attributes in name order, no namespace
/// declarations, and the children of unordered sets in name order.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Meaning {
    /// The element's name, without prefix.
    name: String,
    /// Its attributes, by name, without namespace declarations.
    attrs: BTreeMap<String, String>,
    /// Its text, trimmed.
    text: String,
    /// Its child elements, in order, or in name order for a set.
    children: Vec<Meaning>,
}

impl Meaning {
    /// Reduces an element to its meaning.
    ///
    /// # Arguments
    ///
    /// * `e` - The element.
    ///
    /// # Returns
    ///
    /// Its meaning.
    fn of(e: &Element) -> Meaning {
        let mut children: Vec<Meaning> =
            e.elements().map(Meaning::of).collect();
        if tcs_xtce::digest::UNORDERED.contains(&e.local()) {
            children.sort_by_key(Meaning::key);
        }
        Meaning {
            name: e.local().to_string(),
            attrs: e
                .attrs
                .iter()
                .filter(|a| !crate::validate::is_namespace_attr(&a.name))
                .map(|a| (a.name.clone(), a.value()))
                .collect(),
            text: e.text().trim().to_string(),
            children,
        }
    }

    /// What names this element among its siblings: its element name and
    /// its `name` attribute.
    ///
    /// # Returns
    ///
    /// The key, as `Parameter[p]` or `Header`.
    fn key(&self) -> String {
        match self.attrs.get("name") {
            Some(n) => format!("{}[{n}]", self.name),
            None => self.name.clone(),
        }
    }
}

/// Says how two documents differ in what they mean, ignoring comments,
/// layout, attribute order and prefixes.
///
/// # Arguments
///
/// * `old` - The first document.
/// * `new` - The second.
///
/// # Returns
///
/// Each difference, as a line saying where and what; none if they mean
/// the same.
pub fn diff(old: &Document, new: &Document) -> Vec<String> {
    let mut out = Vec::new();
    let a = Meaning::of(old.root());
    let b = Meaning::of(new.root());
    diff_into(&a, &b, &a.key(), &mut out);
    let (va, vb) = (Version::of_document(old), Version::of_document(new));
    if va != vb {
        out.insert(
            0,
            format!(
                "the versions differ: {} and {}",
                va.map_or("?".into(), |v| v.to_string()),
                vb.map_or("?".into(), |v| v.to_string())
            ),
        );
    }
    out
}

/// Adds the differences between two elements to a list.
///
/// # Arguments
///
/// * `a`    - The first element.
/// * `b`    - The second, at the same place.
/// * `path` - Where they are.
/// * `out`  - The list.
fn diff_into(a: &Meaning, b: &Meaning, path: &str, out: &mut Vec<String>) {
    for (k, v) in &a.attrs {
        match b.attrs.get(k) {
            None => out.push(format!("{path}: @{k}=\"{v}\" taken out")),
            Some(w) if w != v => {
                out.push(format!("{path}: @{k} \"{v}\" -> \"{w}\""))
            }
            _ => {}
        }
    }
    for (k, w) in &b.attrs {
        if !a.attrs.contains_key(k) {
            out.push(format!("{path}: @{k}=\"{w}\" added"));
        }
    }
    if a.text != b.text {
        out.push(format!("{path}: text {:?} -> {:?}", a.text, b.text));
    }
    // The children are matched by key, in order: a longest common
    // subsequence, so that one element put in or taken out is reported as
    // that and not as every element after it changing.
    let ka: Vec<String> = a.children.iter().map(Meaning::key).collect();
    let kb: Vec<String> = b.children.iter().map(Meaning::key).collect();
    let (n, m) = (ka.len(), kb.len());
    let mut l = vec![vec![0usize; m + 1]; n + 1];
    for i in (0..n).rev() {
        for j in (0..m).rev() {
            l[i][j] = if ka[i] == kb[j] {
                l[i + 1][j + 1] + 1
            } else {
                l[i + 1][j].max(l[i][j + 1])
            };
        }
    }
    let (mut i, mut j) = (0, 0);
    while i < n || j < m {
        if i < n && j < m && ka[i] == kb[j] {
            let p = format!("{path}/{}", ka[i]);
            diff_into(&a.children[i], &b.children[j], &p, out);
            i += 1;
            j += 1;
        } else if j < m && (i == n || l[i][j + 1] >= l[i + 1][j]) {
            out.push(format!("{path}/{}: added", kb[j]));
            j += 1;
        } else {
            out.push(format!("{path}/{}: taken out", ka[i]));
            i += 1;
        }
    }
}

/// The digest of what a document means, as `xtce-digest` computes it.
///
/// # Arguments
///
/// * `doc` - The document.
///
/// # Returns
///
/// The digest in hexadecimal.
///
/// # Errors
///
/// Why it could not be computed.
pub fn digest(doc: &Document) -> Result<String, String> {
    tcs_xtce::digest::digest(doc.write().as_bytes()).map_err(|e| e.to_string())
}

/// A ready-made part to put in a document.
#[derive(Debug, Clone, Copy)]
pub struct Template {
    /// What it is called in the Insert menu.
    pub title: &'static str,
    /// What it is, for the menu's tooltip.
    pub about: &'static str,
    /// The element it goes in, without prefix.
    pub parent: &'static str,
    /// The part, as XTCE 1.3, in the default namespace.
    xml: &'static str,
}

/// The ready-made parts.  Each is written once, in XTCE 1.3, and converted
/// for a document of an older version as it is put in.
pub const TEMPLATES: &[Template] = &[
    Template {
        title: "Unsigned 8-bit integer type",
        about: "A whole number from 0 to 255, in one byte.",
        parent: "ParameterTypeSet",
        xml: "<IntegerParameterType name=\"uint8\" signed=\"false\">\
              <IntegerDataEncoding sizeInBits=\"8\"/>\
              </IntegerParameterType>",
    },
    Template {
        title: "Unsigned 16-bit integer type",
        about: "A whole number from 0 to 65535, most significant byte first.",
        parent: "ParameterTypeSet",
        xml: "<IntegerParameterType name=\"uint16\" signed=\"false\">\
              <IntegerDataEncoding sizeInBits=\"16\"/>\
              </IntegerParameterType>",
    },
    Template {
        title: "Signed 32-bit integer type",
        about: "A whole number in two's complement, in four bytes.",
        parent: "ParameterTypeSet",
        xml: "<IntegerParameterType name=\"int32\" signed=\"true\">\
              <IntegerDataEncoding sizeInBits=\"32\" \
              encoding=\"twosComplement\"/></IntegerParameterType>",
    },
    Template {
        title: "32-bit float type",
        about: "An IEEE 754 single-precision number.",
        parent: "ParameterTypeSet",
        xml: "<FloatParameterType name=\"float32\" sizeInBits=\"32\">\
              <FloatDataEncoding sizeInBits=\"32\"/></FloatParameterType>",
    },
    Template {
        title: "Boolean type",
        about: "True or false, in one bit.",
        parent: "ParameterTypeSet",
        xml: "<BooleanParameterType name=\"flag\">\
              <IntegerDataEncoding sizeInBits=\"1\"/>\
              </BooleanParameterType>",
    },
    Template {
        title: "CCSDS space packet primary header",
        about: "A SpaceSystem holding the six-byte primary header of CCSDS \
                133.0-B, Space Packet Protocol: version, type, secondary \
                header flag, APID, sequence flags, sequence count and data \
                length, and the abstract container a packet builds on.",
        parent: "SpaceSystem",
        xml: "<SpaceSystem name=\"CCSDS\"><TelemetryMetaData>\
              <ParameterTypeSet>\
              <IntegerParameterType name=\"uint1\" signed=\"false\">\
              <IntegerDataEncoding sizeInBits=\"1\"/></IntegerParameterType>\
              <IntegerParameterType name=\"uint2\" signed=\"false\">\
              <IntegerDataEncoding sizeInBits=\"2\"/></IntegerParameterType>\
              <IntegerParameterType name=\"uint3\" signed=\"false\">\
              <IntegerDataEncoding sizeInBits=\"3\"/></IntegerParameterType>\
              <IntegerParameterType name=\"uint11\" signed=\"false\">\
              <IntegerDataEncoding sizeInBits=\"11\"/></IntegerParameterType>\
              <IntegerParameterType name=\"uint14\" signed=\"false\">\
              <IntegerDataEncoding sizeInBits=\"14\"/></IntegerParameterType>\
              <IntegerParameterType name=\"uint16\" signed=\"false\">\
              <IntegerDataEncoding sizeInBits=\"16\"/></IntegerParameterType>\
              </ParameterTypeSet><ParameterSet>\
              <Parameter name=\"Version\" parameterTypeRef=\"uint3\"/>\
              <Parameter name=\"Type\" parameterTypeRef=\"uint1\"/>\
              <Parameter name=\"SecHdrFlag\" parameterTypeRef=\"uint1\"/>\
              <Parameter name=\"APID\" parameterTypeRef=\"uint11\"/>\
              <Parameter name=\"SeqFlags\" parameterTypeRef=\"uint2\"/>\
              <Parameter name=\"SeqCount\" parameterTypeRef=\"uint14\"/>\
              <Parameter name=\"DataLength\" parameterTypeRef=\"uint16\"/>\
              </ParameterSet><ContainerSet>\
              <SequenceContainer name=\"PrimaryHeader\" abstract=\"true\">\
              <EntryList>\
              <ParameterRefEntry parameterRef=\"Version\"/>\
              <ParameterRefEntry parameterRef=\"Type\"/>\
              <ParameterRefEntry parameterRef=\"SecHdrFlag\"/>\
              <ParameterRefEntry parameterRef=\"APID\"/>\
              <ParameterRefEntry parameterRef=\"SeqFlags\"/>\
              <ParameterRefEntry parameterRef=\"SeqCount\"/>\
              <ParameterRefEntry parameterRef=\"DataLength\"/>\
              </EntryList></SequenceContainer></ContainerSet>\
              </TelemetryMetaData></SpaceSystem>",
    },
];

impl Template {
    /// The part as an element of a version, ready to put in.
    ///
    /// # Arguments
    ///
    /// * `version` - The version of the document it goes in.
    /// * `prefix`  - The prefix the document writes that version's
    ///               namespace with, `""` for none.
    /// * `last_id` - The last id the document has given out.
    ///
    /// # Returns
    ///
    /// The element, its ids after `last_id`, and the last id it uses.
    ///
    /// # Errors
    ///
    /// Says why, if the part cannot be said in that version.
    pub fn element(
        &self,
        version: Version,
        prefix: &str,
        last_id: crate::tree::Id,
    ) -> Result<(Element, crate::tree::Id), String> {
        // The part is put in a SpaceSystem of its own to be converted,
        // since only a whole document can be.
        let wrapped = match self.parent {
            "SpaceSystem" => format!(
                "<SpaceSystem xmlns=\"{}\" name=\"T\">{}</SpaceSystem>",
                Version::V1_3.namespace(),
                self.xml
            ),
            _ => format!(
                "<SpaceSystem xmlns=\"{}\" name=\"T\"><TelemetryMetaData>\
                 <{p}>{}</{p}></TelemetryMetaData></SpaceSystem>",
                Version::V1_3.namespace(),
                self.xml,
                p = self.parent
            ),
        };
        let doc = Document::parse(&wrapped).map_err(|e| e.to_string())?;
        let doc =
            convert(&doc, version, &Answers::new()).doc.ok_or_else(|| {
                format!("{} cannot be said in XTCE {version}", self.title)
            })?;
        // The part is the first element in the wrapper's SpaceSystem, or
        // in the set inside its TelemetryMetaData.
        let depth = if self.parent == "SpaceSystem" { 1 } else { 3 };
        let mut at = doc.root();
        for _ in 0..depth {
            at = at.elements().next().ok_or("no part")?;
        }
        let mut part = at.clone();
        prefix_all(&mut part, prefix);
        let mut node = crate::tree::Node::Element(part);
        let mut next = last_id;
        node.renumber(&mut next);
        match node {
            crate::tree::Node::Element(e) => Ok((e, next)),
            _ => unreachable!("an element was put in"),
        }
    }
}

/// Writes an element and everything in it with a prefix.
///
/// # Arguments
///
/// * `e`      - The element.
/// * `prefix` - The prefix, `""` for none.
fn prefix_all(e: &mut Element, prefix: &str) {
    if !prefix.is_empty() {
        e.name = format!("{prefix}:{}", e.local());
    }
    for c in &mut e.children {
        if let crate::tree::Node::Element(k) = c {
            prefix_all(k, prefix);
        }
    }
}

/// Opens the specification of a version at an element's description.
///
/// # Arguments
///
/// * `version` - The version.
/// * `element` - The element's name.
///
/// # Errors
///
/// Says why, if the specification cannot be found or no viewer started.
pub fn open_spec(version: Version, element: &str) -> Result<(), String> {
    let pdf = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(format!("../references/XTCE-{version}.pdf"));
    let installed = std::path::PathBuf::from(format!(
        "/opt/tcs/share/doc/tcs-xtce/XTCE-{version}.pdf"
    ));
    let pdf = if pdf.exists() { pdf } else { installed };
    if !pdf.exists() {
        return Err(format!(
            "the XTCE {version} specification is not here; fetch it with \
             `make -C references editor` in tcs-xtce"
        ));
    }
    // Evince can open a PDF searching for a word, which lands on the
    // element's first mention; other viewers open it at the start.
    let tried = std::process::Command::new("evince")
        .arg(format!("--find={element}"))
        .arg(&pdf)
        .spawn();
    if tried.is_ok() {
        return Ok(());
    }
    std::process::Command::new("xdg-open")
        .arg(&pdf)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("no PDF viewer could be started: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::validate::check;

    #[test]
    fn compares_meaning_not_form() {
        let a = Document::parse(
            "<S a=\"1\" b=\"2\"><!-- x --><ParameterSet>\
             <P name=\"x\"/><P name=\"y\"/></ParameterSet><L>t</L></S>",
        )
        .unwrap();
        let b = Document::parse(
            "<S b='2' a='1'>\n  <ParameterSet><P name=\"y\"/>\
             <P name=\"x\"/></ParameterSet>\n  <L> t </L>\n</S>",
        )
        .unwrap();
        assert_eq!(diff(&a, &b), Vec::<String>::new());
        let c = Document::parse(
            "<S a=\"1\" b=\"3\"><ParameterSet><P name=\"x\"/>\
             <P name=\"z\"/></ParameterSet><L>t</L></S>",
        )
        .unwrap();
        let d = diff(&a, &c);
        assert_eq!(d.len(), 3, "{d:?}");
    }

    #[test]
    fn every_template_is_valid_in_every_version() {
        for v in Version::ALL {
            for t in TEMPLATES {
                let (part, _) = t.element(v, "", 100).unwrap();
                let mut text = String::new();
                crate::tree::write_node(
                    &crate::tree::Node::Element(part),
                    &mut text,
                );
                let doc = match t.parent {
                    "SpaceSystem" => format!(
                        "<SpaceSystem xmlns=\"{}\" name=\"S\">{text}\
                         </SpaceSystem>",
                        v.namespace()
                    ),
                    p => format!(
                        "<SpaceSystem xmlns=\"{}\" name=\"S\">\
                         <TelemetryMetaData><{p}>{text}</{p}>\
                         </TelemetryMetaData></SpaceSystem>",
                        v.namespace()
                    ),
                };
                let d = Document::parse(&doc).unwrap();
                let r = check(&d);
                assert!(!r.has_errors(), "{v} {}: {:?}", t.title, r.problems);
            }
        }
    }
}
