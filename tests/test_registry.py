"""Tests for the extension registry."""

from duckdb_extensions.registry import (
    EXTENSION_ALIASES,
    EXTENSIONS,
    PLATFORMS,
    dist_name,
    extension_url,
    project_name,
    server_name,
    wheel_filename,
)

# Project suffix -> file name on the extension server: the 28 served extensions the CLI loads from a file.
SERVED_CORE_EXTENSIONS = {
    "avro": "avro",
    "aws": "aws",
    "azure": "azure",
    "delta": "delta",
    "demo_capi": "demo_capi",
    "ducklake": "ducklake",
    "encodings": "encodings",
    "excel": "excel",
    "fts": "fts",
    "httpfs": "httpfs",
    "iceberg": "iceberg",
    "inet": "inet",
    "lance": "lance",
    "legacy_parser": "legacy_parser",
    "motherduck": "motherduck",
    "mysql": "mysql_scanner",
    "odbc": "odbc_scanner",
    "postgres": "postgres_scanner",
    "quack": "quack",
    "spatial": "spatial",
    "sqlite": "sqlite_scanner",
    "sqlsmith": "sqlsmith",
    "tpcds": "tpcds",
    "tpch": "tpch",
    "ui": "ui",
    "unity_catalog": "unity_catalog",
    "vortex": "vortex",
    "vss": "vss",
}


def test_extensions_are_the_served_core_extensions_under_the_extras_names():
    assert EXTENSIONS == SERVED_CORE_EXTENSIONS
    assert list(EXTENSIONS) == sorted(EXTENSIONS)


def test_never_served_and_built_in_names_are_absent():
    for name in ("shell", "jemalloc", "sirius", "mysql_scanner", "postgres_scanner", "sqlite_scanner", "odbc_scanner"):
        assert name not in EXTENSIONS
    for name in ("autocomplete", "core_functions", "icu", "json", "parquet"):
        assert name not in EXTENSIONS


def test_aliases_point_at_project_names():
    assert EXTENSION_ALIASES["s3"] == "httpfs"
    assert EXTENSION_ALIASES["postgres_scanner"] == "postgres"
    assert EXTENSION_ALIASES["sqlite3"] == "sqlite"
    assert EXTENSION_ALIASES["odbc_scanner"] == "odbc"
    assert EXTENSION_ALIASES["full_text_search"] == "fts"
    for alias, canonical in EXTENSION_ALIASES.items():
        assert canonical in EXTENSIONS, alias
        assert alias not in EXTENSIONS, alias


def test_platforms_cover_pip_targets_with_server_name_and_tag():
    assert PLATFORMS == {
        "linux-amd64": ("linux_amd64", "manylinux_2_17_x86_64.manylinux2014_x86_64"),
        "linux-amd64-musl": ("linux_amd64_musl", "musllinux_1_2_x86_64"),
        "linux-arm64": ("linux_arm64", "manylinux_2_17_aarch64.manylinux2014_aarch64"),
        "linux-arm64-musl": ("linux_arm64_musl", "musllinux_1_2_aarch64"),
        "osx-amd64": ("osx_amd64", "macosx_12_0_x86_64"),
        "osx-arm64": ("osx_arm64", "macosx_12_0_arm64"),
        "windows-amd64": ("windows_amd64", "win_amd64"),
        "windows-arm64": ("windows_arm64", "win_arm64"),
    }


def test_extension_url_uses_server_name_platform_and_gz():
    assert extension_url("httpfs", "2.0.0", "linux-amd64-musl") == (
        "https://extensions.duckdb.org/v2.0.0/linux_amd64_musl/httpfs.duckdb_extension.gz"
    )
    assert extension_url("postgres", "2.0.0", "osx-arm64") == (
        "https://extensions.duckdb.org/v2.0.0/osx_arm64/postgres_scanner.duckdb_extension.gz"
    )


def test_names():
    assert project_name("mysql") == "duckdb-core-ext-mysql"
    assert dist_name("mysql") == "duckdb_core_ext_mysql"
    assert server_name("mysql") == "mysql_scanner"
    assert wheel_filename("httpfs", "2.0.0", "osx-arm64") == (
        "duckdb_core_ext_httpfs-2.0.0-py3-none-macosx_12_0_arm64.whl"
    )
