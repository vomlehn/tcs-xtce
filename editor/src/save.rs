//! Writing a file without being able to damage the one already there.
//!
//! A write that is interrupted -- by power, a full disk, the process being
//! killed -- must leave either the old file or the new one, never part of
//! either.  So the new contents go to a temporary file in the same
//! directory, are flushed to the disk, and the temporary file is renamed
//! over the old one, which is atomic within a filesystem; then the
//! directory is flushed so the rename itself survives a power failure.
//! This is tcs-gui's recipe for its configurations, for the same reasons.

use std::fs::{self, File};
use std::io::Write;
use std::path::Path;

/// Writes a file whole, or leaves it as it was.
///
/// # Arguments
///
/// * `path` - The file.
/// * `text` - Its new contents.
///
/// # Errors
///
/// Says what failed; the file is then as it was before.
pub fn save(path: &Path, text: &str) -> Result<(), String> {
    let dir = match path.parent() {
        Some(d) if !d.as_os_str().is_empty() => d.to_path_buf(),
        _ => Path::new(".").to_path_buf(),
    };
    let name = path
        .file_name()
        .ok_or_else(|| format!("{} names no file", path.display()))?
        .to_string_lossy()
        .to_string();
    let tmp = dir.join(format!(".{name}.{}.tmp", std::process::id()));
    let result = (|| -> std::io::Result<()> {
        let mut f = File::create(&tmp)?;
        f.write_all(text.as_bytes())?;
        f.sync_all()?;
        // Keep the old file's permissions, so that a save does not make a
        // file readable or writable by people it was not before.
        if let Ok(meta) = fs::metadata(path) {
            fs::set_permissions(&tmp, meta.permissions())?;
        }
        fs::rename(&tmp, path)?;
        File::open(&dir)?.sync_all()
    })();
    result.map_err(|e| {
        let _ = fs::remove_file(&tmp);
        format!(
            "{} could not be written ({e}); it is as it was",
            path.display()
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replaces_a_file_whole() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("a.xtce");
        fs::write(&p, "old").unwrap();
        save(&p, "new").unwrap();
        assert_eq!(fs::read_to_string(&p).unwrap(), "new");
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 1);
    }

    #[test]
    fn leaves_the_file_if_it_cannot_write() {
        let p = Path::new("/nonexistent-directory/a.xtce");
        assert!(save(p, "new").is_err());
    }
}
