"""Which DuckDB core extensions exist, what their PyPI projects are called, and where the server keeps them."""

from duckdb_extension_wheel.binary import PLATFORMS
from duckdb_extension_wheel import dist_name as _tool_dist_name, wheel_filename as _tool_wheel_filename

# PyPI project suffix -> file name on extensions.duckdb.org. The scanners carry the names the duckdb-cli
# extras require rather than the server's file names. Extensions compiled into the CLI (autocomplete,
# core_functions, icu, json, parquet) are left out: the CLI never loads them from a file, so a wheel for
# them cannot be verified.
EXTENSIONS = {
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

EXTENSION_ALIASES = {
    "full_text_search": "fts",
    "http": "httpfs",
    "https": "httpfs",
    "s3": "httpfs",
    "md": "motherduck",
    "mysql_scanner": "mysql",
    "odbc_scanner": "odbc",
    "postgres_scanner": "postgres",
    "sqlite_scanner": "sqlite",
    "sqlite3": "sqlite",
    "uc_catalog": "unity_catalog",
}

# Extensions whose LOAD needs another extension present in the same directory.
REQUIRES = {
    "iceberg": ["avro"],
}

EXTENSIONS_URL = "https://extensions.duckdb.org"


def server_name(name):
    return EXTENSIONS[name]


def server_platform(platform):
    return PLATFORMS[platform][0]


def platform_tag(platform):
    return PLATFORMS[platform][1]


def extension_url(name, version, platform):
    return f"{EXTENSIONS_URL}/v{version}/{server_platform(platform)}/{server_name(name)}.duckdb_extension.gz"


def project_name(name):
    return f"duckdb-core-ext-{name}"


def dist_name(name):
    return _tool_dist_name(project_name(name))


def wheel_filename(name, version, platform):
    return _tool_wheel_filename(project_name(name), version, platform)
