//! The editor's windows, and what each thing done in them does.
//!
//! Everything the windows do is done through [`Session`]: an edit is made
//! into ops, the session makes them if the document's version allows them,
//! and every window is then drawn again from the documents.  Nothing a
//! window shows is kept anywhere but in the documents and in which rows are
//! open and selected, so no window can show something the document does
//! not say.

use std::cell::RefCell;
use std::collections::HashSet;
use std::path::PathBuf;
use std::rc::Rc;
use std::time::{Duration, Instant};

use slint::{ComponentHandle, ModelRc, SharedString, VecModel};

use crate::codec;
use crate::convert::{convert, Answers, Outcome};
use crate::edit;
use crate::make::Maker;
use crate::refs::{Kind, Loc};
use crate::schema::{TypeRef, Version};
use crate::session::{Change, Session};
use crate::tools;
use crate::tree::{Comment, Element, Id, Node};
use crate::validate::{insertable, Severity};

slint::include_modules!();

/// One document window.
struct Win {
    /// What names the window to its callbacks, which outlive any index.
    key: u64,
    /// The file of the document it shows, or `None` before one is opened.
    path: Option<PathBuf>,
    /// The window.
    window: DocWindow,
    /// The elements whose children are shown.
    open: HashSet<Id>,
    /// The selected element or comment.
    selected: Option<Id>,
    /// The ids of the rows, in the order shown, to find a row's place.
    order: Vec<Id>,
    /// Usages being shown in place of the problems, with their documents,
    /// or empty to show the problems.
    usages: Vec<Loc>,
}

/// A conversion being set up in its dialog.
struct Converting {
    /// The window whose document is converted.
    key: u64,
    /// The version it goes to.
    target: Version,
    /// The answers given so far.
    answers: Answers,
    /// What the last try did.
    outcome: Outcome,
    /// The dialog.
    dialog: ConvertDialog,
}

/// The editor: the session, its windows and dialogs, and its settings.
struct App {
    /// The open documents.
    session: Session,
    /// The document windows.
    wins: Vec<Win>,
    /// The last window key given out.
    last_key: u64,
    /// Whether autosave is on.
    autosave: bool,
    /// When a document was last changed, so autosave waits for a pause.
    last_change: Instant,
    /// Windows of text, kept so they stay open.
    texts: Vec<TextWindow>,
    /// Layout windows, kept so they stay open.
    layouts: Vec<LayoutWindow>,
    /// Tester windows and their codecs.
    testers: Vec<(TesterWindow, Rc<codec::Tester>)>,
    /// Questions being asked.
    asks: Vec<AskDialog>,
    /// Questions answered in words being asked.
    inputs: Vec<InputDialog>,
    /// The conversion being set up, if any.
    converting: Option<Converting>,
}

/// The editor, shared among the callbacks.
type Shared = Rc<RefCell<App>>;

/// Where the editor's settings are kept: `$XDG_CONFIG_HOME/xtce-edit` or
/// `~/.config/xtce-edit`.
///
/// # Returns
///
/// The settings file's path, or `None` without a home directory.
fn settings_path() -> Option<PathBuf> {
    let base = match std::env::var_os("XDG_CONFIG_HOME") {
        Some(d) if !d.is_empty() => PathBuf::from(d),
        _ => PathBuf::from(std::env::var_os("HOME")?).join(".config"),
    };
    Some(base.join("xtce-edit").join("settings"))
}

/// Reads whether autosave is on.  It is on unless the settings say off.
///
/// # Returns
///
/// Whether autosave is on.
fn read_autosave() -> bool {
    settings_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .map(|t| !t.lines().any(|l| l.trim() == "autosave=off"))
        .unwrap_or(true)
}

/// Writes whether autosave is on.
///
/// # Arguments
///
/// * `on` - Whether it is.
///
/// # Errors
///
/// Says why the setting could not be kept.
fn write_autosave(on: bool) -> Result<(), String> {
    let p = settings_path().ok_or("there is no home directory")?;
    if let Some(d) = p.parent() {
        std::fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    let text = format!("autosave={}\n", if on { "on" } else { "off" });
    crate::save::save(&p, &text)
}

/// Opens the editor on files and runs it until its last window closes.
///
/// # Arguments
///
/// * `files` - The files to open, the documents of one mission.
///
/// # Errors
///
/// Says why the windows could not be made.
pub fn run(files: &[String]) -> Result<(), String> {
    let app = start(files);
    // Once a second: files changed on disk, and autosave or the recovery
    // copy once the person has paused.
    let timer = slint::Timer::default();
    let t_app = app.clone();
    timer.start(
        slint::TimerMode::Repeated,
        Duration::from_secs(1),
        move || tick(&t_app),
    );
    slint::run_event_loop_until_quit().map_err(|e| e.to_string())
}

/// Makes the editor and opens files in it, each in a window of its own,
/// or one empty window if none opens.
///
/// # Arguments
///
/// * `files` - The files to open, the documents of one mission.
///
/// # Returns
///
/// The editor.
fn start(files: &[String]) -> Shared {
    let app: Shared = Rc::new(RefCell::new(App {
        session: Session::new(),
        wins: Vec::new(),
        last_key: 0,
        autosave: read_autosave(),
        last_change: Instant::now(),
        texts: Vec::new(),
        layouts: Vec::new(),
        testers: Vec::new(),
        asks: Vec::new(),
        inputs: Vec::new(),
        converting: None,
    }));
    let mut opened = false;
    for f in files {
        match open_file(&app, PathBuf::from(f)) {
            Ok(()) => opened = true,
            Err(e) => eprintln!("{e}"),
        }
    }
    if !opened {
        new_window(&app, None);
    }
    app
}

/// Does what the editor does once a second.
///
/// # Arguments
///
/// * `app` - The editor.
fn tick(app: &Shared) {
    let changed = {
        let mut a = app.borrow_mut();
        let changed = a.session.look_at_disk();
        if a.last_change.elapsed() >= Duration::from_secs(2) {
            let on = a.autosave;
            let errs = a.session.tick(on);
            if let Some(e) = errs.first() {
                let e = e.clone();
                for w in &a.wins {
                    w.window.set_status(e.clone().into());
                }
            }
        }
        changed
    };
    for d in changed {
        let path = app.borrow().session.docs[d].path.clone();
        ask(
            app,
            &format!(
                "{} has been changed by another program.  Reload it, losing \
                 the changes made here (Undo brings them back), or keep the \
                 version here and write it over the other on the next save?",
                path.display()
            ),
            &["Reload", "Keep mine"],
            move |app, i| {
                let mut a = app.borrow_mut();
                if let Some(d) =
                    a.session.docs.iter().position(|o| o.path == path)
                {
                    if i == 0 {
                        if let Err(e) = a.session.reload(d) {
                            status_all(&a, &e);
                        }
                    } else {
                        a.session.keep_mine(d);
                    }
                }
                drop(a);
                refresh_all(app);
            },
        );
    }
    let a = app.borrow();
    for w in &a.wins {
        let dirty = doc_index(&a, w).is_some_and(|d| a.session.docs[d].dirty());
        w.window.set_dirty(dirty);
    }
}

/// Shows a message in every window's status line.
///
/// # Arguments
///
/// * `a`   - The editor.
/// * `msg` - The message.
fn status_all(a: &App, msg: &str) {
    for w in &a.wins {
        w.window.set_status(msg.into());
    }
}

/// The index in the session of a window's document.
///
/// # Arguments
///
/// * `a` - The editor.
/// * `w` - The window.
///
/// # Returns
///
/// The index, or `None` if the window shows no document.
fn doc_index(a: &App, w: &Win) -> Option<usize> {
    let p = w.path.as_ref()?;
    a.session.docs.iter().position(|o| &o.path == p)
}

/// Opens a file in a window of its own, offering its recovery copy if one
/// was left behind.  A file already open has its window brought forward.
///
/// # Arguments
///
/// * `app`  - The editor.
/// * `path` - The file.
///
/// # Errors
///
/// Says why the file could not be opened.
fn open_file(app: &Shared, path: PathBuf) -> Result<(), String> {
    let (d, recovery) = app.borrow_mut().session.open(&path)?;
    let doc_path = app.borrow().session.docs[d].path.clone();
    let existing = app
        .borrow()
        .wins
        .iter()
        .find(|w| w.path.as_ref() == Some(&doc_path))
        .map(|w| w.key);
    let key = match existing {
        Some(k) => k,
        None => {
            // A window showing nothing takes the document.
            let empty = app
                .borrow()
                .wins
                .iter()
                .find(|w| w.path.is_none())
                .map(|w| w.key);
            match empty {
                Some(k) => {
                    let mut a = app.borrow_mut();
                    let w = a.wins.iter_mut().find(|w| w.key == k).unwrap();
                    w.path = Some(doc_path.clone());
                    k
                }
                None => new_window(app, Some(doc_path.clone())),
            }
        }
    };
    let shown = doc_path.clone();
    if let Some(r) = recovery {
        ask(
            app,
            &format!(
                "The editor did not close cleanly last time {} was open, and \
                 kept changes that were not saved.  Take them (Undo gives \
                 back the file as it is), or leave them and remove the copy?",
                doc_path.display()
            ),
            &["Take the unsaved changes", "Leave them"],
            move |app, i| {
                let mut a = app.borrow_mut();
                if let Some(d) =
                    a.session.docs.iter().position(|o| o.path == doc_path)
                {
                    if i == 0 {
                        if let Err(e) = a.session.recover(d, &r) {
                            status_all(&a, &e);
                        }
                    } else {
                        let _ = std::fs::remove_file(&r.path);
                    }
                }
                drop(a);
                refresh_all(app);
            },
        );
    }
    {
        let mut a = app.borrow_mut();
        let root = a.session.docs[d].doc.root().id;
        if let Some(w) = a.wins.iter_mut().find(|w| w.key == key) {
            w.open.insert(root);
            w.selected.get_or_insert(root);
        }
    }
    refresh_all(app);
    status_of(app, key, &format!("Opened {}", shown.display()));
    Ok(())
}

/// Makes a document window and connects its callbacks.
///
/// # Arguments
///
/// * `app`  - The editor.
/// * `path` - The file of the document it shows, if any.
///
/// # Returns
///
/// The window's key.
fn new_window(app: &Shared, path: Option<PathBuf>) -> u64 {
    let window = DocWindow::new().expect("the window can be made");
    let key = {
        let mut a = app.borrow_mut();
        a.last_key += 1;
        a.last_key
    };
    connect(app, &window, key);
    // Each new window a little below and right of the last, so that the
    // documents of a mission opened together are not stacked exactly.
    let n = app.borrow().wins.len() as f32;
    window
        .window()
        .set_position(slint::LogicalPosition::new(60.0 * n, 60.0 * n));
    window.show().expect("the window can be shown");
    app.borrow_mut().wins.push(Win {
        key,
        path,
        window,
        open: HashSet::new(),
        selected: None,
        order: Vec::new(),
        usages: Vec::new(),
    });
    refresh_all(app);
    key
}

/// Connects a window's callbacks.
///
/// # Arguments
///
/// * `app`    - The editor.
/// * `window` - The window.
/// * `key`    - The window's key.
fn connect(app: &Shared, window: &DocWindow, key: u64) {
    /// Connects a callback of no arguments to a function of the editor and
    /// the window's key.
    macro_rules! on {
        ($setter:ident, $f:expr) => {{
            let app = app.clone();
            window.$setter(move || $f(&app, key));
        }};
        ($setter:ident, $f:expr, $($arg:ident),+) => {{
            let app = app.clone();
            window.$setter(move |$($arg),+| $f(&app, key, $($arg),+));
        }};
    }
    on!(on_new_doc, new_doc);
    on!(on_open_doc, open_doc);
    on!(on_add_doc, open_doc);
    on!(on_save, save);
    on!(on_save_as, save_as);
    on!(on_close_doc, close_doc);
    on!(on_toggle_autosave, toggle_autosave);
    on!(on_undo, |app, key| history(app, key, true));
    on!(on_redo, |app, key| history(app, key, false));
    on!(on_select, select, id);
    on!(on_toggle, toggle, id);
    on!(on_set_attr, set_attr, name, value);
    on!(on_remove_attr, remove_attr, name);
    on!(on_follow, follow, name);
    on!(on_set_value, set_value, text);
    on!(on_set_comment, set_comment, text);
    on!(on_add_comment, add_comment);
    on!(on_insert, insert_decl, index);
    on!(on_insert_template, insert_template, index);
    on!(on_delete_selected, delete_selected);
    on!(on_move_up, |app, key| move_selected(app, key, true));
    on!(on_move_down, |app, key| move_selected(app, key, false));
    on!(on_rename, rename);
    on!(on_find_usages, find_usages);
    on!(on_convert, start_convert, v);
    on!(on_load_source, load_source);
    on!(on_apply_source, apply_source, text);
    on!(on_show_layout, show_layout);
    on!(on_show_tester, show_tester);
    on!(on_compare, compare);
    on!(on_digest, digest);
    on!(on_spec, spec);
    on!(on_pick_problem, pick_problem, id);
    let app2 = app.clone();
    window.window().on_close_requested(move || {
        close_doc(&app2, key);
        slint::CloseRequestResponse::KeepWindowShown
    });
}

/// Asks a question, and does something with the answer.
///
/// # Arguments
///
/// * `app`     - The editor.
/// * `message` - The question.
/// * `answers` - Up to three answers, as the buttons say them.
/// * `then`    - Called with the editor and the index of the answer.
fn ask(
    app: &Shared,
    message: &str,
    answers: &[&str],
    then: impl Fn(&Shared, usize) + 'static,
) {
    let d = AskDialog::new().expect("the dialog can be made");
    d.set_message(message.into());
    d.set_first(answers.first().copied().unwrap_or("OK").into());
    d.set_second(answers.get(1).copied().unwrap_or("").into());
    d.set_third(answers.get(2).copied().unwrap_or("").into());
    let a2 = app.clone();
    let weak = d.as_weak();
    d.on_chosen(move |i| {
        if let Some(d) = weak.upgrade() {
            let _ = d.hide();
        }
        then(&a2, i as usize);
    });
    let _ = d.show();
    app.borrow_mut().asks.push(d);
}

/// Asks for a word, and does something with it.
///
/// # Arguments
///
/// * `app`     - The editor.
/// * `message` - What is asked.
/// * `start`   - The answer to begin with.
/// * `then`    - Called with the editor and the answer, if one was given.
fn input(
    app: &Shared,
    message: &str,
    start: &str,
    then: impl Fn(&Shared, String) + 'static,
) {
    let d = InputDialog::new().expect("the dialog can be made");
    d.set_message(message.into());
    d.set_answer(start.into());
    let a2 = app.clone();
    let weak = d.as_weak();
    d.on_done(move |ok, text| {
        if let Some(d) = weak.upgrade() {
            let _ = d.hide();
        }
        if ok {
            then(&a2, text.to_string());
        }
    });
    let _ = d.show();
    app.borrow_mut().inputs.push(d);
}

/// Shows text in a window of its own.
///
/// # Arguments
///
/// * `app`     - The editor.
/// * `heading` - The window's title.
/// * `body`    - The text.
fn show_text(app: &Shared, heading: &str, body: &str) {
    let w = TextWindow::new().expect("the window can be made");
    w.set_heading(heading.into());
    w.set_body(body.into());
    let _ = w.show();
    app.borrow_mut().texts.push(w);
}

/// Draws every window again from the documents.
///
/// # Arguments
///
/// * `app` - The editor.
fn refresh_all(app: &Shared) {
    let mut a = app.borrow_mut();
    let keys: Vec<u64> = a.wins.iter().map(|w| w.key).collect();
    for k in keys {
        refresh(&mut a, k);
    }
}

/// Draws one window again from its document.
///
/// # Arguments
///
/// * `a`   - The editor.
/// * `key` - The window's key.
fn refresh(a: &mut App, key: u64) {
    let autosave = a.autosave;
    let Some(wi) = a.wins.iter().position(|w| w.key == key) else {
        return;
    };
    let d = doc_index(a, &a.wins[wi]);
    let w = &a.wins[wi];
    w.window.set_autosave(autosave);
    let Some(d) = d else {
        w.window
            .set_doc_title("No document: use File > Open or New".into());
        w.window
            .set_rows(ModelRc::new(VecModel::from(Vec::<Row>::new())));
        return;
    };
    let session = &a.session;
    let open = &session.docs[d];
    let doc = &open.doc;
    let problems = session.problems(d);
    // The worst problem in each element, marked on it and every element
    // it is in, so a closed row shows that something inside it is wrong.
    let mut worst: std::collections::HashMap<Id, Severity> = Default::default();
    for p in &problems {
        for at in doc.ancestry(p.id) {
            let e = worst.entry(at).or_insert(p.severity);
            *e = (*e).min(p.severity);
        }
    }
    let mut rows = Vec::new();
    let mut order = Vec::new();
    let w = &a.wins[wi];
    build_rows(doc.root(), 0, w, &worst, &mut rows, &mut order);
    let window = &w.window;
    window.set_doc_title(
        open.path
            .file_name()
            .map_or("?".into(), |n| n.to_string_lossy().to_string())
            .into(),
    );
    let version = open.report.version.unwrap_or(Version::NEWEST);
    window.set_version(version.to_string().into());
    window.set_dirty(open.dirty());
    window.set_rows(ModelRc::new(VecModel::from(rows)));
    window.set_undo_label(session.undo_label(d).unwrap_or("").into());
    window.set_redo_label(session.redo_label(d).unwrap_or("").into());
    let others: Vec<SharedString> = Version::ALL
        .iter()
        .filter(|v| **v != version)
        .map(|v| v.to_string().into())
        .collect();
    window.set_other_versions(ModelRc::new(VecModel::from(others)));
    // The problems, or the usages being shown in their place.
    let rows: Vec<ProblemRow> = if w.usages.is_empty() {
        window.set_problems_title(
            format!(
                "Problems: {} error(s), {} warning(s)",
                problems
                    .iter()
                    .filter(|p| p.severity == Severity::Error)
                    .count(),
                problems
                    .iter()
                    .filter(|p| p.severity == Severity::Warning)
                    .count()
            )
            .into(),
        );
        problems
            .iter()
            .map(|p| ProblemRow {
                id: p.id as i32,
                severity: match p.severity {
                    Severity::Error => "error".into(),
                    Severity::Warning => "warning".into(),
                },
                text: format!(
                    "line {}: {}",
                    doc.line_of(p.id).unwrap_or(1),
                    p.message
                )
                .into(),
            })
            .collect()
    } else {
        w.usages
            .iter()
            .map(|l| {
                let o = &session.docs[l.doc];
                ProblemRow {
                    id: l.id as i32,
                    severity: "usage".into(),
                    text: format!(
                        "{} line {}",
                        o.path
                            .file_name()
                            .map_or("?".into(), |n| n.to_string_lossy()),
                        o.doc.line_of(l.id).unwrap_or(1)
                    )
                    .into(),
                }
            })
            .collect()
    };
    window.set_problems(ModelRc::new(VecModel::from(rows)));
    let sel = w.selected;
    let (
        attrs,
        name,
        docstr,
        has_el,
        is_comment,
        has_value,
        value,
        value_problem,
        comment,
    ) = details(session, d, sel);
    window.set_attrs(ModelRc::new(VecModel::from(attrs)));
    window.set_element_name(name.into());
    window.set_element_doc(docstr.into());
    window.set_has_element(has_el);
    window.set_is_comment(is_comment);
    window.set_has_value(has_value);
    window.set_value_text(value.into());
    window.set_value_problem(value_problem.into());
    window.set_comment_text(comment.into());
    window.set_is_definition(
        sel.is_some_and(|id| {
            session.index.def_at(Loc { doc: d, id }).is_some()
        }),
    );
    window.set_inserts(ModelRc::new(VecModel::from(insert_choices(
        session, d, sel,
    ))));
    window.set_templates(ModelRc::new(VecModel::from(template_choices(
        session, d, sel,
    ))));
    a.wins[wi].order = order;
}

/// Makes the outline's rows for an element and what is shown inside it.
///
/// # Arguments
///
/// * `e`     - The element.
/// * `depth` - How deep it is.
/// * `w`     - The window, which says what is open and selected.
/// * `worst` - The worst problem in each element.
/// * `rows`  - The rows, added to.
/// * `order` - The rows' ids, added to.
fn build_rows(
    e: &Element,
    depth: i32,
    w: &Win,
    worst: &std::collections::HashMap<Id, Severity>,
    rows: &mut Vec<Row>,
    order: &mut Vec<Id>,
) {
    let has_kids = e
        .children
        .iter()
        .any(|c| matches!(c, Node::Element(_) | Node::Comment(_)));
    let open = w.open.contains(&e.id);
    let label = match e.get("name") {
        Some(n) => format!("{}  {n}", e.local()),
        None => e.local().to_string(),
    };
    rows.push(Row {
        id: e.id as i32,
        depth,
        label: label.into(),
        kind: "element".into(),
        open,
        has_kids,
        mark: match worst.get(&e.id) {
            Some(Severity::Error) => "error".into(),
            Some(Severity::Warning) => "warning".into(),
            None => "".into(),
        },
        selected: w.selected == Some(e.id),
    });
    order.push(e.id);
    if !open {
        return;
    }
    for c in &e.children {
        match c {
            Node::Element(k) => build_rows(k, depth + 1, w, worst, rows, order),
            Node::Comment(k) => {
                let first =
                    k.text.trim().lines().next().unwrap_or("").to_string();
                rows.push(Row {
                    id: k.id as i32,
                    depth: depth + 1,
                    label: format!("<!-- {first} -->").into(),
                    kind: "comment".into(),
                    open: false,
                    has_kids: false,
                    mark: "".into(),
                    selected: w.selected == Some(k.id),
                });
                order.push(k.id);
            }
            _ => {}
        }
    }
}

/// What the details pane shows for the selection.
type Details = (
    Vec<AttrRow>,
    String,
    String,
    bool,
    bool,
    bool,
    String,
    String,
    String,
);

/// Works out what the details pane shows.
///
/// # Arguments
///
/// * `s`   - The session.
/// * `d`   - The document's index.
/// * `sel` - The selected element or comment.
///
/// # Returns
///
/// The attributes' rows, the element's name, its documentation, whether an
/// element is selected, whether a comment is, whether the element holds a
/// value, the value, what is wrong with the value, and the comment's text.
fn details(s: &Session, d: usize, sel: Option<Id>) -> Details {
    let empty = (
        Vec::new(),
        String::new(),
        String::new(),
        false,
        false,
        false,
        String::new(),
        String::new(),
        String::new(),
    );
    let Some(id) = sel else {
        return empty;
    };
    let o = &s.docs[d];
    match o.doc.node(id) {
        Some(Node::Comment(c)) => (
            Vec::new(),
            String::new(),
            "A comment.  It stays where it is, before the element after it, \
             when the elements around it are edited."
                .to_string(),
            false,
            true,
            false,
            String::new(),
            String::new(),
            c.text.clone(),
        ),
        Some(Node::Element(e)) => {
            let problems = s.problems(d);
            let mine = |attr: Option<&str>| -> String {
                problems
                    .iter()
                    .filter(|p| p.id == id && p.attr.as_deref() == attr)
                    .map(|p| p.message.clone())
                    .collect::<Vec<_>>()
                    .join("; ")
            };
            let version = o.report.version.unwrap_or(Version::NEWEST);
            let schema = version.schema();
            let decl = o.report.decls.get(&id).copied();
            let in_cmd = o.doc.ancestry(id).iter().any(|a| {
                o.doc
                    .element(*a)
                    .is_some_and(|x| x.local() == "CommandMetaData")
            });
            let mut rows = Vec::new();
            let mut doc = String::new();
            let mut has_value = false;
            if let Some(dc) = decl {
                let de = &schema.elements[dc];
                doc = de.doc.clone();
                match de.ty {
                    TypeRef::Complex(ci) => {
                        let ct = &schema.complex[ci];
                        has_value = ct.text.is_some();
                        if doc.is_empty() {
                            doc = ct.doc.clone();
                        }
                        for a in &ct.attrs {
                            let present = e.get(&a.name);
                            rows.push(AttrRow {
                                name: a.name.clone().into(),
                                value: present
                                    .clone()
                                    .or_else(|| a.default.clone())
                                    .unwrap_or_default()
                                    .into(),
                                required: a.required,
                                present: present.is_some(),
                                choices: ModelRc::new(VecModel::from(
                                    schema
                                        .enumeration(a.ty)
                                        .into_iter()
                                        .map(SharedString::from)
                                        .collect::<Vec<_>>(),
                                )),
                                doc: a.doc.clone().into(),
                                problem: mine(Some(&a.name)).into(),
                                is_ref: Kind::of_attr(
                                    &a.name,
                                    e.local(),
                                    in_cmd,
                                )
                                .is_some(),
                            });
                        }
                    }
                    TypeRef::Simple(_) => has_value = true,
                }
            }
            // Attributes the schema does not have are shown too, with what
            // is wrong with them, so they can be taken out.
            for a in &e.attrs {
                if crate::validate::is_namespace_attr(&a.name)
                    || rows.iter().any(|r| r.name == a.name.as_str())
                {
                    continue;
                }
                rows.push(AttrRow {
                    name: a.name.clone().into(),
                    value: a.value().into(),
                    required: false,
                    present: true,
                    choices: ModelRc::new(VecModel::from(
                        Vec::<SharedString>::new(),
                    )),
                    doc: "".into(),
                    problem: mine(Some(&a.name)).into(),
                    is_ref: false,
                });
            }
            let element_problem = mine(None);
            (
                rows,
                e.local().to_string(),
                doc,
                true,
                false,
                has_value,
                if has_value { e.text() } else { String::new() },
                element_problem,
                String::new(),
            )
        }
        _ => empty,
    }
}

/// The elements that may be put in the selected element, at some place.
///
/// # Arguments
///
/// * `s`   - The session.
/// * `d`   - The document's index.
/// * `sel` - The selected element.
///
/// # Returns
///
/// One choice for each, in the schema's order.
fn insert_choices(s: &Session, d: usize, sel: Option<Id>) -> Vec<Choice> {
    let Some(id) = sel else {
        return Vec::new();
    };
    let o = &s.docs[d];
    let Some(e) = o.doc.element(id) else {
        return Vec::new();
    };
    let Some(version) = o.report.version else {
        return Vec::new();
    };
    let schema = version.schema();
    let n = e.elements().count();
    let mut seen = Vec::new();
    for at in 0..=n {
        for dc in insertable(&o.doc, &o.report, id, at) {
            if !seen.contains(&dc) {
                seen.push(dc);
            }
        }
    }
    seen.into_iter()
        .map(|dc| Choice {
            title: format!("{} in {}", schema.elements[dc].name, e.local())
                .into(),
            index: dc as i32,
            about: schema.elements[dc].doc.clone().into(),
        })
        .collect()
}

/// The ready-made parts that may be put in the selected element.
///
/// # Arguments
///
/// * `s`   - The session.
/// * `d`   - The document's index.
/// * `sel` - The selected element.
///
/// # Returns
///
/// One choice for each.
fn template_choices(s: &Session, d: usize, sel: Option<Id>) -> Vec<Choice> {
    let Some(e) = sel.and_then(|id| s.docs[d].doc.element(id)) else {
        return Vec::new();
    };
    tools::TEMPLATES
        .iter()
        .enumerate()
        .filter(|(_, t)| t.parent == e.local())
        .map(|(i, t)| Choice {
            title: t.title.into(),
            index: i as i32,
            about: t.about.into(),
        })
        .collect()
}

/// Makes a change to a window's document, saying in its status line why
/// not if it is refused, and draws every window again.
///
/// # Arguments
///
/// * `app`   - The editor.
/// * `key`   - The window's key.
/// * `label` - What the change is, for the Undo menu item.
/// * `make`  - Makes the change's ops from the session and the document's
///             index, or says why it cannot be made.
fn change(
    app: &Shared,
    key: u64,
    label: &str,
    make: impl FnOnce(&Session, usize) -> Result<Vec<Change>, String>,
) {
    {
        let mut a = app.borrow_mut();
        let Some(w) = a.wins.iter().find(|w| w.key == key) else {
            return;
        };
        let Some(d) = doc_index(&a, w) else {
            return;
        };
        let r = make(&a.session, d).and_then(|c| a.session.apply(c, label));
        let msg = match r {
            Ok(()) => {
                a.last_change = Instant::now();
                format!("{label}: done")
            }
            Err(e) => e,
        };
        if let Some(w) = a.wins.iter().find(|w| w.key == key) {
            w.window.set_status(msg.into());
        }
    }
    refresh_all(app);
}

/// The window's document's index and its selection.
///
/// # Arguments
///
/// * `a`   - The editor.
/// * `key` - The window's key.
///
/// # Returns
///
/// Both, if there is a document and a selection.
fn selection(a: &App, key: u64) -> Option<(usize, Id)> {
    let w = a.wins.iter().find(|w| w.key == key)?;
    Some((doc_index(a, w)?, w.selected?))
}

/// Makes a new document, asking where and in which version.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `_`   - The window's key; a new document has a window of its own.
fn new_doc(app: &Shared, _: u64) {
    let Some(path) = rfd::FileDialog::new()
        .add_filter("XTCE", &["xtce", "xml"])
        .set_file_name("mission.xtce")
        .save_file()
    else {
        return;
    };
    let labels: Vec<String> =
        Version::ALL.iter().map(|v| format!("XTCE {v}")).collect();
    let refs: Vec<&str> = labels.iter().map(String::as_str).collect();
    ask(
        app,
        "Which XTCE version is the new document in?",
        &refs,
        move |app, i| {
            let v = Version::ALL[i.min(Version::ALL.len() - 1)];
            let name = path
                .file_stem()
                .map_or("mission".into(), |s| s.to_string_lossy().to_string());
            let r = app.borrow_mut().session.create(&path, v, &name);
            match r {
                Ok(d) => {
                    let p = app.borrow().session.docs[d].path.clone();
                    let _ = app.borrow_mut().session.save(d);
                    let _ = open_file(app, p);
                }
                Err(e) => status_all(&app.borrow(), &e),
            }
        },
    );
}

/// Opens a document, asking which.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key, whose status line says why if it fails.
fn open_doc(app: &Shared, key: u64) {
    let Some(files) = rfd::FileDialog::new()
        .add_filter("XTCE", &["xtce", "xml"])
        .pick_files()
    else {
        return;
    };
    for f in files {
        if let Err(e) = open_file(app, f) {
            if let Some(w) = app.borrow().wins.iter().find(|w| w.key == key) {
                w.window.set_status(e.into());
            }
        }
    }
}

/// Saves the window's document.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn save(app: &Shared, key: u64) {
    let mut a = app.borrow_mut();
    let Some(w) = a.wins.iter().find(|w| w.key == key) else {
        return;
    };
    let Some(d) = doc_index(&a, w) else {
        return;
    };
    let msg = match a.session.save(d) {
        Ok(()) => "Saved".to_string(),
        Err(e) => e,
    };
    a.wins
        .iter()
        .find(|w| w.key == key)
        .unwrap()
        .window
        .set_status(msg.into());
    drop(a);
    refresh_all(app);
}

/// Saves the window's document to another file, asking which.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn save_as(app: &Shared, key: u64) {
    let Some(path) = rfd::FileDialog::new()
        .add_filter("XTCE", &["xtce", "xml"])
        .save_file()
    else {
        return;
    };
    let mut a = app.borrow_mut();
    let Some(wi) = a.wins.iter().position(|w| w.key == key) else {
        return;
    };
    let Some(d) = doc_index(&a, &a.wins[wi]) else {
        return;
    };
    let msg = match a.session.save_as(d, &path) {
        Ok(()) => {
            a.wins[wi].path = Some(a.session.docs[d].path.clone());
            "Saved".to_string()
        }
        Err(e) => e,
    };
    a.wins[wi].window.set_status(msg.into());
    drop(a);
    refresh_all(app);
}

/// Closes the window's document, asking first if it has changes autosave
/// would not write.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn close_doc(app: &Shared, key: u64) {
    let (dirty, autosave) = {
        let a = app.borrow();
        let Some(w) = a.wins.iter().find(|w| w.key == key) else {
            return;
        };
        let dirty = doc_index(&a, w).is_some_and(|d| a.session.docs[d].dirty());
        (dirty, a.autosave)
    };
    if dirty && !autosave {
        ask(
            app,
            "The document has changes that are not saved.  Its undo history \
             goes when it is closed.",
            &["Save and close", "Close without saving", "Cancel"],
            move |app, i| match i {
                0 => {
                    save(app, key);
                    really_close(app, key);
                }
                1 => really_close(app, key),
                _ => {}
            },
        );
    } else {
        if dirty {
            save(app, key);
        }
        really_close(app, key);
    }
}

/// Closes a window and its document, and the editor with its last window.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn really_close(app: &Shared, key: u64) {
    let mut a = app.borrow_mut();
    let Some(wi) = a.wins.iter().position(|w| w.key == key) else {
        return;
    };
    if let Some(d) = doc_index(&a, &a.wins[wi]) {
        a.session.close(d);
    }
    let w = a.wins.remove(wi);
    let _ = w.window.hide();
    let last = a.wins.is_empty();
    drop(a);
    if last {
        let _ = slint::quit_event_loop();
    } else {
        refresh_all(app);
    }
}

/// Turns autosave on or off, and keeps the setting.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `_`   - The window's key; the setting is the editor's.
fn toggle_autosave(app: &Shared, _: u64) {
    let mut a = app.borrow_mut();
    a.autosave = !a.autosave;
    let on = a.autosave;
    let msg = match write_autosave(on) {
        Ok(()) if on => {
            "Autosave is on: changes are written as they are made".into()
        }
        Ok(()) => {
            "Autosave is off: a recovery copy of unsaved changes is kept".into()
        }
        Err(e) => format!(
            "Autosave is {}, but the setting could not be kept: {e}",
            if on { "on" } else { "off" }
        ),
    };
    status_all(&a, &msg);
    drop(a);
    refresh_all(app);
}

/// Undoes or redoes in the window's document.
///
/// # Arguments
///
/// * `app`  - The editor.
/// * `key`  - The window's key.
/// * `back` - True to undo, false to redo.
fn history(app: &Shared, key: u64, back: bool) {
    let mut a = app.borrow_mut();
    let Some(w) = a.wins.iter().find(|w| w.key == key) else {
        return;
    };
    let Some(d) = doc_index(&a, w) else {
        return;
    };
    let r = if back {
        a.session.undo(d)
    } else {
        a.session.redo(d)
    };
    if let Err(e) = r {
        a.wins
            .iter()
            .find(|w| w.key == key)
            .unwrap()
            .window
            .set_status(e.into());
    }
    a.last_change = Instant::now();
    // A selection that the step took away goes with it.
    for wi in 0..a.wins.len() {
        let gone = match (doc_index(&a, &a.wins[wi]), a.wins[wi].selected) {
            (Some(d), Some(id)) => a.session.docs[d].doc.node(id).is_none(),
            _ => false,
        };
        if gone {
            a.wins[wi].selected = None;
        }
    }
    drop(a);
    refresh_all(app);
}

/// Selects a row.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
/// * `id`  - The row's id.
fn select(app: &Shared, key: u64, id: i32) {
    {
        let mut a = app.borrow_mut();
        if let Some(w) = a.wins.iter_mut().find(|w| w.key == key) {
            w.selected = Some(id as Id);
            w.usages.clear();
        }
    }
    refresh_all(app);
}

/// Opens or closes a row.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
/// * `id`  - The row's id.
fn toggle(app: &Shared, key: u64, id: i32) {
    {
        let mut a = app.borrow_mut();
        if let Some(w) = a.wins.iter_mut().find(|w| w.key == key) {
            let id = id as Id;
            if !w.open.remove(&id) {
                w.open.insert(id);
            }
        }
    }
    refresh_all(app);
}

/// Sets an attribute of the selected element.
///
/// # Arguments
///
/// * `app`   - The editor.
/// * `key`   - The window's key.
/// * `name`  - The attribute.
/// * `value` - Its value.
fn set_attr(app: &Shared, key: u64, name: SharedString, value: SharedString) {
    let Some((_, id)) = selection(&app.borrow(), key) else {
        return;
    };
    change(app, key, &format!("set {name}"), |s, d| {
        Ok(edit::set_attr(&s.docs[d].doc, id, &name, &value)
            .map(|op| {
                vec![Change {
                    doc: d,
                    ops: vec![op],
                }]
            })
            .unwrap_or_default())
    });
}

/// Takes an attribute of the selected element out.
///
/// # Arguments
///
/// * `app`  - The editor.
/// * `key`  - The window's key.
/// * `name` - The attribute.
fn remove_attr(app: &Shared, key: u64, name: SharedString) {
    let Some((_, id)) = selection(&app.borrow(), key) else {
        return;
    };
    change(app, key, &format!("remove {name}"), |s, d| {
        Ok(edit::remove_attr(&s.docs[d].doc, id, &name)
            .map(|op| {
                vec![Change {
                    doc: d,
                    ops: vec![op],
                }]
            })
            .unwrap_or_default())
    });
}

/// Sets the selected element's value.
///
/// # Arguments
///
/// * `app`  - The editor.
/// * `key`  - The window's key.
/// * `text` - The value.
fn set_value(app: &Shared, key: u64, text: SharedString) {
    let Some((_, id)) = selection(&app.borrow(), key) else {
        return;
    };
    change(app, key, "set the value", |s, d| {
        Ok(edit::set_text(&s.docs[d].doc, id, &text)
            .map(|op| {
                vec![Change {
                    doc: d,
                    ops: vec![op],
                }]
            })
            .unwrap_or_default())
    });
}

/// Sets the selected comment's text.
///
/// # Arguments
///
/// * `app`  - The editor.
/// * `key`  - The window's key.
/// * `text` - The text.
fn set_comment(app: &Shared, key: u64, text: SharedString) {
    let Some((_, id)) = selection(&app.borrow(), key) else {
        return;
    };
    change(app, key, "edit the comment", |s, d| {
        let op = edit::set_comment(&s.docs[d].doc, id, &text)?;
        Ok(vec![Change {
            doc: d,
            ops: vec![op],
        }])
    });
}

/// Puts a new comment before the selected element.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn add_comment(app: &Shared, key: u64) {
    let Some((_, id)) = selection(&app.borrow(), key) else {
        return;
    };
    let mut new_id = 0;
    change(app, key, "add a comment", |s, d| {
        let doc = &s.docs[d].doc;
        let (parent, _) =
            doc.parent_of(id).ok_or("the root has no place before it")?;
        let p = doc.element(parent).ok_or("comments go inside the root")?;
        let at = p.elements().position(|c| c.id == id).ok_or("not found")?;
        new_id = doc.last_id + 1;
        let c = Node::Comment(Comment {
            id: new_id,
            text: " ".to_string(),
        });
        let op = edit::insert(doc, parent, at, vec![c]).ok_or("not found")?;
        Ok(vec![Change {
            doc: d,
            ops: vec![op],
        }])
    });
    // The new comment's id is past the document's last, which is moved on
    // so the id is never given again.
    let mut a = app.borrow_mut();
    if let Some((d, _)) = selection(&a, key) {
        let doc = &mut a.session.docs[d].doc;
        if doc.node(new_id).is_some() {
            doc.last_id = doc.last_id.max(new_id);
            if let Some(w) = a.wins.iter_mut().find(|w| w.key == key) {
                w.selected = Some(new_id);
            }
        }
    }
    drop(a);
    refresh_all(app);
}

/// Puts a new element, complete, in the selected element, at the first
/// place from the end its version allows it.
///
/// # Arguments
///
/// * `app`  - The editor.
/// * `key`  - The window's key.
/// * `decl` - The new element's declaration.
fn insert_decl(app: &Shared, key: u64, decl: i32) {
    let Some((_, parent)) = selection(&app.borrow(), key) else {
        return;
    };
    let mut made: Option<Id> = None;
    let mut last = 0;
    change(app, key, "insert", |s, d| {
        let o = &s.docs[d];
        let version = o.report.version.ok_or("no version")?;
        let p = o.doc.element(parent).ok_or("nothing selected")?;
        let n = p.elements().count();
        let at = (0..=n)
            .rev()
            .find(|&at| {
                insertable(&o.doc, &o.report, parent, at)
                    .contains(&(decl as usize))
            })
            .ok_or("that may not be put in here")?;
        let prefix = p.prefix().unwrap_or("").to_string();
        let mut m = Maker::new(version.schema(), &prefix, o.doc.last_id);
        let e = m
            .element(decl as usize)
            .ok_or("it cannot be made complete")?;
        made = Some(e.id);
        last = m.last_id;
        let op = edit::insert(&o.doc, parent, at, vec![Node::Element(e)])
            .ok_or("not found")?;
        Ok(vec![Change {
            doc: d,
            ops: vec![op],
        }])
    });
    finish_insert(app, key, parent, made, last);
}

/// Selects what was just put in, and moves the document's last id past
/// the ids it was made with.
///
/// # Arguments
///
/// * `app`    - The editor.
/// * `key`    - The window's key.
/// * `parent` - Where it was put in.
/// * `made`   - The new element's id, if one was made.
/// * `last`   - The last id it was made with.
fn finish_insert(
    app: &Shared,
    key: u64,
    parent: Id,
    made: Option<Id>,
    last: Id,
) {
    let mut a = app.borrow_mut();
    let Some(wi) = a.wins.iter().position(|w| w.key == key) else {
        return;
    };
    let Some(d) = doc_index(&a, &a.wins[wi]) else {
        return;
    };
    if let Some(id) = made {
        let doc = &mut a.session.docs[d].doc;
        if doc.node(id).is_some() {
            doc.last_id = doc.last_id.max(last);
            let w = &mut a.wins[wi];
            w.open.insert(parent);
            w.selected = Some(id);
        }
    }
    drop(a);
    refresh_all(app);
}

/// Puts a ready-made part in the selected element.
///
/// # Arguments
///
/// * `app`   - The editor.
/// * `key`   - The window's key.
/// * `index` - The part's index in [`tools::TEMPLATES`].
fn insert_template(app: &Shared, key: u64, index: i32) {
    let Some((_, parent)) = selection(&app.borrow(), key) else {
        return;
    };
    let Some(t) = tools::TEMPLATES.get(index as usize) else {
        return;
    };
    let mut made = None;
    let mut last = 0;
    change(app, key, &format!("insert {}", t.title), |s, d| {
        let o = &s.docs[d];
        let version = o.report.version.ok_or("no version")?;
        let p = o.doc.element(parent).ok_or("nothing selected")?;
        let prefix = p.prefix().unwrap_or("").to_string();
        let (e, l) = t.element(version, &prefix, o.doc.last_id)?;
        let schema = version.schema();
        let n = p.elements().count();
        let at = (0..=n)
            .rev()
            .find(|&at| {
                insertable(&o.doc, &o.report, parent, at)
                    .iter()
                    .any(|dc| schema.elements[*dc].name == e.local())
            })
            .ok_or("that may not be put in here")?;
        made = Some(e.id);
        last = l;
        let op = edit::insert(&o.doc, parent, at, vec![Node::Element(e)])
            .ok_or("not found")?;
        Ok(vec![Change {
            doc: d,
            ops: vec![op],
        }])
    });
    finish_insert(app, key, parent, made, last);
}

/// Takes the selected element or comment out.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn delete_selected(app: &Shared, key: u64) {
    let Some((_, id)) = selection(&app.borrow(), key) else {
        return;
    };
    change(app, key, "delete", |s, d| {
        let doc = &s.docs[d].doc;
        let op = match doc.node(id) {
            Some(Node::Comment(_)) => {
                let (parent, i) = doc.parent_of(id).ok_or("not found")?;
                let kids = doc.children(parent).ok_or("not found")?;
                let start = if i > 0 && kids[i - 1].is_space() {
                    i - 1
                } else {
                    i
                };
                let empty = doc.element(parent).is_some_and(|p| p.empty);
                edit::Op::Splice {
                    parent,
                    at: start,
                    removed: kids[start..=i].to_vec(),
                    inserted: Vec::new(),
                    empty: (empty, empty),
                }
            }
            _ => {
                edit::remove(doc, id)
                    .ok_or("the root cannot be taken out")?
                    .0
            }
        };
        Ok(vec![Change {
            doc: d,
            ops: vec![op],
        }])
    });
    let mut a = app.borrow_mut();
    if let Some(w) = a.wins.iter_mut().find(|w| w.key == key) {
        if w.selected == Some(id) {
            // The selection moves to the row before, if the delete was made.
            let i = w.order.iter().position(|x| *x == id);
            w.selected = i.and_then(|i| i.checked_sub(1)).map(|i| w.order[i]);
        }
    }
    let ok = a
        .wins
        .iter()
        .find(|w| w.key == key)
        .and_then(|w| doc_index(&a, w))
        .is_some_and(|d| a.session.docs[d].doc.node(id).is_none());
    if !ok {
        if let Some(w) = a.wins.iter_mut().find(|w| w.key == key) {
            w.selected = Some(id);
        }
    }
    drop(a);
    refresh_all(app);
}

/// Moves the selected element before the one before it, or after the one
/// after it, with its comments.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
/// * `up`  - True to move it up.
fn move_selected(app: &Shared, key: u64, up: bool) {
    let Some((_, id)) = selection(&app.borrow(), key) else {
        return;
    };
    change(
        app,
        key,
        if up { "move up" } else { "move down" },
        |s, d| {
            let doc = &s.docs[d].doc;
            let (parent, _) =
                doc.parent_of(id).ok_or("the root does not move")?;
            let p = doc.element(parent).ok_or("the root does not move")?;
            let at =
                p.elements().position(|c| c.id == id).ok_or("not found")?;
            let n = p.elements().count();
            let to = if up {
                at.checked_sub(1).ok_or("it is first already")?
            } else if at + 1 < n {
                at + 1
            } else {
                return Err("it is last already".into());
            };
            let (out, taken) = edit::remove(doc, id).ok_or("not found")?;
            let mut after = doc.clone();
            out.apply(&mut after)?;
            let back = edit::insert(&after, parent, to, edit::unlaid(taken))
                .ok_or("not found")?;
            Ok(vec![Change {
                doc: d,
                ops: vec![out, back],
            }])
        },
    );
}

/// Renames the selected definition and every reference to it, asking for
/// the new name.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn rename(app: &Shared, key: u64) {
    let Some((d, id)) = selection(&app.borrow(), key) else {
        return;
    };
    let old = app.borrow().session.docs[d]
        .doc
        .element(id)
        .and_then(|e| e.get("name"));
    let old = old.unwrap_or_default();
    let start = old.clone();
    input(
        app,
        &format!(
            "Rename {old}, and every reference to it in the open \
             documents, to:"
        ),
        &start,
        move |app, new| {
            change(app, key, &format!("rename {old} to {new}"), |s, _| {
                s.rename_changes(Loc { doc: d, id }, &new)
            });
        },
    );
}

/// Lists where the selected definition is used, in place of the problems.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn find_usages(app: &Shared, key: u64) {
    let mut a = app.borrow_mut();
    let Some((d, id)) = selection(&a, key) else {
        return;
    };
    let Some(def) = a.session.index.def_at(Loc { doc: d, id }) else {
        return;
    };
    let locs: Vec<Loc> =
        a.session.index.usages(def).iter().map(|r| r.loc).collect();
    let n = locs.len();
    if let Some(w) = a.wins.iter_mut().find(|w| w.key == key) {
        w.usages = locs;
        w.window.set_status(
            format!(
                "{n} usage(s); select a row in the outline to see the \
                 problems again"
            )
            .into(),
        );
    }
    drop(a);
    refresh_all(app);
    let a = app.borrow();
    if let Some(w) = a.wins.iter().find(|w| w.key == key) {
        w.window.set_problems_title(format!("Usages: {n}").into());
    }
}

/// Goes to what an attribute of the selected element refers to, opening
/// a window for its document if it is in another.
///
/// # Arguments
///
/// * `app`  - The editor.
/// * `key`  - The window's key.
/// * `name` - The attribute.
fn follow(app: &Shared, key: u64, name: SharedString) {
    let target = {
        let a = app.borrow();
        let Some((d, id)) = selection(&a, key) else {
            return;
        };
        a.session
            .index
            .refs
            .iter()
            .find(|r| {
                r.loc == Loc { doc: d, id }
                    && r.attr.as_deref() == Some(name.as_str())
            })
            .and_then(|r| r.target)
            .map(|t| a.session.index.defs[t].loc)
    };
    match target {
        Some(loc) => reveal(app, loc),
        None => {
            let a = app.borrow();
            if let Some(w) = a.wins.iter().find(|w| w.key == key) {
                w.window.set_status(
                    format!("{name} names nothing in the open documents")
                        .into(),
                );
            }
        }
    }
}

/// Shows an element in the window of its document, opening one if there
/// is none, with every row above it open.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `loc` - The element.
fn reveal(app: &Shared, loc: Loc) {
    let path = app.borrow().session.docs[loc.doc].path.clone();
    let key = app
        .borrow()
        .wins
        .iter()
        .find(|w| w.path.as_ref() == Some(&path))
        .map(|w| w.key);
    let key = key.unwrap_or_else(|| new_window(app, Some(path)));
    {
        let mut a = app.borrow_mut();
        let chain = a.session.docs[loc.doc].doc.ancestry(loc.id);
        if let Some(w) = a.wins.iter_mut().find(|w| w.key == key) {
            for at in chain {
                if at != loc.id {
                    w.open.insert(at);
                }
            }
            w.selected = Some(loc.id);
            w.usages.clear();
            let _ = w.window.show();
        }
    }
    refresh_all(app);
}

/// Selects the element a problem or usage row is about.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
/// * `id`  - The row's element.
fn pick_problem(app: &Shared, key: u64, id: i32) {
    let loc = {
        let a = app.borrow();
        let Some(w) = a.wins.iter().find(|w| w.key == key) else {
            return;
        };
        match w.usages.iter().find(|l| l.id == id as Id) {
            Some(l) => *l,
            None => match doc_index(&a, w) {
                Some(d) => Loc {
                    doc: d,
                    id: id as Id,
                },
                None => return,
            },
        }
    };
    let keep = !app
        .borrow()
        .wins
        .iter()
        .find(|w| w.key == key)
        .is_some_and(|w| w.usages.is_empty());
    let usages = if keep {
        app.borrow()
            .wins
            .iter()
            .find(|w| w.key == key)
            .map(|w| w.usages.clone())
    } else {
        None
    };
    reveal(app, loc);
    // Following a usage keeps the list of usages showing.
    if let Some(u) = usages {
        let mut a = app.borrow_mut();
        if let Some(w) = a.wins.iter_mut().find(|w| w.key == key) {
            w.usages = u;
        }
        drop(a);
        refresh_all(app);
    }
}

/// Starts converting the window's document to another version.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
/// * `v`   - The version, as its name.
fn start_convert(app: &Shared, key: u64, v: SharedString) {
    let Some(target) = Version::parse(&v) else {
        return;
    };
    let dialog = ConvertDialog::new().expect("the dialog can be made");
    dialog.set_target(v.clone());
    let a2 = app.clone();
    dialog.on_answer(move |k, t| {
        if let Some(c) = a2.borrow_mut().converting.as_mut() {
            c.answers.insert(k.to_string(), t.to_string());
        }
    });
    let a2 = app.clone();
    dialog.on_go(move || try_convert(&a2, true));
    let a2 = app.clone();
    dialog.on_cancel(move || {
        if let Some(c) = a2.borrow_mut().converting.take() {
            let _ = c.dialog.hide();
        }
    });
    let _ = dialog.show();
    app.borrow_mut().converting = Some(Converting {
        key,
        target,
        answers: Answers::new(),
        outcome: Outcome::default(),
        dialog,
    });
    try_convert(app, false);
}

/// Tries the conversion being set up, and takes its result if it went
/// through and `take` says to.
///
/// # Arguments
///
/// * `app`  - The editor.
/// * `take` - Whether to take the result if there is one.
fn try_convert(app: &Shared, take: bool) {
    let mut a = app.borrow_mut();
    let Some(mut c) = a.converting.take() else {
        return;
    };
    let Some(d) = a
        .wins
        .iter()
        .find(|w| w.key == c.key)
        .and_then(|w| doc_index(&a, w))
    else {
        return;
    };
    let had = c.outcome.doc.is_some();
    c.outcome = convert(&a.session.docs[d].doc, c.target, &c.answers);
    let o = &c.outcome;
    // The person must have seen what is lost before it is taken: a result
    // that appears only now, after answers, is shown before it is taken.
    if take
        && had
        && o.done()
        && (o.lost.is_empty() || c.dialog.get_accept_loss())
    {
        let label = format!("convert to XTCE {}", c.target);
        let r = a
            .session
            .take_conversion(d, o.doc.as_ref().unwrap(), &label);
        let msg = match r {
            Ok(()) => {
                format!("Converted to XTCE {}; Undo converts it back", c.target)
            }
            Err(e) => e,
        };
        let _ = c.dialog.hide();
        a.last_change = Instant::now();
        if let Some(w) = a.wins.iter_mut().find(|w| w.key == c.key) {
            w.window.set_status(msg.into());
            w.selected = None;
        }
        drop(a);
        refresh_all(app);
        return;
    }
    let mut summary = String::new();
    if o.done() {
        summary.push_str("The conversion can be made.\n\n");
    }
    for (title, list) in [
        ("Refused, so the conversion cannot be made", &o.refused),
        ("Lost: what the other version cannot say", &o.lost),
        ("Changed", &o.changed),
    ] {
        if !list.is_empty() {
            summary.push_str(&format!("{title} ({}):\n", list.len()));
            for l in list {
                summary.push_str(&format!("  - {l}\n"));
            }
            summary.push('\n');
        }
    }
    if !o.needs.is_empty() {
        summary.push_str("Answers are needed below before it can be made.\n");
    }
    let needs: Vec<NeedRow> = o
        .needs
        .iter()
        .map(|n| NeedRow {
            key: n.key.clone().into(),
            question: n.question.clone().into(),
            answer: c
                .answers
                .get(&n.key)
                .cloned()
                .or_else(|| n.suggestion.clone())
                .unwrap_or_default()
                .into(),
        })
        .collect();
    for n in &o.needs {
        if let Some(s) = &n.suggestion {
            c.answers.entry(n.key.clone()).or_insert(s.clone());
        }
    }
    c.dialog.set_summary(summary.into());
    c.dialog.set_needs(ModelRc::new(VecModel::from(needs)));
    c.dialog.set_loses(!o.lost.is_empty());
    c.dialog.set_refused(!o.refused.is_empty());
    a.converting = Some(c);
}

/// Shows the document's text in the Source tab.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn load_source(app: &Shared, key: u64) {
    let a = app.borrow();
    let Some(w) = a.wins.iter().find(|w| w.key == key) else {
        return;
    };
    if let Some(d) = doc_index(&a, w) {
        w.window.set_source(a.session.docs[d].doc.write().into());
    }
}

/// Takes the Source tab's text as the document.
///
/// # Arguments
///
/// * `app`  - The editor.
/// * `key`  - The window's key.
/// * `text` - The text.
fn apply_source(app: &Shared, key: u64, text: SharedString) {
    let mut a = app.borrow_mut();
    let Some(w) = a.wins.iter().find(|w| w.key == key) else {
        return;
    };
    let Some(d) = doc_index(&a, w) else {
        return;
    };
    let msg = match a.session.replace_text(d, &text, "edit the source") {
        Ok(()) => {
            a.last_change = Instant::now();
            "The source was taken".to_string()
        }
        Err(e) => e,
    };
    if let Some(w) = a.wins.iter_mut().find(|w| w.key == key) {
        w.window.set_status(msg.into());
        w.selected = None;
    }
    drop(a);
    refresh_all(app);
}

/// The mission's documents, written for the compiler.
///
/// # Arguments
///
/// * `a` - The editor.
///
/// # Returns
///
/// Where they were written.
///
/// # Errors
///
/// Says why they could not be.
fn mission(a: &App) -> Result<codec::Written, String> {
    let docs: Vec<(String, &crate::tree::Document)> = a
        .session
        .docs
        .iter()
        .map(|o| (o.path.display().to_string(), &o.doc))
        .collect();
    let refs: Vec<(&str, &crate::tree::Document)> =
        docs.iter().map(|(n, d)| (n.as_str(), *d)).collect();
    codec::write(&refs)
}

/// Shows each packet's layout, as the compiler lays it out.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn show_layout(app: &Shared, key: u64) {
    let r = mission(&app.borrow()).and_then(|m| codec::size_report(&m));
    match r {
        Ok(report) => {
            let packets: Vec<PacketRow> = codec::layouts(&report)
                .into_iter()
                .map(|l| {
                    let mut off = 0u64;
                    let fields: Vec<FieldRow> = l
                        .fields
                        .iter()
                        .map(|f| {
                            let r = FieldRow {
                                name: f.name.clone().into(),
                                bits: f.bits as i32,
                                offset: off as i32,
                                note: f.note.clone().into(),
                            };
                            off += f.bits;
                            r
                        })
                        .collect();
                    PacketRow {
                        name: l.name.into(),
                        size: l.size.into(),
                        fields: ModelRc::new(VecModel::from(fields)),
                        total: off as i32,
                    }
                })
                .collect();
            let w = LayoutWindow::new().expect("the window can be made");
            w.set_note(
                "Each field at its place, as the tcs-xtce compiler lays the \
                 packet out.  A field there only when a condition holds is \
                 drawn where it goes when it is there."
                    .into(),
            );
            w.set_packets(ModelRc::new(VecModel::from(packets)));
            let _ = w.show();
            app.borrow_mut().layouts.push(w);
            show_text(app, "Size report", &report);
        }
        Err(e) => status_of(app, key, &e),
    }
}

/// Shows a message in one window's status line.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
/// * `msg` - The message.
fn status_of(app: &Shared, key: u64, msg: &str) {
    if let Some(w) = app.borrow().wins.iter().find(|w| w.key == key) {
        w.window.set_status(msg.into());
    }
}

/// Opens the encode and decode window for the mission.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn show_tester(app: &Shared, key: u64) {
    let made = mission(&app.borrow()).and_then(codec::Tester::new);
    let t = match made {
        Ok(t) => Rc::new(t),
        Err(e) => return status_of(app, key, &e),
    };
    let listing = match t.list() {
        Ok(l) => l,
        Err(e) => return status_of(app, key, &e),
    };
    // The listing: a packet's line, then its fields' lines.
    let mut packets: Vec<(String, Vec<String>)> = Vec::new();
    for line in listing.lines() {
        let parts: Vec<&str> = line.split('\t').collect();
        match parts.as_slice() {
            ["PACKET", n] => packets.push((n.to_string(), Vec::new())),
            ["FIELD", f, ty] => {
                if let Some(p) = packets.last_mut() {
                    p.1.push(format!("{f}: {ty}"));
                }
            }
            _ => {}
        }
    }
    let w = TesterWindow::new().expect("the window can be made");
    let names: Vec<SharedString> =
        packets.iter().map(|p| p.0.clone().into()).collect();
    w.set_packets(ModelRc::new(VecModel::from(names)));
    if let Some(p) = packets.first() {
        w.set_chosen(p.0.clone().into());
        w.set_fields(p.1.join("\n").into());
    }
    let weak = w.as_weak();
    let fields = packets.clone();
    w.on_pick(move |name| {
        if let (Some(w), Some(p)) =
            (weak.upgrade(), fields.iter().find(|p| p.0 == name.as_str()))
        {
            w.set_fields(p.1.join("\n").into());
        }
    });
    let weak = w.as_weak();
    let t2 = t.clone();
    w.on_encode(move |p, v| {
        if let Some(w) = weak.upgrade() {
            match t2.encode(&p, &v) {
                Ok(hex) => {
                    w.set_hex(hex.clone().into());
                    w.set_result(format!("Encoded: {hex}").into());
                }
                Err(e) => w.set_result(format!("Not encoded: {e}").into()),
            }
        }
    });
    let weak = w.as_weak();
    let t2 = t.clone();
    w.on_decode(move |p, h| {
        if let Some(w) = weak.upgrade() {
            w.set_result(match t2.decode(&p, &h) {
                Ok(text) => text.into(),
                Err(e) => format!("Not decoded: {e}").into(),
            });
        }
    });
    let _ = w.show();
    app.borrow_mut().testers.push((w, t));
}

/// Compares the window's document with a file, asking which.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn compare(app: &Shared, key: u64) {
    let Some(path) = rfd::FileDialog::new()
        .add_filter("XTCE", &["xtce", "xml"])
        .pick_file()
    else {
        return;
    };
    let r = std::fs::read_to_string(&path)
        .map_err(|e| e.to_string())
        .and_then(|t| {
            crate::tree::Document::parse(&t).map_err(|e| e.to_string())
        });
    let other = match r {
        Ok(d) => d,
        Err(e) => {
            return status_of(app, key, &format!("{}: {e}", path.display()))
        }
    };
    let body = {
        let a = app.borrow();
        let Some(w) = a.wins.iter().find(|w| w.key == key) else {
            return;
        };
        let Some(d) = doc_index(&a, w) else {
            return;
        };
        let diffs = tools::diff(&other, &a.session.docs[d].doc);
        if diffs.is_empty() {
            "They mean the same: they differ, if at all, only in comments, \
             layout, attribute order and the order of sets."
                .to_string()
        } else {
            format!(
                "From {} to this document:\n\n{}",
                path.display(),
                diffs.join("\n")
            )
        }
    };
    show_text(app, "Comparison", &body);
}

/// Shows the document's digest.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn digest(app: &Shared, key: u64) {
    let body = {
        let a = app.borrow();
        let Some(w) = a.wins.iter().find(|w| w.key == key) else {
            return;
        };
        let Some(d) = doc_index(&a, w) else {
            return;
        };
        match tools::digest(&a.session.docs[d].doc) {
            Ok(h) => format!(
                "{h}\n\nThe MD5 digest of what this document means, as \
                 xtce-digest computes it: two programs built from documents \
                 with the same digest agree on every packet."
            ),
            Err(e) => e,
        }
    };
    show_text(app, "Digest", &body);
}

/// Opens the specification at the selected element.
///
/// # Arguments
///
/// * `app` - The editor.
/// * `key` - The window's key.
fn spec(app: &Shared, key: u64) {
    let r = {
        let a = app.borrow();
        let Some((d, id)) = selection(&a, key) else {
            return;
        };
        let o = &a.session.docs[d];
        let name = o
            .doc
            .element(id)
            .map(|e| e.local().to_string())
            .unwrap_or_default();
        tools::open_spec(o.report.version.unwrap_or(Version::NEWEST), &name)
    };
    if let Err(e) = r {
        status_of(app, key, &e);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use slint::Model;

    /// Copies shipped documents into a directory of their own and opens
    /// the editor on them, its windows drawn by Slint's testing backend.
    ///
    /// # Arguments
    ///
    /// * `files` - The documents' paths from the top of the repository.
    ///
    /// # Returns
    ///
    /// The editor, and the directory, which is removed when it is dropped.
    fn editor(files: &[&str]) -> (Shared, tempfile::TempDir) {
        i_slint_backend_testing::init_no_event_loop();
        let dir = tempfile::tempdir().unwrap();
        let mut paths = Vec::new();
        for f in files {
            let from = format!("{}/../{f}", env!("CARGO_MANIFEST_DIR"));
            let to = dir
                .path()
                .join(std::path::Path::new(f).file_name().unwrap());
            std::fs::copy(from, &to).unwrap();
            paths.push(to.display().to_string());
        }
        let app = start(&paths);
        app.borrow_mut().session.state_dir = Some(dir.path().join("state"));
        (app, dir)
    }

    /// A window's document window, by its place among the windows.
    ///
    /// # Arguments
    ///
    /// * `app` - The editor.
    /// * `i`   - The window's place.
    ///
    /// # Returns
    ///
    /// The window.
    fn win(app: &Shared, i: usize) -> DocWindow {
        app.borrow().wins[i].window.clone_strong()
    }

    /// The labels of a window's outline rows.
    ///
    /// # Arguments
    ///
    /// * `w` - The window.
    ///
    /// # Returns
    ///
    /// The labels, with their ids.
    fn rows(w: &DocWindow) -> Vec<(i32, String)> {
        w.get_rows()
            .iter()
            .map(|r| (r.id, r.label.to_string()))
            .collect()
    }

    /// Opens every row of a window's outline, so all are shown.
    ///
    /// # Arguments
    ///
    /// * `app` - The editor.
    /// * `i`   - The window's place.
    fn open_all(app: &Shared, i: usize) {
        let mut a = app.borrow_mut();
        let d = doc_index(&a, &a.wins[i]).unwrap();
        let mut ids = Vec::new();
        /// Gives the ids of an element and every element in it.
        ///
        /// # Arguments
        ///
        /// * `e`   - The element.
        /// * `out` - The ids, added to.
        fn all(e: &Element, out: &mut Vec<Id>) {
            out.push(e.id);
            for k in e.elements() {
                all(k, out);
            }
        }
        all(a.session.docs[d].doc.root(), &mut ids);
        a.wins[i].open.extend(ids);
        drop(a);
        refresh_all(app);
    }

    /// The id of the row whose label starts with a text.
    ///
    /// # Arguments
    ///
    /// * `w`    - The window.
    /// * `text` - The start of the label.
    ///
    /// # Returns
    ///
    /// The row's id.
    fn row(w: &DocWindow, text: &str) -> i32 {
        rows(w)
            .into_iter()
            .find(|(_, l)| l.starts_with(text))
            .unwrap_or_else(|| panic!("no row {text}: {:?}", rows(w)))
            .0
    }

    #[test]
    fn edits_are_held_to_the_version_and_undone() {
        let (app, _dir) = editor(&["examples/housekeeping/housekeeping.xtce"]);
        let w = win(&app, 0);
        let root = row(&w, "SpaceSystem");
        w.invoke_select(root);
        w.invoke_set_attr("name".into(), "a b".into());
        assert!(
            w.get_status().contains("does not allow"),
            "{}",
            w.get_status()
        );
        assert_eq!(w.get_undo_label(), "");
        w.invoke_set_attr("shortDescription".into(), "the probe".into());
        assert_eq!(w.get_undo_label(), "set shortDescription");
        assert!(w.get_dirty());
        w.invoke_undo();
        assert_eq!(w.get_undo_label(), "");
        assert_eq!(w.get_redo_label(), "set shortDescription");
    }

    #[test]
    fn inserts_only_what_fits_and_makes_it_complete() {
        let (app, _dir) = editor(&["examples/housekeeping/housekeeping.xtce"]);
        let w = win(&app, 0);
        open_all(&app, 0);
        w.invoke_select(row(&w, "ParameterTypeSet"));
        let inserts: Vec<String> = w
            .get_inserts()
            .iter()
            .map(|c| c.title.to_string())
            .collect();
        assert!(
            inserts
                .iter()
                .any(|t| t.starts_with("IntegerParameterType")),
            "{inserts:?}"
        );
        assert!(
            !inserts.iter().any(|t| t.starts_with("Parameter ")),
            "{inserts:?}"
        );
        let before = rows(&w).len();
        let int = w
            .get_inserts()
            .iter()
            .find(|c| c.title.starts_with("IntegerParameterType"))
            .unwrap();
        w.invoke_insert(int.index);
        assert!(rows(&w).len() > before);
        assert!(w.get_problems_title().starts_with("Problems: 0 error"));
        // The template of a CCSDS header goes in a SpaceSystem.
        w.invoke_select(row(&w, "SpaceSystem"));
        let t = w.get_templates().iter().next().expect("a template").index;
        w.invoke_insert_template(t);
        assert!(w.get_status().contains("done"), "{}", w.get_status());
        assert!(w.get_problems_title().starts_with("Problems: 0 error"));
    }

    #[test]
    fn follows_a_reference_into_another_document() {
        let (app, _dir) = editor(&[
            "examples/split-mission/dt.xtce",
            "examples/split-mission/pus.xtce",
            "examples/split-mission/rover.xtce",
        ]);
        let rover = win(&app, 2);
        open_all(&app, 2);
        let p = rover
            .get_rows()
            .iter()
            .find(|r| r.label.starts_with("Parameter "))
            .expect("a parameter")
            .id;
        rover.invoke_select(p);
        let wanted = rover
            .get_attrs()
            .iter()
            .find(|a| a.name == "parameterTypeRef")
            .map(|a| a.value.to_string())
            .unwrap();
        rover.invoke_follow("parameterTypeRef".into());
        // The type is shown selected in the window of the document that
        // defines it, whose name is the reference's last part.
        let name = wanted.rsplit('/').next().unwrap().to_string();
        let a = app.borrow();
        let shown = a.wins.iter().any(|w| {
            w.window
                .get_attrs()
                .iter()
                .any(|x| x.name == "name" && x.value == name.as_str())
                && w.window.get_element_name().ends_with("Type")
        });
        assert!(shown, "{wanted} was not shown");
    }

    #[test]
    fn converts_and_undoes_the_conversion() {
        let (app, _dir) = editor(&["examples/housekeeping/housekeeping.xtce"]);
        let w = win(&app, 0);
        w.invoke_convert("1.2".into());
        let dialog = app
            .borrow()
            .converting
            .as_ref()
            .unwrap()
            .dialog
            .clone_strong();
        assert!(
            dialog.get_summary().contains("can be made"),
            "{}",
            dialog.get_summary()
        );
        dialog.set_accept_loss(true);
        dialog.invoke_go();
        assert_eq!(w.get_version(), "1.2", "{}", w.get_status());
        w.invoke_undo();
        assert_eq!(w.get_version(), "1.3");
    }

    #[test]
    fn the_source_is_held_to_the_version_too() {
        let (app, _dir) = editor(&["examples/housekeeping/housekeeping.xtce"]);
        let w = win(&app, 0);
        w.invoke_load_source();
        let text = w.get_source().to_string();
        assert!(text.contains("<!--") || text.contains("SpaceSystem"));
        let bad = text.replacen(
            "name=\"Housekeeping\"",
            "bogus=\"1\" name=\"Housekeeping\"",
            1,
        );
        w.invoke_apply_source(bad.into());
        assert!(w.get_status().contains("not taken"), "{}", w.get_status());
        let good = text.replacen("name=\"Housekeeping\"", "name=\"House\"", 1);
        w.invoke_apply_source(good.into());
        assert_eq!(w.get_status(), "The source was taken");
        let _ = app;
    }
}
