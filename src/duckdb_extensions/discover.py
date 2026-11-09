"""Find core extensions the server serves that the registry does not know, from DuckDB's own build configuration."""

from duckdb_extensions.net import get_json, head as _head
from duckdb_extensions.registry import EXTENSIONS, EXTENSIONS_URL, PLATFORMS

# One cmake file per out-of-tree extension that DuckDB's CI builds and uploads to the core repository for a tag.
_CONFIG_DIR = "https://api.github.com/repos/duckdb/duckdb/contents/.github/config/extensions"


def configured_extensions(version, fetch=get_json, token=None):
    """The out-of-tree extension names in duckdb/duckdb's build configuration at the release tag."""
    entries = fetch(f"{_CONFIG_DIR}?ref=v{version}", "application/vnd.github+json", token=token)
    return sorted(e["name"][: -len(".cmake")] for e in entries if e["type"] == "file" and e["name"].endswith(".cmake"))


def unregistered(version, names, head=_head):
    """{server name: [pip platforms]} for the names the registry lacks and the server serves for the version."""
    known = set(EXTENSIONS.values())
    found = {}
    for name in names:
        if name in known:
            continue
        platforms = [p for p in PLATFORMS if head(extension_url_for(name, version, p)) == 200]
        if platforms:
            found[name] = platforms
    return found


def extension_url_for(server_name, version, platform):
    """The download URL for a server name that may not be in the registry."""
    return f"{EXTENSIONS_URL}/v{version}/{PLATFORMS[platform][0]}/{server_name}.duckdb_extension.gz"
