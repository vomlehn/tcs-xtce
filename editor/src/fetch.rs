//! OMG's files, found where they were kept, or downloaded when first
//! needed, once the person has said they may be.
//!
//! The XTCE schemas the editor holds documents to, and the specifications
//! it opens at an element, are OMG's, and OMG does not allow them to be
//! redistributed.  So none is built into the editor or packaged with it.
//! Each is downloaded from OMG with `curl` the first time it is needed --
//! a schema when a document of its version is opened, made or converted
//! to, a specification when it is asked for -- and only after the person
//! has agreed, the editor asking in a dialog and the commands on the
//! terminal.  It is kept in the user's data directory,
//! `$XDG_DATA_HOME/tcs-xtcee` or `~/.local/share/tcs-xtcee`, for every
//! later run, so that each file is asked about once.  A build in a
//! checkout uses the copies `make -C references editor` downloaded there
//! instead, so that the tests need no network and ask nothing.

use std::path::{Path, PathBuf};
use std::process::Command;

/// One of OMG's files that is not on this machine yet.
#[derive(Clone, Debug)]
pub struct Missing {
    /// What it is, as a sentence names it: "the XTCE 1.3 schema".
    pub what: String,
    /// Where OMG publishes it.
    pub url: String,
    /// Where it is kept once downloaded.
    pub path: PathBuf,
}

impl Missing {
    /// Asks whether to download it, as the dialog and the terminal put it.
    ///
    /// # Returns
    ///
    /// The question.
    pub fn question(&self) -> String {
        format!(
            "{} is not on this machine.  OMG does not allow it to be \
             shipped with the editor, so it has to be downloaded from \
             {} and kept as {}.  Download it?",
            capitalised(&self.what),
            self.url,
            self.path.display()
        )
    }

    /// Says what was not done for want of it, and how to put it in place
    /// by hand.
    ///
    /// # Returns
    ///
    /// The message.
    pub fn refused(&self) -> String {
        format!(
            "{} is not on this machine and was not downloaded: download {} \
             yourself and save it as {}",
            self.what,
            self.url,
            self.path.display()
        )
    }
}

/// Gives a sentence's first word a capital.
///
/// # Arguments
///
/// * `s` - The sentence.
///
/// # Returns
///
/// It, capitalised.
fn capitalised(s: &str) -> String {
    let mut c = s.chars();
    c.next()
        .map(|f| f.to_uppercase().chain(c).collect())
        .unwrap_or_default()
}

/// Where the user keeps the editor's own files: `$XDG_DATA_HOME/tcs-xtcee`
/// or `~/.local/share/tcs-xtcee`.
///
/// # Returns
///
/// The directory, which may not exist, or `None` without a home
/// directory.
pub fn user_data() -> Option<PathBuf> {
    let base = match std::env::var_os("XDG_DATA_HOME") {
        Some(d) if !d.is_empty() => PathBuf::from(d),
        _ => PathBuf::from(std::env::var_os("HOME")?).join(".local/share"),
    };
    Some(base.join("tcs-xtcee"))
}

/// Finds one of OMG's files, without downloading anything.
///
/// # Arguments
///
/// * `name` - Its file name, as `references/` and the data directory
///            keep it, such as `xtce-1.3.xsd`.
/// * `url`  - Where OMG publishes it.
/// * `what` - What it is, as a sentence names it.
///
/// # Returns
///
/// Where it is, or, if it is nowhere yet, what to download.
///
/// # Errors
///
/// Says so when there is no home directory to keep it in.
pub fn find(
    name: &str,
    url: &str,
    what: &str,
) -> Result<Result<PathBuf, Missing>, String> {
    let checkout = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../references")
        .join(name);
    if checkout.exists() {
        return Ok(Ok(checkout));
    }
    let dir = user_data().ok_or_else(|| {
        format!(
            "{what} is needed, and there is no home directory to keep it \
             in: set HOME or XDG_DATA_HOME"
        )
    })?;
    let kept = dir.join(name);
    if kept.exists() {
        return Ok(Ok(kept));
    }
    Ok(Err(Missing {
        what: what.to_string(),
        url: url.to_string(),
        path: kept,
    }))
}

/// Downloads one of OMG's files, which the person has agreed to.
///
/// # Arguments
///
/// * `m`     - The file, as [`find`] gave it.
/// * `check` - Says whether downloaded bytes are the file meant, and
///             what they are if not: a web server's error page is not.
///
/// # Returns
///
/// Where the file is.
///
/// # Errors
///
/// Says why it could not be downloaded, and how to put it in place by
/// hand.
pub fn download(
    m: &Missing,
    check: impl Fn(&[u8]) -> Result<(), String>,
) -> Result<PathBuf, String> {
    let by_hand = format!(
        "download {} yourself and save it as {}",
        m.url,
        m.path.display()
    );
    let dir = m.path.parent().unwrap_or(Path::new("."));
    std::fs::create_dir_all(dir).map_err(|e| {
        format!("{} cannot be made ({e}); {by_hand}", dir.display())
    })?;
    eprintln!("tcs-xtcee: downloading {} to {}", m.url, m.path.display());
    // Written beside the file and renamed into place, so that a download
    // cut short is never taken for the file.
    let mut part = m.path.clone().into_os_string();
    part.push(".part");
    let part = PathBuf::from(part);
    let got = Command::new("curl")
        .args(["--fail", "--silent", "--show-error", "--location"])
        .args(["--max-time", "300", "--output"])
        .arg(&part)
        .arg(&m.url)
        .output();
    let fail = |why: String| {
        let _ = std::fs::remove_file(&part);
        Err(format!(
            "{} could not be downloaded: {why}; {by_hand}",
            m.what
        ))
    };
    match got {
        Err(e) => return fail(format!("curl could not be run ({e})")),
        Ok(o) if !o.status.success() => {
            return fail(String::from_utf8_lossy(&o.stderr).trim().to_string())
        }
        Ok(_) => {}
    }
    let bytes = match std::fs::read(&part) {
        Ok(b) => b,
        Err(e) => return fail(e.to_string()),
    };
    if let Err(why) = check(&bytes) {
        return fail(why);
    }
    if let Err(e) = std::fs::rename(&part, &m.path) {
        return fail(e.to_string());
    }
    Ok(m.path.clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Makes a file to download, from a local file rather than OMG, so
    /// that the test needs no network.
    ///
    /// # Arguments
    ///
    /// * `dir`  - The test's directory, under which it is kept.
    /// * `from` - The local file it is downloaded from.
    ///
    /// # Returns
    ///
    /// The file, as [`find`] would give it.
    fn missing(dir: &Path, from: &Path) -> Missing {
        Missing {
            what: "the test file".to_string(),
            url: format!("file://{}", from.display()),
            path: dir.join("kept/test.xsd"),
        }
    }

    #[test]
    fn downloads_what_passes_its_check_into_place() {
        let dir = tempfile::tempdir().unwrap();
        let from = dir.path().join("source.xsd");
        std::fs::write(&from, "<schema/>").unwrap();
        let m = missing(dir.path(), &from);
        let got = download(&m, |b| {
            if b.starts_with(b"<schema") {
                Ok(())
            } else {
                Err("not a schema".to_string())
            }
        })
        .unwrap();
        assert_eq!(got, m.path);
        assert_eq!(std::fs::read_to_string(&got).unwrap(), "<schema/>");
        assert!(!dir.path().join("kept/test.xsd.part").exists());
    }

    #[test]
    fn keeps_nothing_that_fails_its_check() {
        let dir = tempfile::tempdir().unwrap();
        let from = dir.path().join("source.html");
        std::fs::write(&from, "<html>Not found</html>").unwrap();
        let m = missing(dir.path(), &from);
        let e = download(&m, |_| Err("an error page".to_string())).unwrap_err();
        assert!(e.contains("an error page"), "{e}");
        assert!(e.contains("save it as"), "{e}");
        assert!(!m.path.exists());
        assert!(!dir.path().join("kept/test.xsd.part").exists());
    }

    #[test]
    fn finds_what_the_checkout_has_and_downloads_nothing_else() {
        let got = find("xtce-1.3.xsd", "https://example.org/x", "it");
        assert!(matches!(got, Ok(Ok(p)) if p.ends_with("xtce-1.3.xsd")));
        let m = find("not-one-of-omgs.xsd", "https://example.org/n", "it")
            .unwrap()
            .unwrap_err();
        assert!(m.path.ends_with("tcs-xtcee/not-one-of-omgs.xsd"));
        assert!(!m.path.exists());
    }

    #[test]
    fn says_what_to_do_when_it_is_not_downloaded() {
        let m = Missing {
            what: "the XTCE 1.3 schema".to_string(),
            url: "https://example.org/x.xsd".to_string(),
            path: PathBuf::from("/home/u/.local/share/tcs-xtcee/x.xsd"),
        };
        assert!(m.question().starts_with("The XTCE 1.3 schema is not"));
        assert!(m.question().ends_with("Download it?"));
        assert!(m.refused().contains("save it as /home/u/"));
    }
}
