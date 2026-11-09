"""Tests for duckdb_extensions.discover: served extensions the registry does not know."""

from duckdb_extensions.discover import configured_extensions, extension_url_for, unregistered


def test_configured_extensions_reads_the_cmake_file_names_at_the_tag():
    asked = []

    def fetch(url, accept, token=None):
        asked.append((url, accept, token))
        return [
            {"type": "file", "name": "glue.cmake"},
            {"type": "file", "name": "avro.cmake"},
            {"type": "file", "name": "README.md"},
            {"type": "dir", "name": "old"},
        ]

    assert configured_extensions("2.0.0", fetch=fetch, token="t") == ["avro", "glue"]
    assert asked == [("https://api.github.com/repos/duckdb/duckdb/contents/.github/config/extensions?ref=v2.0.0", "application/vnd.github+json", "t")]


def test_unregistered_keeps_only_unknown_names_the_server_serves():
    served = {
        "https://extensions.duckdb.org/v2.0.0/linux_amd64/glue.duckdb_extension.gz",
        "https://extensions.duckdb.org/v2.0.0/osx_arm64/glue.duckdb_extension.gz",
    }
    asked = []

    def head(url):
        asked.append(url)
        return 200 if url in served else 404

    result = unregistered("2.0.0", ["avro", "glue", "mysql_scanner", "test-utils"], head=head)

    assert result == {"glue": ["linux-amd64", "osx-arm64"]}
    assert not any("/avro." in u or "/mysql_scanner." in u for u in asked), "registry names are not asked for"
    assert sum(1 for u in asked if "/test-utils." in u) == 8


def test_extension_url_for_an_unknown_name():
    assert extension_url_for("glue", "2.0.0", "linux-arm64-musl") == "https://extensions.duckdb.org/v2.0.0/linux_arm64_musl/glue.duckdb_extension.gz"
