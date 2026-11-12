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

/// Copies examples into a directory and opens the editor on them as one
/// mission, as `tcs-xtcee FILE...` does, each window the size the
/// pictures are.
///
/// # Arguments
///
/// * `dir`      - The directory.
/// * `examples` - The examples' paths from `examples/`.
///
/// # Returns
///
/// The editor and its windows, in the order of the examples.
///
/// # Panics
///
/// Panics if an example cannot be copied.
fn open_all(dir: &Path, examples: &[&str]) -> (Shared, Vec<DocWindow>) {
    std::fs::create_dir_all(dir).unwrap();
    let mut paths = Vec::new();
    for example in examples {
        let from = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../examples")
            .join(example);
        let to = dir.join(from.file_name().unwrap());
        std::fs::copy(&from, &to).expect("the copy is written");
        paths.push(to.display().to_string());
    }
    let app = start(&paths);
    let wins: Vec<DocWindow> = app
        .borrow()
        .wins
        .iter()
        .map(|w| w.window.clone_strong())
        .collect();
    for w in &wins {
        w.window().set_size(slint::PhysicalSize::new(WIDTH, HEIGHT));
    }
    (app, wins)
}

/// Adds an element to the selection, as a Ctrl+click on its name does.
///
/// # Arguments
///
/// * `w`     - The window.
/// * `label` - What its row says.
fn ctrl_pick(w: &DocWindow, label: &str) {
    w.invoke_pick(row_of(w, label).index, false, true);
}

/// Finds the grip of an element's row, the dots at its left that it is
/// dragged by: the grip, the triangle and the mark come before the name,
/// each with the row's spacing after it.
///
/// # Arguments
///
/// * `w`     - The window.
/// * `label` - What its row says.
///
/// # Returns
///
/// Where the grip's middle is.
fn grip(w: &DocWindow, label: &str) -> LogicalPosition {
    let e = all(w, label)
        .into_iter()
        .find(|e| e.absolute_position().y >= 60.0)
        .unwrap_or_else(|| panic!("no row {label:?}"));
    let p = e.absolute_position();
    let mark = if row_of(w, label).mark.is_empty() {
        0.0
    } else {
        18.0
    };
    LogicalPosition::new(
        p.x - 4.0 - mark - 4.0 - 16.0 - 4.0 - 5.0,
        p.y + e.size().height / 2.0,
    )
}

/// Moves the pointer, pressed, to a place, as a drag does.
///
/// # Arguments
///
/// * `w`     - The window.
/// * `place` - Where.
fn drag_to(w: &slint::Window, place: LogicalPosition) {
    w.dispatch_event(WindowEvent::PointerMoved { position: place });
    slint::platform::update_timers_and_animations();
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
    deleting(&dir.join("delete"));
    cutting(&dir.join("cut"));
    moving(&dir.join("move"));
    dragging(&dir.join("drag"));
    copying(&dir.join("copy"));
    copy_dragging(&dir.join("copy-drag"));
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

/// The pictures of deleting several rows at once, one of them named by a
/// reference.
///
/// # Arguments
///
/// * `dir` - The directory to open the document in.
fn deleting(dir: &Path) {
    let (app, w) = open(dir, "housekeeping/housekeeping.xtce", &[]);
    let win = w.window();
    w.invoke_depth_to(3, true);
    toggle(&w, "ParameterTypeSet");
    pick(&w, "Parameter  HeaterOn");
    ctrl_pick(&w, "Parameter  BusVoltage");
    snap(win, "delete-1", 40, 400);
    w.invoke_delete_selected();
    let ask = app.borrow().asks.last().expect("a question").clone_strong();
    ask.window()
        .set_size(slint::PhysicalSize::new(WIDTH * 3 / 4, 260));
    snap(ask.window(), "delete-2", 0, 260);
    ask.invoke_chosen(0);
    assert_eq!(w.get_undo_label(), "delete 2 elements");
    snap(win, "delete-3", 0, HEIGHT);
}

/// The pictures of cutting a row and pasting it somewhere else, which is
/// one move.
///
/// # Arguments
///
/// * `dir` - The directory to open the document in.
fn cutting(dir: &Path) {
    let (_app, w) = open(dir, "housekeeping/housekeeping.xtce", &[]);
    let win = w.window();
    w.invoke_depth_to(3, true);
    toggle(&w, "ParameterTypeSet");
    let on = "Parameter  HeaterOn";
    click(win, at(&w, on, 60.0));
    key(win, "x".into(), true);
    assert_eq!(w.get_undo_label(), "cut Parameter HeaterOn");
    click(win, at(&w, "Parameter  HeaterPower", 60.0));
    click(win, at(&w, "Edit", 0.0));
    snap(win, "cut-1", 0, 400);
    key(win, Key::Escape.into(), false);
    key(win, "v".into(), true);
    assert_eq!(
        w.get_undo_label(),
        "move Parameter HeaterOn after Parameter HeaterPower"
    );
    snap(win, "cut-2", 0, HEIGHT);
}

/// The pictures of moving a type to another document of a mission, which
/// writes again the references to it in every document.
///
/// # Arguments
///
/// * `dir` - The directory to open the documents in.
fn moving(dir: &Path) {
    let (_app, wins) = open_all(
        dir,
        &[
            "split-mission/dt.xtce",
            "split-mission/pus.xtce",
            "split-mission/rover.xtce",
        ],
    );
    let (dt, pus, rover) = (&wins[0], &wins[1], &wins[2]);
    dt.invoke_depth_to(3, true);
    let uint16 = "IntegerParameterType  uint16";
    click(dt.window(), at(dt, uint16, 60.0));
    key(dt.window(), "x".into(), true);
    rover.invoke_depth_to(3, true);
    toggle(rover, "ParameterSet");
    pick(rover, "ParameterTypeSet");
    click(rover.window(), at(rover, "Edit", 0.0));
    snap(rover.window(), "move-1", 0, 400);
    key(rover.window(), Key::Escape.into(), false);
    rover.invoke_paste(2);
    assert!(rover.get_status().contains("written again"));
    snap(rover.window(), "move-2", 0, HEIGHT);
    // A parameter that names the type, in Rover itself; those in the PUS
    // document are written again the same way.
    toggle(rover, "ParameterTypeSet");
    toggle(rover, "ParameterSet");
    let data = "Parameter  event-data";
    toggle(rover, data);
    pick(rover, data);
    snap(rover.window(), "move-3", 40, 400);
    assert!(pus
        .get_undo_label()
        .starts_with("move IntegerParameterType"));
}

/// The pictures of an attribute that cannot be removed, and of dragging
/// rows by a grip, to a place the schema allows and to one it does not.
///
/// # Arguments
///
/// * `dir` - The directory to open the document in.
fn dragging(dir: &Path) {
    let (_app, w) = open(dir, "housekeeping/housekeeping.xtce", &[]);
    let win = w.window();
    // The pointer resting on the Remove button of an attribute the schema
    // requires, which cannot be removed.
    let remove = at(&w, "Remove", 60.0);
    win.dispatch_event(WindowEvent::PointerMoved { position: remove });
    std::thread::sleep(Duration::from_millis(700));
    snap(win, "remove-1", 40, 220);
    win.dispatch_event(WindowEvent::PointerMoved {
        position: LogicalPosition::new(700.0, 400.0),
    });
    w.invoke_depth_to(3, true);
    toggle(&w, "ParameterTypeSet");
    pick(&w, "Parameter  HeaterOn");
    ctrl_pick(&w, "Parameter  Temperature");
    let from = grip(&w, "Parameter  HeaterOn");
    press(win, from);
    // Just above the line between HeaterPower's row and the next, in the
    // bottom quarter of HeaterPower's row: after it.
    let power = at(&w, "Parameter  HeaterPower", 60.0);
    let next = at(&w, "ContainerSet", power.y + 1.0);
    let below =
        LogicalPosition::new(power.x + 40.0, (power.y + next.y) / 2.0 - 1.0);
    drag_to(win, LogicalPosition::new(from.x + 20.0, from.y + 20.0));
    drag_to(win, below);
    snap(win, "drag-1", 40, 520);
    let types = at(&w, "ParameterTypeSet", 60.0);
    drag_to(win, LogicalPosition::new(types.x + 40.0, types.y - 6.0));
    snap(win, "drag-2", 40, 520);
    drag_to(win, below);
    release(win, below);
    assert_eq!(
        w.get_undo_label(),
        "move 2 elements after Parameter HeaterPower"
    );
    snap(win, "drag-3", 40, 520);
}

/// The pictures of copying a parameter to another document of a mission,
/// which writes again the reference in the copy so that it names the
/// type it named.
///
/// # Arguments
///
/// * `dir` - The directory to open the documents in.
fn copying(dir: &Path) {
    let (_app, wins) = open_all(
        dir,
        &[
            "split-mission/dt.xtce",
            "split-mission/pus.xtce",
            "split-mission/rover.xtce",
        ],
    );
    let (pus, rover) = (&wins[1], &wins[2]);
    rover.invoke_depth_to(3, true);
    let battery = "Parameter  battery";
    toggle(rover, battery);
    pick(rover, battery);
    rover.invoke_copy();
    snap(rover.window(), "copy-1", 40, 400);
    pus.invoke_depth_to(3, true);
    pick(pus, "Parameter  ccsds-version");
    pus.invoke_paste(0);
    assert!(pus.get_status().contains("written again"));
    toggle(pus, battery);
    snap(pus.window(), "copy-2", 0, HEIGHT);
}

/// The pictures of copying a row by dragging its grip with Ctrl held.
///
/// # Arguments
///
/// * `dir` - The directory to open the document in.
fn copy_dragging(dir: &Path) {
    let (_app, w) = open(dir, "housekeeping/housekeeping.xtce", &[]);
    let win = w.window();
    // Taller than the others, so that every entry is in view.
    win.set_size(slint::PhysicalSize::new(WIDTH, HEIGHT + 120));
    w.invoke_depth_to(3, true);
    toggle(&w, "ParameterTypeSet");
    toggle(&w, "ParameterSet");
    toggle(&w, "SequenceContainer  Status");
    let entries: Vec<Row> = w
        .get_rows()
        .iter()
        .filter(|r| r.label == "EntryList")
        .collect();
    w.invoke_toggle(entries[0].id);
    // The second entry, HeaterOn's, by its grip.
    let found = all(&w, "ParameterRefEntry");
    let second = found[1].absolute_position();
    let height = found[1].size().height;
    let from = LogicalPosition::new(second.x - 33.0, second.y + height / 2.0);
    press(win, from);
    let last = at(&w, "ParameterRefEntry", found[4].absolute_position().y);
    let below = LogicalPosition::new(last.x + 60.0, last.y + 10.0);
    drag_to(win, LogicalPosition::new(from.x + 20.0, from.y + 20.0));
    drag_to(win, below);
    snap(win, "copy-drag-1", 160, 520);
    // Ctrl held: the same place, a copy.
    let control: SharedString = Key::Control.into();
    win.dispatch_event(WindowEvent::KeyPressed {
        text: control.clone(),
    });
    drag_to(win, below);
    snap(win, "copy-drag-2", 160, 520);
    release(win, below);
    win.dispatch_event(WindowEvent::KeyReleased { text: control });
    assert_eq!(
        w.get_undo_label(),
        "copy ParameterRefEntry after ParameterRefEntry"
    );
    snap(win, "copy-drag-3", 160, 540);
}
