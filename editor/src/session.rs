//! The documents open in the editor, and every change made to them.
//!
//! A session holds the documents of one mission: each is shown in a
//! window of its own, and a reference in one can name a definition in
//! another.  Every change goes through [`Session::apply`], which is where
//! the editor's promises are kept:
//!
//! * **Strictness.**  A change that would give a document a schema error it
//!   did not have is refused, and the document is left as it was.  A
//!   document opened with errors can be corrected, a change at a time, but
//!   never made worse.
//! * **Undo and redo.**  Each document has its own history.  A change that
//!   spans documents -- renaming a definition and every reference to it --
//!   is one step in each document's history, undone in all of them at once,
//!   and only when it is the last change in each.  The histories are kept in
//!   memory only, and are gone when the editor is closed: what lasts is the
//!   file.
//! * **Autosave**, which may be switched off.  With it on, a changed
//!   document is written to its file shortly after each change.  Either
//!   way, a changed document that has not been written is kept in a
//!   recovery file, so a crash loses at most the last few seconds; the
//!   recovery file is offered the next time the document is opened, and
//!   removed when the document is saved.
//! * **Changes on disk.**  A file changed by something else -- git, another
//!   editor -- is noticed, and the document is not written over it until
//!   the person has chosen to reload it or keep their own.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use crate::edit::Op;
use crate::refs::{Index, Loc};
use crate::save::save;
use crate::tree::{Document, Id, Node, DOCUMENT};
use crate::validate::{check, Problem, Report, Severity};

/// One step of a document's history.
#[derive(Debug, Clone)]
pub struct Entry {
    /// The change this step is part of; a change across documents has one
    /// group in each document's history.
    pub group: u64,
    /// What the change was, for the Undo and Redo menu items.
    pub label: String,
    /// The ops this document's part of the change made, in order.
    pub ops: Vec<Op>,
}

/// A document open in the editor.
#[derive(Debug)]
pub struct Open {
    /// The file it was read from and is written to.
    pub path: PathBuf,
    /// The document as it is now.
    pub doc: Document,
    /// What checking it against its schema found.
    pub report: Report,
    /// The steps that can be undone, the last one last.
    pub undo: Vec<Entry>,
    /// The steps that can be redone, the next one last.
    pub redo: Vec<Entry>,
    /// The text last read from or written to the file.
    pub saved: String,
    /// The file's modification time and size when it was last read or
    /// written, to notice another program changing it.
    pub stamp: Option<(SystemTime, u64)>,
    /// Whether the file has changed on disk since, and the person has not
    /// yet said whether to reload it.
    pub changed_on_disk: bool,
}

impl Open {
    /// Whether the document differs from its file.
    ///
    /// # Returns
    ///
    /// True if it has changes not yet written.
    pub fn dirty(&self) -> bool {
        self.doc.write() != self.saved
    }
}

/// A change to make, to one document.
#[derive(Debug, Clone)]
pub struct Change {
    /// The document's index in [`Session::docs`].
    pub doc: usize,
    /// The ops, in order.
    pub ops: Vec<Op>,
}

/// The documents of one mission, open together.
#[derive(Debug, Default)]
pub struct Session {
    /// The open documents, in the order the mission lists them: those
    /// others build on first.
    pub docs: Vec<Open>,
    /// The references between them, made again after each change.
    pub index: Index,
    /// The last group number given to a change.
    last_group: u64,
    /// Where recovery copies are kept, if not the user's state directory:
    /// tests keep theirs apart.
    pub state_dir: Option<PathBuf>,
}

/// What opening a file found besides the document.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Recovery {
    /// The recovery file.
    pub path: PathBuf,
    /// What it holds: the document as it was when the editor last kept it.
    pub text: String,
}

impl Session {
    /// Makes an empty session.
    ///
    /// # Returns
    ///
    /// The session, with no documents.
    pub fn new() -> Session {
        Session::default()
    }

    /// Opens a file.
    ///
    /// # Arguments
    ///
    /// * `path` - The file.
    ///
    /// # Returns
    ///
    /// The document's index, and a recovery file for it if one was left by
    /// an editor that did not close cleanly.  The document is as the file
    /// has it; [`Session::recover`] takes the recovery file's text.
    ///
    /// # Errors
    ///
    /// Says why the file cannot be read, or why it is not a document the
    /// editor can hold.  A document with schema errors is opened, and its
    /// errors listed.
    pub fn open(
        &mut self,
        path: &Path,
    ) -> Result<(usize, Option<Recovery>), String> {
        let path = absolute(path);
        if let Some(i) = self.docs.iter().position(|o| o.path == path) {
            return Ok((i, None));
        }
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("{} cannot be read: {e}", path.display()))?;
        let doc = Document::parse(&text)
            .map_err(|e| format!("{}:{}", path.display(), e))?;
        let report = check(&doc);
        if report.version.is_none() {
            let why = report
                .problems
                .first()
                .map_or(String::new(), |p| p.message.clone());
            return Err(format!("{}: {why}", path.display()));
        }
        let recovery = recovery_path(self.state_dir.as_deref(), &path)
            .and_then(|r| {
                let kept = std::fs::read_to_string(&r).ok()?;
                (kept != text).then_some(Recovery {
                    path: r,
                    text: kept,
                })
            });
        self.docs.push(Open {
            stamp: stamp_of(&path),
            path,
            doc,
            report,
            undo: Vec::new(),
            redo: Vec::new(),
            saved: text,
            changed_on_disk: false,
        });
        self.reindex();
        Ok((self.docs.len() - 1, recovery))
    }

    /// Makes a new document of a version, not yet written.
    ///
    /// # Arguments
    ///
    /// * `path`    - Where it will be written.
    /// * `version` - Its XTCE version.
    /// * `name`    - Its root SpaceSystem's name.
    ///
    /// # Returns
    ///
    /// The document's index.
    ///
    /// # Errors
    ///
    /// Says why, if the name is not one the version allows.
    pub fn create(
        &mut self,
        path: &Path,
        version: crate::schema::Version,
        name: &str,
    ) -> Result<usize, String> {
        let text = format!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
             <xtce:SpaceSystem xmlns:xtce=\"{}\" name=\"{}\"/>\n",
            version.namespace(),
            crate::tree::escape(name, '"')
        );
        let doc = Document::parse(&text).map_err(|e| e.to_string())?;
        let report = check(&doc);
        if let Some(p) = report.problems.first() {
            return Err(p.message.clone());
        }
        self.docs.push(Open {
            path: absolute(path),
            doc,
            report,
            undo: Vec::new(),
            redo: Vec::new(),
            saved: String::new(),
            stamp: None,
            changed_on_disk: false,
        });
        self.reindex();
        Ok(self.docs.len() - 1)
    }

    /// Takes a recovery file's text as the document, as one change that
    /// can be undone, giving back the file's text.
    ///
    /// # Arguments
    ///
    /// * `doc`      - The document's index.
    /// * `recovery` - The recovery file, as [`Session::open`] found it.
    ///
    /// # Errors
    ///
    /// Says why, if the recovered text is not a document of the same
    /// version without more errors.
    pub fn recover(
        &mut self,
        doc: usize,
        recovery: &Recovery,
    ) -> Result<(), String> {
        self.replace_text(doc, &recovery.text, "Recover unsaved changes")
    }

    /// Closes a document.  Its history goes with it.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document's index.  The documents after it move down.
    pub fn close(&mut self, doc: usize) {
        if doc < self.docs.len() {
            let o = self.docs.remove(doc);
            if !o.dirty() {
                if let Some(r) =
                    recovery_path(self.state_dir.as_deref(), &o.path)
                {
                    let _ = std::fs::remove_file(r);
                }
            }
            self.reindex();
        }
    }

    /// Makes a change, if it gives no document a schema error it did not
    /// have.
    ///
    /// # Arguments
    ///
    /// * `changes` - What to change, document by document.
    /// * `label`   - What the change is, for the Undo menu item.
    ///
    /// # Errors
    ///
    /// Says what the change would have broken, or why it could not be
    /// made; every document is then as it was.
    pub fn apply(
        &mut self,
        changes: Vec<Change>,
        label: &str,
    ) -> Result<(), String> {
        if changes.iter().all(|c| c.ops.is_empty()) {
            return Ok(());
        }
        let mut done: Vec<(usize, Vec<Op>)> = Vec::new();
        let mut failure = None;
        'all: for c in &changes {
            let Some(o) = self.docs.get_mut(c.doc) else {
                failure = Some("that document is not open".to_string());
                break;
            };
            let mut made = Vec::new();
            for op in &c.ops {
                if let Err(e) = op.apply(&mut o.doc) {
                    failure =
                        Some(format!("the change could not be made: {e}"));
                    done.push((c.doc, made));
                    break 'all;
                }
                made.push(op.clone());
            }
            done.push((c.doc, made));
        }
        if failure.is_none() {
            for (d, _) in &done {
                let o = &self.docs[*d];
                let after = check(&o.doc);
                if let Some(why) = worse(&o.report, &after, &o.doc) {
                    failure = Some(why);
                    break;
                }
            }
        }
        if let Some(why) = failure {
            for (d, ops) in done.iter().rev() {
                for op in ops.iter().rev() {
                    op.inverse()
                        .apply(&mut self.docs[*d].doc)
                        .expect("undoing what was just done");
                }
            }
            return Err(why);
        }
        self.last_group += 1;
        for (d, ops) in done {
            let o = &mut self.docs[d];
            o.report = check(&o.doc);
            o.undo.push(Entry {
                group: self.last_group,
                label: label.to_string(),
                ops,
            });
            o.redo.clear();
        }
        self.reindex();
        Ok(())
    }

    /// Replaces a document's whole text, as editing its source does, as
    /// one change that can be undone.
    ///
    /// # Arguments
    ///
    /// * `doc`   - The document's index.
    /// * `text`  - The new text.
    /// * `label` - What the change is, for the Undo menu item.
    ///
    /// # Errors
    ///
    /// Says why, if the text is not well-formed, changes the version, or
    /// has more schema errors than the document had.
    pub fn replace_text(
        &mut self,
        doc: usize,
        text: &str,
        label: &str,
    ) -> Result<(), String> {
        self.replace(doc, text, label, true)
    }

    /// Replaces a document's whole text, as one change that can be undone.
    ///
    /// # Arguments
    ///
    /// * `doc`    - The document's index.
    /// * `text`   - The new text.
    /// * `label`  - What the change is, for the Undo menu item.
    /// * `strict` - Whether to refuse text with more schema errors than
    ///              the document has: true for an edit made in the editor,
    ///              false for taking what is in the file, which is the
    ///              file's whatever it holds.
    ///
    /// # Errors
    ///
    /// Says why, if the text is not well-formed, changes the version, or
    /// is strict and has more schema errors than the document had.
    fn replace(
        &mut self,
        doc: usize,
        text: &str,
        label: &str,
        strict: bool,
    ) -> Result<(), String> {
        let o = self.docs.get(doc).ok_or("that document is not open")?;
        let mut new = Document::parse(text).map_err(|e| e.to_string())?;
        let was = o.report.version;
        let now = check(&new);
        if now.version != was {
            return Err(format!(
                "the text is not XTCE {}, as the document is; use Upgrade or \
                 Downgrade to change its version",
                was.map_or("?".to_string(), |v| v.to_string())
            ));
        }
        let count = |r: &Report| {
            r.problems
                .iter()
                .filter(|p| p.severity == Severity::Error)
                .count()
        };
        if strict && count(&now) > count(&o.report) {
            let first = now
                .problems
                .iter()
                .find(|p| p.severity == Severity::Error)
                .map(|p| {
                    let line = new.line_of(p.id).unwrap_or(1);
                    format!("line {line}: {}", p.message)
                })
                .unwrap_or_default();
            return Err(format!(
                "the text breaks XTCE {}'s schema, so it was not taken: \
                 {first}",
                was.map_or("?".to_string(), |v| v.to_string())
            ));
        }
        // The new nodes are given ids after the document's, so that an id
        // is never used twice in one document's life.
        let mut next = o.doc.last_id;
        for n in &mut new.top {
            n.renumber(&mut next);
        }
        let op = Op::Splice {
            parent: DOCUMENT,
            at: 0,
            removed: o.doc.top.clone(),
            inserted: new.top,
            empty: (false, false),
        };
        self.docs[doc].doc.last_id = next;
        self.docs[doc].doc.bom = new.bom;
        // Count-based checking has been done above, since every id changed.
        let o = &mut self.docs[doc];
        op.apply(&mut o.doc)?;
        o.report = check(&o.doc);
        self.last_group += 1;
        o.undo.push(Entry {
            group: self.last_group,
            label: label.to_string(),
            ops: vec![op],
        });
        o.redo.clear();
        self.reindex();
        Ok(())
    }

    /// What Undo would undo in a document.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document's index.
    ///
    /// # Returns
    ///
    /// The change's label, or `None` if there is nothing to undo.
    pub fn undo_label(&self, doc: usize) -> Option<&str> {
        self.docs.get(doc)?.undo.last().map(|e| e.label.as_str())
    }

    /// What Redo would redo in a document.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document's index.
    ///
    /// # Returns
    ///
    /// The change's label, or `None` if there is nothing to redo.
    pub fn redo_label(&self, doc: usize) -> Option<&str> {
        self.docs.get(doc)?.redo.last().map(|e| e.label.as_str())
    }

    /// Undoes the last change to a document, and its part in every other
    /// document it was made in.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document's index.
    ///
    /// # Errors
    ///
    /// Says why, if there is nothing to undo, or the change spans another
    /// document that has been changed since.
    pub fn undo(&mut self, doc: usize) -> Result<(), String> {
        self.step(doc, true)
    }

    /// Redoes the last change undone in a document.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document's index.
    ///
    /// # Errors
    ///
    /// As [`Session::undo`].
    pub fn redo(&mut self, doc: usize) -> Result<(), String> {
        self.step(doc, false)
    }

    /// Undoes or redoes a step.
    ///
    /// # Arguments
    ///
    /// * `doc`  - The document's index.
    /// * `back` - True to undo, false to redo.
    ///
    /// # Errors
    ///
    /// As [`Session::undo`].
    fn step(&mut self, doc: usize, back: bool) -> Result<(), String> {
        /// Gives the history a step is taken from.
        ///
        /// # Arguments
        ///
        /// * `o`    - The document.
        /// * `back` - True for its undo history, false for its redo.
        ///
        /// # Returns
        ///
        /// The history.
        fn stack(o: &Open, back: bool) -> &Vec<Entry> {
            if back {
                &o.undo
            } else {
                &o.redo
            }
        }
        let group = self
            .docs
            .get(doc)
            .and_then(|o| stack(o, back).last())
            .map(|e| e.group)
            .ok_or(if back {
                "there is nothing to undo"
            } else {
                "there is nothing to redo"
            })?;
        let mut members = Vec::new();
        for (i, o) in self.docs.iter().enumerate() {
            let in_group =
                o.undo.iter().chain(&o.redo).any(|e| e.group == group);
            if !in_group {
                continue;
            }
            if stack(o, back).last().map(|e| e.group) != Some(group) {
                return Err(format!(
                    "this change was also made in {}, which has been changed \
                     since; {} the later changes there first",
                    o.path.display(),
                    if back { "undo" } else { "redo" }
                ));
            }
            members.push(i);
        }
        for i in members {
            let o = &mut self.docs[i];
            let e = if back { o.undo.pop() } else { o.redo.pop() }
                .expect("checked");
            if back {
                for op in e.ops.iter().rev() {
                    op.inverse().apply(&mut o.doc)?;
                }
                o.redo.push(e);
            } else {
                for op in &e.ops {
                    op.apply(&mut o.doc)?;
                }
                o.undo.push(e);
            }
            o.report = check(&o.doc);
        }
        self.reindex();
        Ok(())
    }

    /// Makes the index of references again.
    fn reindex(&mut self) {
        let docs: Vec<&Document> = self.docs.iter().map(|o| &o.doc).collect();
        self.index = Index::of(&docs);
    }

    /// Everything wrong with a document: its schema errors, and the
    /// warnings about its references.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document's index.
    ///
    /// # Returns
    ///
    /// The problems, errors first.
    pub fn problems(&self, doc: usize) -> Vec<Problem> {
        let Some(o) = self.docs.get(doc) else {
            return Vec::new();
        };
        let mut out = o.report.problems.clone();
        out.extend(self.index.problems(doc));
        out.sort_by_key(|p| p.severity);
        out
    }

    /// Writes a document to its file.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document's index.
    ///
    /// # Errors
    ///
    /// Says why it could not be written, or that the file has changed on
    /// disk and the person has not yet chosen what to do about it.
    pub fn save(&mut self, doc: usize) -> Result<(), String> {
        let o = self.docs.get_mut(doc).ok_or("that document is not open")?;
        if o.changed_on_disk {
            return Err(format!(
                "{} has been changed by another program since it was opened; \
                 reload it, or choose to keep this version, before saving",
                o.path.display()
            ));
        }
        let text = o.doc.write();
        save(&o.path, &text)?;
        o.saved = text;
        o.stamp = stamp_of(&o.path);
        if let Some(r) = recovery_path(self.state_dir.as_deref(), &o.path) {
            let _ = std::fs::remove_file(r);
        }
        Ok(())
    }

    /// Writes a document to another file, which it is then written to.
    ///
    /// # Arguments
    ///
    /// * `doc`  - The document's index.
    /// * `path` - The new file.
    ///
    /// # Errors
    ///
    /// Says why it could not be written.
    pub fn save_as(&mut self, doc: usize, path: &Path) -> Result<(), String> {
        let o = self.docs.get_mut(doc).ok_or("that document is not open")?;
        let old = std::mem::replace(&mut o.path, absolute(path));
        o.changed_on_disk = false;
        let r = self.save(doc);
        if r.is_err() {
            self.docs[doc].path = old;
        }
        r
    }

    /// Does what the autosave timer does: writes each changed document
    /// that may be written, and keeps a recovery copy of the rest.
    ///
    /// # Arguments
    ///
    /// * `autosave` - Whether autosave is on.
    ///
    /// # Returns
    ///
    /// What could not be written, as messages to show.
    pub fn tick(&mut self, autosave: bool) -> Vec<String> {
        let mut out = Vec::new();
        for i in 0..self.docs.len() {
            if !self.docs[i].dirty() {
                continue;
            }
            let o = &self.docs[i];
            // A document never saved has no file yet to save over, and one
            // changed on disk waits for the person to choose.
            let may_save =
                autosave && !o.changed_on_disk && !o.saved.is_empty();
            if may_save {
                if let Err(e) = self.save(i) {
                    out.push(e);
                }
            } else if let Some(r) =
                recovery_path(self.state_dir.as_deref(), &o.path)
            {
                if let Some(dir) = r.parent() {
                    let _ = std::fs::create_dir_all(dir);
                }
                if let Err(e) = save(&r, &o.doc.write()) {
                    out.push(format!(
                        "the recovery copy could not be kept: {e}"
                    ));
                }
            }
        }
        out
    }

    /// Looks for files changed on disk by another program.
    ///
    /// # Returns
    ///
    /// The indexes of documents newly found changed on disk.
    pub fn look_at_disk(&mut self) -> Vec<usize> {
        let mut out = Vec::new();
        for (i, o) in self.docs.iter_mut().enumerate() {
            if o.changed_on_disk || o.saved.is_empty() {
                continue;
            }
            let now = stamp_of(&o.path);
            if now == o.stamp {
                continue;
            }
            o.stamp = now;
            // A touch that changed nothing is not a change.
            match std::fs::read_to_string(&o.path) {
                Ok(t) if t == o.saved => {}
                _ => {
                    o.changed_on_disk = true;
                    out.push(i);
                }
            }
        }
        out
    }

    /// Reads a document's file again, replacing the document with what is
    /// on disk, as a change that can be undone.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document's index.
    ///
    /// # Errors
    ///
    /// Says why the file cannot be read or taken.
    pub fn reload(&mut self, doc: usize) -> Result<(), String> {
        let path = self
            .docs
            .get(doc)
            .ok_or("that document is not open")?
            .path
            .clone();
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("{} cannot be read: {e}", path.display()))?;
        self.docs[doc].changed_on_disk = false;
        let r = self.replace(doc, &text, "Reload from disk", false);
        let o = &mut self.docs[doc];
        if r.is_ok() {
            o.saved = text;
            o.stamp = stamp_of(&path);
        } else {
            o.changed_on_disk = true;
        }
        r
    }

    /// Keeps the document as it is in the editor, over a file changed on
    /// disk: the next save writes over the other program's change.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document's index.
    pub fn keep_mine(&mut self, doc: usize) {
        if let Some(o) = self.docs.get_mut(doc) {
            o.changed_on_disk = false;
            o.saved = std::fs::read_to_string(&o.path).unwrap_or_default();
        }
    }

    /// Makes the changes that rename a definition and every reference to
    /// it in every open document.
    ///
    /// # Arguments
    ///
    /// * `at`  - The definition's element.
    /// * `new` - Its new name.
    ///
    /// # Returns
    ///
    /// The changes, document by document, for [`Session::apply`].
    ///
    /// # Errors
    ///
    /// Says why, if the element is not a definition.
    pub fn rename_changes(
        &self,
        at: Loc,
        new: &str,
    ) -> Result<Vec<Change>, String> {
        let def = self
            .index
            .def_at(at)
            .ok_or("only something a reference can name is renamed this way")?;
        let kind = self.index.defs[def].kind;
        let mut per: HashMap<usize, Vec<Op>> = HashMap::new();
        if let Some(op) =
            crate::edit::set_attr(&self.docs[at.doc].doc, at.id, "name", new)
        {
            per.entry(at.doc).or_default().push(op);
        }
        for r in self.index.usages(def) {
            let d = &self.docs[r.loc.doc].doc;
            let text = crate::refs::renamed(&r.text, kind, new);
            let op = match &r.attr {
                Some(a) => crate::edit::set_attr(d, r.loc.id, a, &text),
                None => crate::edit::set_text(d, r.loc.id, &text),
            };
            if let Some(op) = op {
                per.entry(r.loc.doc).or_default().push(op);
            }
        }
        let mut out: Vec<Change> = per
            .into_iter()
            .map(|(doc, ops)| Change { doc, ops })
            .collect();
        out.sort_by_key(|c| c.doc);
        Ok(out)
    }

    /// Finds the element a node with an id is in, in any open document.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document's index.
    /// * `id`  - The node.
    ///
    /// # Returns
    ///
    /// The node, if it is there.
    pub fn node(&self, doc: usize, id: Id) -> Option<&Node> {
        self.docs.get(doc)?.doc.node(id)
    }
}

/// Whether a change made a document worse: an element or attribute with
/// an error it did not have, or more errors than there were.
///
/// # Arguments
///
/// * `before` - What checking the document found before the change.
/// * `after`  - What it finds after.
/// * `doc`    - The document after the change, to say where.
///
/// # Returns
///
/// What is worse, as a message, or `None` if nothing is.
fn worse(before: &Report, after: &Report, doc: &Document) -> Option<String> {
    let errors = |r: &Report| -> Vec<(Id, Option<String>)> {
        r.problems
            .iter()
            .filter(|p| p.severity == Severity::Error)
            .map(|p| (p.id, p.attr.clone()))
            .collect()
    };
    let had = errors(before);
    let has = errors(after);
    let new = after
        .problems
        .iter()
        .filter(|p| p.severity == Severity::Error)
        .find(|p| !had.contains(&(p.id, p.attr.clone())));
    if new.is_none() && has.len() <= had.len() {
        return None;
    }
    let p = new.or_else(|| after.problems.first())?;
    let version = after.version.map_or("?".to_string(), |v| v.to_string());
    let line = doc
        .line_of(p.id)
        .map_or(String::new(), |l| format!("line {l}: "));
    Some(format!(
        "XTCE {version} does not allow that, so it was not done: {line}{}",
        p.message
    ))
}

/// A path made absolute, so that one file opened by two names is one
/// document.
///
/// # Arguments
///
/// * `path` - The path.
///
/// # Returns
///
/// The path, resolved if the file exists and joined to the working
/// directory if not.
fn absolute(path: &Path) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| {
        std::env::current_dir()
            .map(|d| d.join(path))
            .unwrap_or_else(|_| path.to_path_buf())
    })
}

/// A file's modification time and size.
///
/// # Arguments
///
/// * `path` - The file.
///
/// # Returns
///
/// Both, or `None` if the file cannot be looked at.
fn stamp_of(path: &Path) -> Option<(SystemTime, u64)> {
    let m = std::fs::metadata(path).ok()?;
    Some((m.modified().ok()?, m.len()))
}

/// Where the recovery copy of a document is kept: in the user's state
/// directory, `$XDG_STATE_HOME/xtce-edit` or `~/.local/state/xtce-edit`,
/// named by the file's stem and a digest of its full path.  Not beside the
/// file, where it would turn up in `git status`; and named by the digest so
/// that two files of one name in two directories have two copies.
///
/// # Arguments
///
/// * `state` - The directory to keep it in instead, if any.
/// * `path`  - The document's file, absolute.
///
/// # Returns
///
/// The recovery file's path, or `None` if there is no home directory.
pub fn recovery_path(state: Option<&Path>, path: &Path) -> Option<PathBuf> {
    let base = match std::env::var_os("XDG_STATE_HOME") {
        _ if state.is_some() => state.map(Path::to_path_buf)?,
        Some(d) if !d.is_empty() => PathBuf::from(d),
        _ => PathBuf::from(std::env::var_os("HOME")?).join(".local/state"),
    };
    let stem = path.file_stem()?.to_string_lossy();
    // FNV-1a, written out so the name is the same on every run and every
    // build: the copy is found again by working the name out again.
    let mut h: u64 = 0xcbf29ce484222325;
    for b in path.to_string_lossy().bytes() {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x100000001b3);
    }
    Some(base.join("xtce-edit").join(format!("{stem}-{h:016x}.xtce")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::Version;

    /// Writes a small document of a version in a directory.
    ///
    /// # Arguments
    ///
    /// * `dir`    - The directory.
    /// * `name`   - The file's name.
    /// * `inside` - What its SpaceSystem holds.
    ///
    /// # Returns
    ///
    /// The file's path.
    fn file(dir: &Path, name: &str, inside: &str) -> PathBuf {
        let p = dir.join(name);
        std::fs::write(
            &p,
            format!(
                "<SpaceSystem xmlns=\"{}\" name=\"{}\">{inside}\
                 </SpaceSystem>\n",
                Version::V1_3.namespace(),
                name.trim_end_matches(".xtce")
            ),
        )
        .unwrap();
        p
    }

    /// The ParameterSet text with parameters of names.
    ///
    /// # Arguments
    ///
    /// * `refs` - The type each parameter refers to, one per parameter.
    ///
    /// # Returns
    ///
    /// The TelemetryMetaData holding them.
    fn params(refs: &[&str]) -> String {
        let ps: String = refs
            .iter()
            .enumerate()
            .map(|(i, r)| {
                format!("<Parameter name=\"p{i}\" parameterTypeRef=\"{r}\"/>")
            })
            .collect();
        format!(
            "<TelemetryMetaData><ParameterSet>{ps}</ParameterSet>\
             </TelemetryMetaData>"
        )
    }

    #[test]
    fn refuses_a_change_that_breaks_the_schema() {
        let dir = tempfile::tempdir().unwrap();
        let p = file(dir.path(), "a.xtce", &params(&["t"]));
        let mut s = Session::new();
        s.state_dir = Some(dir.path().join("state"));
        let (d, _) = s.open(&p).unwrap();
        let root = s.docs[d].doc.root().id;
        let op =
            crate::edit::set_attr(&s.docs[d].doc, root, "bogus", "1").unwrap();
        let before = s.docs[d].doc.write();
        let e = s
            .apply(
                vec![Change {
                    doc: d,
                    ops: vec![op],
                }],
                "x",
            )
            .unwrap_err();
        assert!(e.contains("does not allow"), "{e}");
        assert_eq!(s.docs[d].doc.write(), before);
        assert!(s.docs[d].undo.is_empty());
    }

    #[test]
    fn renames_across_documents_and_undoes_in_one_step() {
        let dir = tempfile::tempdir().unwrap();
        let types = file(
            dir.path(),
            "dt.xtce",
            "<TelemetryMetaData><ParameterTypeSet>\
             <IntegerParameterType name=\"u8\"><IntegerDataEncoding/>\
             </IntegerParameterType></ParameterTypeSet></TelemetryMetaData>",
        );
        let user = file(dir.path(), "r.xtce", &params(&["/dt/u8", "/dt/u8"]));
        let mut s = Session::new();
        s.state_dir = Some(dir.path().join("state"));
        let (a, _) = s.open(&types).unwrap();
        let (b, _) = s.open(&user).unwrap();
        assert!(s.problems(b).is_empty(), "{:?}", s.problems(b));
        let def = s.index.defs.iter().find(|d| d.key == ["dt", "u8"]).unwrap();
        let changes = s.rename_changes(def.loc, "byte").unwrap();
        s.apply(changes, "Rename u8 to byte").unwrap();
        assert!(s.docs[b].doc.write().contains("/dt/byte"));
        assert!(s.problems(b).is_empty(), "{:?}", s.problems(b));
        // A later change to one document stops the shared one being undone
        // from the other until it is undone itself.
        let root = s.docs[a].doc.root().id;
        let op = crate::edit::set_attr(
            &s.docs[a].doc,
            root,
            "shortDescription",
            "x",
        )
        .unwrap();
        s.apply(
            vec![Change {
                doc: a,
                ops: vec![op],
            }],
            "describe",
        )
        .unwrap();
        assert!(s.undo(b).is_err());
        s.undo(a).unwrap();
        s.undo(b).unwrap();
        assert!(s.docs[b].doc.write().contains("/dt/u8"));
        assert!(!s.docs[a].doc.write().contains("byte"));
        s.redo(a).unwrap();
        assert!(s.docs[b].doc.write().contains("/dt/byte"));
    }

    #[test]
    fn autosaves_or_keeps_a_recovery_copy() {
        let dir = tempfile::tempdir().unwrap();
        let p = file(dir.path(), "a.xtce", "");
        let mut s = Session::new();
        s.state_dir = Some(dir.path().join("state"));
        let (d, _) = s.open(&p).unwrap();
        let root = s.docs[d].doc.root().id;
        let op = crate::edit::set_attr(
            &s.docs[d].doc,
            root,
            "shortDescription",
            "x",
        )
        .unwrap();
        s.apply(
            vec![Change {
                doc: d,
                ops: vec![op],
            }],
            "x",
        )
        .unwrap();
        // Off: the file stays, and a recovery copy is kept.
        assert!(s.tick(false).is_empty());
        assert!(!std::fs::read_to_string(&p)
            .unwrap()
            .contains("shortDescription"));
        let r = recovery_path(s.state_dir.as_deref(), &s.docs[d].path).unwrap();
        assert!(std::fs::read_to_string(&r)
            .unwrap()
            .contains("shortDescription"));
        // A new session finds it and can take it.
        let mut s2 = Session::new();
        s2.state_dir = s.state_dir.clone();
        let (d2, rec) = s2.open(&p).unwrap();
        let rec = rec.expect("a recovery copy");
        s2.recover(d2, &rec).unwrap();
        assert!(s2.docs[d2].doc.write().contains("shortDescription"));
        // On: the file is written and the copy goes.
        assert!(s.tick(true).is_empty());
        assert!(std::fs::read_to_string(&p)
            .unwrap()
            .contains("shortDescription"));
        assert!(!r.exists());
    }

    #[test]
    fn notices_a_file_changed_on_disk() {
        let dir = tempfile::tempdir().unwrap();
        let p = file(dir.path(), "a.xtce", "");
        let mut s = Session::new();
        s.state_dir = Some(dir.path().join("state"));
        let (d, _) = s.open(&p).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(20));
        file(dir.path(), "a.xtce", "<Header version=\"2\" bogus=\"1\"/>");
        assert_eq!(s.look_at_disk(), vec![d]);
        assert!(s.save(d).is_err());
        s.reload(d).unwrap();
        assert!(s.docs[d].doc.write().contains("version=\"2\""));
        s.undo(d).unwrap();
        assert!(!s.docs[d].doc.write().contains("version"));
    }
}
