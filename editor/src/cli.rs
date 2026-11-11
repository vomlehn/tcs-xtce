//! The editor's commands for use without its windows, as from a Makefile
//! or a CI job.  They do what the editor's menus do, through the same code.

use std::process::ExitCode;

use crate::convert::{convert, Answers};
use crate::fetch::Missing;
use crate::schema::Version;
use crate::tree::Document;
use crate::validate::{check, Severity};

/// What `tcs-xtcee --help` prints.
const USAGE: &str = "\
usage: tcs-xtcee [FILE...]             open the files in the editor
       tcs-xtcee check FILE...         check each file against its XTCE
                                       version's schema
       tcs-xtcee convert FILE --to VERSION [--allow-loss]
                 [--set KEY=VALUE]... [-o OUT]
                                       convert FILE to another XTCE
                                       version, writing OUT (FILE itself
                                       if no -o); --allow-loss lets a
                                       downgrade take out what the older
                                       version cannot say, and --set
                                       answers what it asks
       tcs-xtcee sample VERSION SEED   print a random valid document of
                                       an XTCE version, for testing

A command that needs an XTCE schema not yet on this machine asks, at a
terminal, before downloading it from OMG; elsewhere it stops, unless
given --download, which downloads it without asking.
";

/// Whether a command line names one of the commands, rather than files to
/// open in the editor.
///
/// # Arguments
///
/// * `args` - The arguments, without the program's name.
///
/// # Returns
///
/// True for a command.
pub fn is_command(args: &[String]) -> bool {
    matches!(
        args.iter().map(String::as_str).find(|a| *a != "--download"),
        Some("check" | "convert" | "sample" | "-h" | "--help")
    )
}

/// Runs the command a command line gives.
///
/// # Arguments
///
/// * `args` - The arguments, without the program's name.
///
/// # Returns
///
/// Success, or failure if the command found a problem or could not run.
pub fn run(args: &[String]) -> ExitCode {
    let download = args.iter().any(|a| a == "--download");
    let args: Vec<String> = args
        .iter()
        .filter(|a| *a != "--download")
        .cloned()
        .collect();
    match args.first().map(String::as_str) {
        Some("check") => check_files(&args[1..], download),
        Some("convert") => convert_file(&args[1..], download),
        Some("sample") => sample(&args[1..], download),
        Some("-h" | "--help") => {
            print!("{USAGE}");
            ExitCode::SUCCESS
        }
        _ => {
            eprint!("{USAGE}");
            ExitCode::FAILURE
        }
    }
}

/// Reads the schemas of some versions, downloading any that is not on
/// this machine if the person agrees.
///
/// # Arguments
///
/// * `versions` - The versions.
/// * `download` - Whether `--download` was given, which agrees to every
///                download without asking.
///
/// # Errors
///
/// Says why a schema could not be had: not agreed to, with how to put it
/// in place, or its download failed.
fn ensure(versions: &[Version], download: bool) -> Result<(), String> {
    for &v in versions {
        let Some(m) = v.missing()? else {
            v.load()?;
            continue;
        };
        if !download && !agreed(&m) {
            return Err(format!(
                "{}, or give --download to have it downloaded",
                m.refused()
            ));
        }
        v.download(&m)?;
    }
    Ok(())
}

/// Asks on the terminal whether to download a file.
///
/// # Arguments
///
/// * `m` - The file.
///
/// # Returns
///
/// True if the answer was yes.  Without a terminal to ask at, as in a
/// Makefile or a CI job, nothing is asked and the answer is no, so that
/// nothing is downloaded that nobody agreed to.
fn agreed(m: &Missing) -> bool {
    use std::io::{BufRead, IsTerminal, Write};
    if !std::io::stdin().is_terminal() {
        return false;
    }
    eprint!("tcs-xtcee: {} [y/N] ", m.question());
    let _ = std::io::stderr().flush();
    let mut answer = String::new();
    if std::io::stdin().lock().read_line(&mut answer).is_err() {
        return false;
    }
    matches!(answer.trim().to_lowercase().as_str(), "y" | "yes")
}

/// Checks files and prints each problem with its file and line.
///
/// # Arguments
///
/// * `files`    - The files.
/// * `download` - Whether to download a schema without asking.
///
/// # Returns
///
/// Success if no file has an error.
fn check_files(files: &[String], download: bool) -> ExitCode {
    let mut failed = false;
    for f in files {
        let bytes = match std::fs::read(f) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("{f}: {e}");
                failed = true;
                continue;
            }
        };
        let doc = match Document::from_bytes(&bytes) {
            Ok(d) => d,
            Err(e) => {
                println!("{f}:{}: error: {}", e.place.line, e.message);
                failed = true;
                continue;
            }
        };
        if let Ok(v) = Version::of_document(&doc) {
            if let Err(e) = ensure(&[v], download) {
                println!("{f}:1: error: {e}");
                failed = true;
                continue;
            }
        }
        let report = check(&doc);
        for p in &report.problems {
            let line = doc.line_of(p.id).unwrap_or(1);
            let sev = match p.severity {
                Severity::Error => {
                    failed = true;
                    "error"
                }
                Severity::Warning => "warning",
            };
            println!("{f}:{line}: {sev}: {}", p.message);
        }
    }
    if failed {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}

/// Converts a file to another XTCE version, as the editor's Version menu
/// does, and prints what was changed, lost or is needed.
///
/// # Arguments
///
/// * `args`     - The file, `--to VERSION`, and the options in [`USAGE`].
/// * `download` - Whether to download a schema without asking.
///
/// # Returns
///
/// Success if the file was converted and written.
fn convert_file(args: &[String], download: bool) -> ExitCode {
    let mut file = None;
    let mut to = None;
    let mut out = None;
    let mut allow_loss = false;
    let mut answers = Answers::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--to" => to = it.next().and_then(|v| Version::parse(v)),
            "-o" => out = it.next().cloned(),
            "--allow-loss" => allow_loss = true,
            "--set" => {
                let Some((k, v)) = it.next().and_then(|kv| kv.split_once('='))
                else {
                    eprintln!("--set takes KEY=VALUE, the key as asked");
                    return ExitCode::FAILURE;
                };
                answers.insert(k.to_string(), v.to_string());
            }
            f if file.is_none() => file = Some(f.to_string()),
            other => {
                eprintln!("{other}: one file is converted at a time");
                return ExitCode::FAILURE;
            }
        }
    }
    let (Some(file), Some(to)) = (file, to) else {
        eprintln!(
            "convert needs a file and --to with one of {}",
            Version::ALL.map(|v| v.to_string()).join(", ")
        );
        return ExitCode::FAILURE;
    };
    let doc = match std::fs::read(&file)
        .map_err(|e| e.to_string())
        .and_then(|b| Document::from_bytes(&b).map_err(|e| e.to_string()))
    {
        Ok(d) => d,
        Err(e) => {
            eprintln!("{file}: {e}");
            return ExitCode::FAILURE;
        }
    };
    // The document's own version's schema checks it, and the target's
    // the conversion.
    let mut versions = vec![to];
    versions.extend(Version::of_document(&doc).ok());
    if let Err(e) = ensure(&versions, download) {
        eprintln!("{file}: {e}");
        return ExitCode::FAILURE;
    }
    let o = convert(&doc, to, &answers);
    for c in &o.changed {
        println!("{file}: changed: {c}");
    }
    for l in &o.lost {
        println!("{file}: lost: {l}");
    }
    for n in &o.needs {
        println!("{file}: needs: {}", n.question);
        println!("    answer with --set '{}=VALUE'", n.key);
    }
    for r in &o.refused {
        println!("{file}: refused: {r}");
    }
    let Some(new) = o.doc else {
        return ExitCode::FAILURE;
    };
    if !o.lost.is_empty() && !allow_loss {
        println!(
            "{file}: not written: XTCE {to} cannot say what is listed as \
             lost; give --allow-loss to convert without it"
        );
        return ExitCode::FAILURE;
    }
    let target = out.unwrap_or(file);
    match crate::save::save(std::path::Path::new(&target), &new.write()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{target}: {e}");
            ExitCode::FAILURE
        }
    }
}

/// Prints a random valid document, as the converter's tests use.
///
/// # Arguments
///
/// * `args`     - The version and the seed.
/// * `download` - Whether to download the schema without asking.
///
/// # Returns
///
/// Success, or failure if the arguments are not a version and a number,
/// or the version's schema cannot be had.
fn sample(args: &[String], download: bool) -> ExitCode {
    let v = args.first().and_then(|v| Version::parse(v));
    let seed = args.get(1).and_then(|s| s.parse::<u64>().ok());
    match (v, seed) {
        (Some(v), Some(seed)) => {
            if let Err(e) = ensure(&[v], download) {
                eprintln!("tcs-xtcee: {e}");
                return ExitCode::FAILURE;
            }
            print!("{}", crate::make::sample(v, seed));
            ExitCode::SUCCESS
        }
        _ => {
            eprintln!("sample takes a version and a whole number seed");
            ExitCode::FAILURE
        }
    }
}
