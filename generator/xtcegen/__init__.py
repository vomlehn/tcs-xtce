"""xtcegen: generates the XTCE XML binding from the XTCE schema."""

REPOSITORY = "https://github.com/vomlehn/tcs-xtce"


def header_lines() -> list:
    """The notice at the top of every generated file, without comment markers."""
    return [
        "@generated automatically by tcs-xtce (generator/xtcegen) from references/SpaceSystem.xsd.",
        "Any changes made to this file will be lost when it is regenerated;",
        "change generator/xtcegen instead and run `make generate`.",
        f"tcs-xtce: {REPOSITORY}",
    ]
