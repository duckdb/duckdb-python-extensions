"""Tests for the extension registry."""

from duckdb_extensions.registry import (
    DUCKDB_PLATFORMS,
    EXTENSION_ALIASES,
    EXTENSIONS,
    PLATFORM_TAGS,
    extension_url,
)


def test_extension_count():
    assert len(EXTENSIONS) == 32


def test_extensions_sorted():
    assert EXTENSIONS == sorted(EXTENSIONS)


def test_no_duplicates():
    assert len(EXTENSIONS) == len(set(EXTENSIONS))


def test_aliases_are_for_known_extensions():
    for canonical in EXTENSION_ALIASES:
        assert canonical in EXTENSIONS, f"alias target {canonical} not in EXTENSIONS"


def test_aliases_dont_collide_with_extensions():
    all_aliases = {a for aliases in EXTENSION_ALIASES.values() for a in aliases}
    overlap = all_aliases & set(EXTENSIONS)
    assert not overlap, f"aliases overlap with extensions: {overlap}"


def test_platform_tags_match_duckdb_platforms():
    assert set(PLATFORM_TAGS.keys()) == set(DUCKDB_PLATFORMS.keys())


def test_extension_url_format():
    url = extension_url("httpfs", "v1.4.4", "linux-amd64")
    assert url == "http://extensions.duckdb.org/v1.4.4/linux_amd64/httpfs.duckdb_extension.gz"


def test_extension_url_osx():
    url = extension_url("parquet", "v1.4.4", "osx-arm64")
    assert url == "http://extensions.duckdb.org/v1.4.4/osx_arm64/parquet.duckdb_extension.gz"
