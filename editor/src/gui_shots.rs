//! The screenshots in the editor's manual, `docs/tcs-xtcee.rst`, made by
//! `make screenshots` and by nothing else: the test that makes them is
//! ignored unless asked for by name.
//!
//! The windows are the editor's own, opened on copies of the examples and
//! driven by the editor's own code: a click, a drag or a key is given to
//! the window as the pointer or the keyboard would give it, so that what
//! is drawn is what the editor shows.  They are drawn by Slint's software
//! renderer into memory, never on a display, and written as PNGs in
//! `docs/shots/`.  The elements are found by what they say, which needs
//! Slint's debug information: `make screenshots` builds with
//! `SLINT_EMIT_DEBUG_INFO=1`, in a target directory of its own.

use super::*;
use i_slint_backend_testing::{AccessibleRole, ElementHandle, ElementRoot};
use slint::platform::software_renderer::{
    MinimalSoftwareWindow, RepaintBufferType,
};
use slint::platform::{
    Key, Platform, PointerEventButton, WindowAdapter, WindowEvent,
};
use slint::{LogicalPosition, Model};
use std::path::Path;

/// A platform with no display, whose windows are drawn in memory.
struct Headless;

impl Platform for Headless {
    fn create_window_adapter(
        &self,
    ) -> Result<Rc<dyn WindowAdapter>, slint::PlatformError> {
        Ok(MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer))
    }
}

/// The width of a document window, in pixels.
const WIDTH: u32 = 800;
/// The height of a document window, in pixels.
const HEIGHT: u32 = 680;

/// Draws a window and writes a band of it, the whole width, as
/// `docs/shots/tcs-xtcee-NAME.png`.
///
/// # Arguments
///
/// * `w`      - The window.
/// * `name`   - What the picture is called, after `tcs-xtcee-`.
/// * `top`    - The first row of pixels written.
/// * `bottom` - The row after the last written, or past the window's
///              height for all the rows below `top`.
///
/// # Panics
///
/// Panics if the window cannot be drawn or the picture written.
fn snap(w: &slint::Window, name: &str, top: u32, bottom: u32) {
    slint::platform::update_timers_and_animations();
    let px = w.take_snapshot().expect("the window can be drawn");
    let width = px.width();
    let bottom = bottom.min(px.height());
    // The windows are opaque, so the alpha channel is left out.
    let band: Vec<u8> = px.as_bytes()
        [(top * width * 4) as usize..(bottom * width * 4) as usize]
        .chunks(4)
        .flat_map(|p| [p[0], p[1], p[2]])
        .collect();
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../docs/shots")
        .join(format!("tcs-xtcee-{name}.png"));
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    let file = std::fs::File::create(&path).expect("the picture is made");
    let mut png = png::Encoder::new(file, width, bottom - top);
    png.set_color(png::ColorType::Rgb);
    png.set_depth(png::BitDepth::Eight);
    png.set_compression(png::Compression::High);
    png.write_header()
        .and_then(|mut w| w.write_image_data(&band))
        .expect("the picture is written");
}

/// Finds the elements of a window that say a text, top first.
///
/// # Arguments
///
/// * `w`    - The window.
/// * `text` - What they say: a Text's text, or a field's value.
///
/// # Returns
///
/// The elements.
fn all(w: &impl ElementRoot, text: &str) -> Vec<ElementHandle> {
    let text = text.to_string();
    let mut found: Vec<ElementHandle> = w
        .root_element()
        .query_descendants()
        .match_predicate(move |e| {
            e.accessible_label().is_some_and(|l| l == text)
                || (e.accessible_role() == Some(AccessibleRole::TextInput)
                    && e.accessible_value().is_some_and(|v| v == text))
        })
        .find_all();
    found.sort_by(|a, b| {
        a.absolute_position().y.total_cmp(&b.absolute_position().y)
    });
    found
}

/// Finds the topmost element of a window that says a text, below a
/// height.
///
/// # Arguments
///
/// * `w`     - The window.
/// * `text`  - What it says.
/// * `below` - The height, in logical pixels, it is below.
///
/// # Returns
///
/// Where its middle is.
///
/// # Panics
///
/// Panics if there is none.
fn at(w: &impl ElementRoot, text: &str, below: f32) -> LogicalPosition {
    let e = all(w, text)
        .into_iter()
        .find(|e| e.absolute_position().y >= below)
        .unwrap_or_else(|| panic!("nothing says {text:?} below {below}"));
    let p = e.absolute_position();
    let s = e.size();
    LogicalPosition::new(p.x + s.width / 2.0, p.y + s.height / 2.0)
}

/// Moves the pointer to a place and presses its left button there.
///
/// # Arguments
///
/// * `w`     - The window.
/// * `place` - Where.
fn press(w: &slint::Window, place: LogicalPosition) {
    w.dispatch_event(WindowEvent::PointerMoved { position: place });
    w.dispatch_event(WindowEvent::PointerPressed {
        position: place,
        button: PointerEventButton::Left,
    });
}

/// Moves the pointer, pressed, to a place, and lets go of it there.
///
/// # Arguments
///
/// * `w`     - The window.
/// * `place` - Where.
fn release(w: &slint::Window, place: LogicalPosition) {
    w.dispatch_event(WindowEvent::PointerMoved { position: place });
    w.dispatch_event(WindowEvent::PointerReleased {
        position: place,
        button: PointerEventButton::Left,
    });
}

/// Clicks at a place.
///
/// # Arguments
///
/// * `w`     - The window.
/// * `place` - Where.
fn click(w: &slint::Window, place: LogicalPosition) {
    press(w, place);
    release(w, place);
    slint::platform::update_timers_and_animations();
}

/// Presses and lets go of a key, or of a key with Ctrl held.
///
/// # Arguments
///
/// * `w`    - The window.
/// * `key`  - The key, as the text it gives.
/// * `ctrl` - Whether Ctrl is held.
fn key(w: &slint::Window, key: SharedString, ctrl: bool) {
    let control: SharedString = Key::Control.into();
    if ctrl {
        w.dispatch_event(WindowEvent::KeyPressed {
            text: control.clone(),
        });
    }
    w.dispatch_event(WindowEvent::KeyPressed { text: key.clone() });
    w.dispatch_event(WindowEvent::KeyReleased { text: key });
    if ctrl {
        w.dispatch_event(WindowEvent::KeyReleased { text: control });
    }
}

/// Types a text, a key at a time.
///
/// # Arguments
///
/// * `w`    - The window.
/// * `text` - What is typed.
fn type_text(w: &slint::Window, text: &str) {
    for c in text.chars() {
        key(w, c.to_string().into(), false);
    }
}

/// Finds the row of an element in a window's outline.
///
/// # Arguments
///
/// * `w`     - The window.
/// * `label` - What the row says.
///
/// # Returns
///
/// The row.
///
/// # Panics
///
/// Panics if no element's row says it.
fn row_of(w: &DocWindow, label: &str) -> Row {
    w.get_rows()
        .iter()
        .find(|r| r.kind == "element" && r.label == label)
        .unwrap_or_else(|| panic!("no row {label:?}"))
}

/// Opens or closes an element, as a click on the triangle beside its
/// name does.
///
/// # Arguments
///
/// * `w`     - The window.
/// * `label` - What its row says.
fn toggle(w: &DocWindow, label: &str) {
    w.invoke_toggle(row_of(w, label).id);
}

/// Selects an element, as a click on its name does.
///
/// # Arguments
///
/// * `w`     - The window.
/// * `label` - What its row says.
fn pick(w: &DocWindow, label: &str) {
    w.invoke_pick(row_of(w, label).index, false, false);
}

/// Copies an example into a directory and opens the editor on it, as
/// `tcs-xtcee FILE` does, its window the size the pictures are.
///
/// # Arguments
///
/// * `dir`     - The directory.
/// * `example` - The example's path from `examples/`.
/// * `change`  - Changes made to its text first: each a text and what
///               replaces it.
///
/// # Returns
///
/// The editor and its window.
///
/// # Panics
///
/// Panics if the example cannot be copied or a text to change is not in
/// it.
fn open(
    dir: &Path,
    example: &str,
    change: &[(&str, &str)],
) -> (Shared, DocWindow) {
    std::fs::create_dir_all(dir).unwrap();
    let from = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../examples")
        .join(example);
    let mut text = std::fs::read_to_string(&from).expect("the example");
    for (old, new) in change {
        assert!(text.contains(old), "{example} has no {old}");
        text = text.replacen(old, new, 1);
    }
    let to = dir.join(from.file_name().unwrap());
    std::fs::write(&to, text).expect("the copy is written");
    let app = start(&[to.display().to_string()]);
    let w = app.borrow().wins[0].window.clone_strong();
    w.window().set_size(slint::PhysicalSize::new(WIDTH, HEIGHT));
    (app, w)
}

/// Makes every screenshot in the manual.
///
/// # Panics
///
/// Panics if the editor does not show what the manual says it does.
#[test]
#[ignore = "makes the manual's screenshots: make screenshots"]
fn screenshots() {
    slint::platform::set_platform(Box::new(Headless))
        .expect("no other platform is set");
    // The documents, the settings and the recovery copies are all kept in
    // a directory of their own, whose name is in the status line.
    let dir = std::env::temp_dir().join("tcs-xtcee-shots");
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    std::env::set_var("XDG_CONFIG_HOME", dir.join("config"));
    std::env::set_var("XDG_STATE_HOME", dir.join("state"));
    opening(&dir.join("open"));
    problems(&dir.join("problems"));
    converting(&dir.join("convert"));
    let _ = std::fs::remove_dir_all(&dir);
}

/// The pictures of opening a document, finding a way round it, editing
/// it, a change refused, inserting and saving, all of one document.
///
/// # Arguments
///
/// * `dir` - The directory to open it in.
fn opening(dir: &Path) {
    let (_app, w) = open(dir, "housekeeping/housekeeping.xtce", &[]);
    let win = w.window();
    snap(win, "open-1", 0, HEIGHT);

    // Dragging the selection's handle right, to depth 3.
    let handle = at(&w, "\u{2194}", 60.0);
    press(win, handle);
    let three = LogicalPosition::new(handle.x + 60.0, handle.y);
    win.dispatch_event(WindowEvent::PointerMoved { position: three });
    snap(win, "open-2", 40, 150);
    release(win, three);
    snap(win, "open-3", 40, 400);

    // Closing the types, and resting the pointer on a name.
    toggle(&w, "ParameterTypeSet");
    let set = at(&w, "ParameterSet", 60.0);
    win.dispatch_event(WindowEvent::PointerMoved { position: set });
    std::thread::sleep(Duration::from_millis(700));
    snap(win, "browse-1", 40, 360);
    win.dispatch_event(WindowEvent::PointerMoved {
        position: LogicalPosition::new(700.0, 300.0),
    });

    // Opening a parameter, selecting it, and following its type.
    let temperature = "Parameter  Temperature";
    toggle(&w, temperature);
    pick(&w, temperature);
    snap(win, "browse-2", 40, 380);
    let row = at(&w, temperature, 60.0).y;
    click(win, at(&w, "Go to", row));
    snap(win, "browse-3", 40, 380);

    // Adding an attribute to the parameter.
    toggle(&w, "ParameterTypeSet");
    pick(&w, temperature);
    let row = at(&w, temperature, 60.0).y;
    let adder = at(&w, "add an attribute", row);
    click(win, adder);
    snap(win, "edit-1", 40, 380);
    // The list drops below the field, one line to a value, the first
    // shortDescription; what is in the list is not found by its words.
    click(win, LogicalPosition::new(adder.x, adder.y + 23.0));
    let row = at(&w, temperature, 60.0).y;
    click(win, at(&w, "(not given)", row));
    type_text(win, "The heater plate's temperature");
    snap(win, "edit-2", 40, 400);
    key(win, Key::Return.into(), false);
    assert_eq!(w.get_undo_label(), "set shortDescription");
    click(win, at(&w, "Edit", 0.0));
    snap(win, "edit-3", 0, 330);
    key(win, Key::Escape.into(), false);

    // A name the version does not allow.
    let row = at(&w, temperature, 60.0).y;
    click(win, at(&w, "Temperature", row));
    key(win, "a".into(), true);
    type_text(win, "Plate temperature");
    snap(win, "refuse-1", 40, 400);
    key(win, Key::Return.into(), false);
    assert!(w.get_status().contains("does not allow"));
    snap(win, "refuse-2", 0, HEIGHT);

    // Inserting a type.
    toggle(&w, temperature);
    pick(&w, "ParameterTypeSet");
    click(win, at(&w, "Insert", 0.0));
    snap(win, "insert-1", 0, 545);
    key(win, Key::Escape.into(), false);
    let int = w
        .get_inserts()
        .iter()
        .find(|c| c.title.starts_with("IntegerParameterType"))
        .expect("an integer type may be put in");
    w.invoke_insert(int.index);
    let made = w
        .get_rows()
        .iter()
        .find(|r| r.selected && r.kind == "element")
        .expect("what was put in is selected")
        .label
        .to_string();
    toggle(&w, &made);
    snap(win, "insert-2", 40, 400);

    // Saving.
    click(win, at(&w, "File", 0.0));
    snap(win, "save-1", 0, 300);
    key(win, Key::Escape.into(), false);
}

/// The pictures of a document with problems in it, and of putting one
/// right.
///
/// # Arguments
///
/// * `dir` - The directory to open it in.
fn problems(dir: &Path) {
    let (_app, w) = open(
        dir,
        "housekeeping/housekeeping.xtce",
        &[
            (
                "parameterTypeRef=\"PowerType\"",
                "parameterTypeRef=\"PowrType\"",
            ),
            ("coefficient=\"0.125\"", "coefficient=\"one eighth\""),
        ],
    );
    let win = w.window();
    snap(win, "problems-1", 0, HEIGHT);
    // Picking the problem with the reference, a warning, selects its
    // element, which opened shows the problem under the attribute.
    let problem = w
        .get_problems()
        .iter()
        .find(|p| p.text.contains("PowrType"))
        .expect("the reference names nothing");
    assert_eq!(problem.severity, "warning");
    click(win, at(&w, &format!("Warning: {}", problem.text), 0.0));
    let power = "Parameter  HeaterPower";
    toggle(&w, power);
    snap(win, "problems-2", 0, HEIGHT);

    // Putting it right.
    let row = at(&w, power, 60.0).y;
    click(win, at(&w, "PowrType", row));
    key(win, "a".into(), true);
    type_text(win, "PowerType");
    key(win, Key::Return.into(), false);
    assert_eq!(w.get_problems_title(), "Problems: 1 error(s), 0 warning(s)");
    snap(win, "problems-3", 0, HEIGHT);
}

/// The picture of converting a document to another version.
///
/// # Arguments
///
/// * `dir` - The directory to open it in.
fn converting(dir: &Path) {
    let (app, w) = open(dir, "simple-string/simple_string.xtce", &[]);
    let win = w.window();
    click(win, at(&w, "Version", 0.0));
    snap(win, "convert-1", 0, 130);
    key(win, Key::Escape.into(), false);
    w.invoke_convert("1.1".into());
    let dialog = app
        .borrow()
        .converting
        .as_ref()
        .expect("the conversion dialog is open")
        .dialog
        .clone_strong();
    dialog
        .window()
        .set_size(slint::PhysicalSize::new(WIDTH, 400));
    snap(dialog.window(), "convert-2", 0, 400);
}
