"""Single source of truth for DuckDB extension metadata."""

EXTENSIONS = [
    "autocomplete",
    "avro",
    "aws",
    "azure",
    "core_functions",
    "delta",
    "demo_capi",
    "ducklake",
    "encodings",
    "excel",
    "fts",
    "httpfs",
    "iceberg",
    "icu",
    "inet",
    "jemalloc",
    "json",
    "motherduck",
    "mysql",
    "parquet",
    "postgres",
    "quack",
    "shell",
    "spatial",
    "sqlite",
    "sqlsmith",
    "tpcds",
    "tpch",
    "ui",
    "unity_catalog",
    "vortex",
    "vss",
]

EXTENSION_ALIASES = {
    "httpfs": ["http", "https", "s3"],
    "motherduck": ["md"],
    "mysql": ["mysql_scanner"],
    "postgres": ["postgres_scanner"],
    "sqlite": ["sqlite_scanner", "sqlite3"],
    "unity_catalog": ["uc_catalog"],
}

EXTENSIONS_URL = "https://extensions.duckdb.org"

DUCKDB_PLATFORMS = {
    "linux-amd64": "linux_amd64",
    "linux-arm64": "linux_arm64",
    "osx-amd64": "osx_amd64",
    "osx-arm64": "osx_arm64",
    "windows-amd64": "windows_amd64",
    "windows-arm64": "windows_arm64",
}

PLATFORM_TAGS = {
    "linux-amd64": "manylinux_2_17_x86_64.manylinux2014_x86_64",
    "linux-arm64": "manylinux_2_17_aarch64.manylinux2014_aarch64",
    "osx-amd64": "macosx_12_0_x86_64",
    "osx-arm64": "macosx_12_0_arm64",
    "windows-amd64": "win_amd64",
    "windows-arm64": "win_arm64",
}


def extension_url(name, version, platform):
    """Build the download URL for a DuckDB extension.

    >>> extension_url("httpfs", "v1.4.4", "linux-amd64")
    'https://extensions.duckdb.org/v1.4.4/linux_amd64/httpfs.duckdb_extension.gz'
    """
    duckdb_platform = DUCKDB_PLATFORMS[platform]
    return f"{EXTENSIONS_URL}/{version}/{duckdb_platform}/{name}.duckdb_extension.gz"
