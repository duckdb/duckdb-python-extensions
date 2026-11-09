#!/usr/bin/env python3
"""Build wheels for duckdb-core-ext-<name> packages from the binaries on extensions.duckdb.org.

Usage:
    uv run --no-project scripts/build_extension_wheels.py --version 2.0.0 --extension httpfs
    uv run --no-project scripts/build_extension_wheels.py --version 2.0.0 --extension httpfs --platform linux-amd64 --platform osx-arm64

With explicit --platform flags, a platform the server does not have is an error.
Without them every platform is tried and absent ones are skipped.
"""

import argparse
import gzip
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import duckdb_extension_wheel as tool

from duckdb_extension_wheel.net import get_bytes
from duckdb_extensions.registry import (
    EXTENSIONS,
    PLATFORMS,
    REQUIRES,
    extension_url,
    project_name,
    server_name,
)


def download_extension(name, version, platform, out_dir):
    """Download and decompress one extension. Returns the path, or None when the server has no such file."""
    compressed = get_bytes(extension_url(name, version, platform))
    if compressed is None:
        return None
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{server_name(name)}.duckdb_extension")
    with open(out_path, "wb") as f:
        f.write(gzip.decompress(compressed))
    return out_path


def inspect_binary(name, platform, version, ext_data):
    """The tool's view of a downloaded binary, checked against what the pipeline asked the server for."""
    try:
        ext = tool.extension_from_bytes(server_name(name), ext_data, duckdb_version=version)
    except tool.WheelError as e:
        raise tool.WheelError(f"{name} {platform}: {e}") from None
    if ext.platform != platform:
        raise tool.WheelError(f"{name} {platform}: binary is for {ext.platform}, not {platform}")
    return ext


def write_wheel(name, platform, version, ext_data, out_dir, wheel_version=None, ext=None):
    """Write one wheel from the extension bytes. The wheel's own version may differ from the DuckDB version
    (a staging build), but the binary always sits under v<DuckDB version>, where the engine looks for it."""
    wheel_version = wheel_version or version
    if ext is None:
        ext = inspect_binary(name, platform, version, ext_data)

    metadata = tool.WheelMetadata(
        name=project_name(name),
        version=wheel_version,
        requires=tuple(f"{project_name(dep)}=={wheel_version}" for dep in REQUIRES.get(name, [])),
        summary=f"The DuckDB {server_name(name)} extension for DuckDB {wheel_version}, installed into a shared extension directory",
        description=f"""# {project_name(name)}

The DuckDB `{server_name(name)}` extension binary for DuckDB {wheel_version}, as published on extensions.duckdb.org, placed in the `.duckdb_extensions` directory of site-packages. The `duckdb-cli` package and the `duckdb` Python module can search and install from that directory, so the extension loads without a download. An extension is built for one DuckDB version: install the release that matches your DuckDB.
""",
        description_content_type="text/markdown",
        project_urls=(
            "Documentation, https://duckdb.org/docs/extensions/overview",
            "Source, https://github.com/duckdb/duckdb-python-extensions",
        ),
        requires_python=">=3.8",
    )

    wheel_path = tool.write_wheel(ext, metadata, out_dir)
    print(f"  Built: {wheel_path}")
    return wheel_path


def build_extension_wheel(name, platform, version, out_dir, stage_version=None, stage_dir=None):
    """Build one wheel, and with stage_version a second one from the same bytes under that version into
    stage_dir. Returns the wheel path, None when the server has no binary, or raises WheelError."""
    with tempfile.TemporaryDirectory() as tmp:
        ext_path = download_extension(name, version, platform, tmp)
        if ext_path is None:
            return None
        with open(ext_path, "rb") as f:
            ext_data = f.read()
    ext = inspect_binary(name, platform, version, ext_data)
    wheel_path = write_wheel(name, platform, version, ext_data, out_dir, ext=ext)
    if stage_version:
        write_wheel(name, platform, version, ext_data, stage_dir or out_dir, wheel_version=stage_version, ext=ext)
    return wheel_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build wheels for DuckDB extensions.")
    parser.add_argument("--version", required=True, help="DuckDB version (e.g. 2.0.0)")
    parser.add_argument("--out-dir", default="dist", help="Output directory")
    parser.add_argument("--extension", action="append", choices=list(EXTENSIONS), help="Extension to build, by project name (repeatable; default: all)")
    parser.add_argument("--platform", action="append", choices=list(PLATFORMS), help="Platform to build (repeatable; default: all that the server has)")
    parser.add_argument("--stage-version", default=None, help="Also write each wheel under this version (e.g. 2.0.0.dev42), for a test index")
    parser.add_argument("--stage-dir", default=None, help="Directory for the staging wheels (default: --out-dir)")
    args = parser.parse_args(argv)

    extensions = args.extension or list(EXTENSIONS)
    strict = args.platform is not None
    platforms = args.platform or list(PLATFORMS)

    os.makedirs(args.out_dir, exist_ok=True)

    wheels = []
    missing = []
    refused = []
    for ext in extensions:
        for plat in platforms:
            print(f"\nBuilding {ext} {args.version} for {plat}...")
            try:
                w = build_extension_wheel(ext, plat, args.version, args.out_dir, stage_version=args.stage_version, stage_dir=args.stage_dir)
                if w:
                    wheels.append(w)
                else:
                    missing.append((ext, plat))
                    print(f"  {ext} {args.version} is not on the server for {plat}")
            except tool.WheelError as e:
                refused.append((ext, plat))
                print(f"  {e}")

    print(f"\nBuilt {len(wheels)} wheel(s) in {args.out_dir}/")
    failures = []
    if strict and missing:
        failures.append("Requested platforms not on the server: " + ", ".join(f"{e} {p}" for e, p in missing))
    if refused:
        failures.append("Refused: " + ", ".join(f"{e} {p}" for e, p in refused))
    if failures:
        sys.exit("\n".join(failures))


if __name__ == "__main__":
    main()
