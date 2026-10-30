"""tcs_xtce.binding: generates the XTCE XML binding from the XTCE schema."""

REPOSITORY = "https://github.com/vomlehn/tcs-xtce"
# The Rust crate on crates.io.
CRATE = "https://crates.io/crates/tcs-xtce"


def header_lines() -> list:
    """Gives the notice at the top of every generated file.

    Returns:
        The notice's lines, without comment markers: what made the file,
        not to edit it, and where tcs-xtce and its crate are.
    """
    return ["@generated automatically by tcs-xtce (python/tcs_xtce/binding) "
            "from references/SpaceSystem.xsd.",
            "Any changes made to this file will be lost when it is "
            "regenerated;",
            "change python/tcs_xtce/binding instead and run `make generate`.",
            f"tcs-xtce: {REPOSITORY}", f"Rust crate: {CRATE}"]
