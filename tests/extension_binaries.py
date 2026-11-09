"""Synthetic DuckDB extension binaries: a body that names the entry point LOAD looks up, then the 512-byte
footer the engine reads (eight 32-byte fields stored last to first, then a 256-byte signature)."""


def footer(platform, version, abi_type="CPP", extension_version="v0.0.1", magic="4"):
    fields = [magic, platform, version, extension_version, abi_type, "", "", ""]
    metadata = b"".join(field.encode("ascii").ljust(32, b"\0") for field in reversed(fields))
    return metadata + b"\0" * 256


def entry_point(name, abi_type="CPP"):
    return f"{name}_duckdb_cpp_init" if abi_type == "CPP" else f"{name}_init_c_api"


def synthetic_binary(name, platform, version, abi_type="CPP", extension_version="v0.0.1", magic="4", entry=None):
    body = b"synthetic extension binary\0" + (entry or entry_point(name, abi_type)).encode("ascii") + b"\0"
    return body + footer(platform, version, abi_type, extension_version, magic)


# The inputs of the reference wheels in data/reference_wheels, whose bytes are the published layout:
# (project suffix, pip platform, DuckDB version, wheel version, server file name, footer version, ABI type).
REFERENCE_CASES = [
    ("httpfs", "linux-amd64", "2.0.0", "2.0.0", "httpfs", "v2.0.0", "CPP"),
    ("iceberg", "linux-arm64", "2.0.0", "2.0.0", "iceberg", "v2.0.0", "CPP"),
    ("iceberg", "linux-arm64", "2.0.0", "2.0.0.dev7", "iceberg", "v2.0.0", "CPP"),
    ("postgres", "osx-arm64", "2.0.0", "2.0.0", "postgres_scanner", "v2.0.0", "CPP"),
    ("tpch", "linux-arm64-musl", "2.0.0", "2.0.0.dev42", "tpch", "v2.0.0", "CPP"),
    ("odbc", "windows-amd64", "2.0.0", "2.0.0", "odbc_scanner", "v1.5.6", "C_STRUCT"),
    ("fts", "windows-arm64", "1.5.0", "1.5.0", "fts", "v1.5.0", "CPP"),
    ("demo_capi", "osx-amd64", "1.5.0", "1.5.0", "demo_capi", "v1.5.0", "C_STRUCT_UNSTABLE"),
]


def reference_binary(case):
    _, platform, _, _, server_file, footer_version, abi_type = case
    from duckdb_extensions.registry import server_platform

    return synthetic_binary(server_file, server_platform(platform), footer_version, abi_type)


def host_server_platform():
    """The extension server's name for the platform these tests run on, or None when it has none."""
    import os
    import platform
    import sys

    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    system = {"linux": "linux", "darwin": "osx", "win32": "windows"}.get(sys.platform)
    if arch is None or system is None:
        return None
    musl = system == "linux" and any(entry.startswith("ld-musl") for entry in os.listdir("/lib"))
    return f"{system}_{arch}" + ("_musl" if musl else "")
