"""Which DuckDB core extensions exist, what their PyPI projects are called, and where the server keeps them."""

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

# pip platform -> (platform name on the extension server, wheel platform tag)
PLATFORMS = {
    "linux-amd64": ("linux_amd64", "manylinux_2_17_x86_64.manylinux2014_x86_64"),
    "linux-amd64-musl": ("linux_amd64_musl", "musllinux_1_2_x86_64"),
    "linux-arm64": ("linux_arm64", "manylinux_2_17_aarch64.manylinux2014_aarch64"),
    "linux-arm64-musl": ("linux_arm64_musl", "musllinux_1_2_aarch64"),
    "osx-amd64": ("osx_amd64", "macosx_12_0_x86_64"),
    "osx-arm64": ("osx_arm64", "macosx_12_0_arm64"),
    "windows-amd64": ("windows_amd64", "win_amd64"),
    "windows-arm64": ("windows_arm64", "win_arm64"),
}


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
    return f"duckdb_core_ext_{name}"


def wheel_filename(name, version, platform):
    return f"{dist_name(name)}-{version}-py3-none-{platform_tag(platform)}.whl"
