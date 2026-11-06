#!/usr/bin/env python3
"""Build wheels for duckdb-core-ext-<name> packages from the binaries on extensions.duckdb.org.

Usage:
    uv run --no-project scripts/build_extension_wheels.py --version 2.0.0 --extension httpfs
    uv run --no-project scripts/build_extension_wheels.py --version 2.0.0 --extension httpfs --platform linux-amd64 --platform osx-arm64

With explicit --platform flags, a platform the server does not have is an error.
Without them every platform is tried and absent ones are skipped.
"""

import argparse
import base64
import csv
import gzip
import hashlib
import io
import os
import stat
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from duckdb_extensions.net import get_bytes
from duckdb_extensions.registry import (
    EXTENSIONS,
    PLATFORMS,
    REQUIRES,
    dist_name,
    extension_url,
    platform_tag,
    project_name,
    server_name,
    server_platform,
    wheel_filename,
)

REPRODUCIBLE_DATE = (1980, 1, 1, 0, 0, 0)


def _record_hash(data):
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _add_file(zf, arcname, data):
    info = zipfile.ZipInfo(arcname, date_time=REPRODUCIBLE_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH) << 16
    zf.writestr(info, data)


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


def generate_init_py(name, version, platform):
    return (
        f'"""DuckDB {server_name(name)} extension, DuckDB {version}, {server_platform(platform)}."""\n'
        "\n"
        "import os\n"
        "\n"
        '_EXTENSIONS_DIR = ".duckdb_extensions"\n'
        "\n"
        "\n"
        "def get_extensions_dir():\n"
        '    """The shared extension directory in site-packages that DuckDB can search and install into."""\n'
        "    return os.path.join(os.path.dirname(os.path.dirname(__file__)), _EXTENSIONS_DIR)\n"
        "\n"
        "\n"
        "def get_extension_load_path():\n"
        '    """The extension binary, for LOAD by path."""\n'
        f'    return os.path.join(get_extensions_dir(), "v{version}", "{server_platform(platform)}", "{server_name(name)}.duckdb_extension")\n'
    ).encode("utf-8")


def generate_metadata(name, version):
    requires = "".join(f"Requires-Dist: {project_name(dep)}=={version}\n" for dep in REQUIRES.get(name, []))
    return (
        "Metadata-Version: 2.1\n"
        f"Name: {project_name(name)}\n"
        f"Version: {version}\n"
        f"Summary: The DuckDB {server_name(name)} extension for DuckDB {version}, installed into a shared extension directory\n"
        "Requires-Python: >=3.8\n"
        f"{requires}"
        "Project-URL: Documentation, https://duckdb.org/docs/extensions/overview\n"
        "Project-URL: Source, https://github.com/duckdb/duckdb-python-extensions\n"
        "Description-Content-Type: text/markdown\n"
        "\n"
        f"# {project_name(name)}\n"
        "\n"
        f"The DuckDB `{server_name(name)}` extension binary for DuckDB {version}, as published on extensions.duckdb.org, "
        "placed in the `.duckdb_extensions` directory of site-packages. The `duckdb-cli` package and the "
        "`duckdb` Python module can search and install from that directory, so the extension loads without "
        "a download. An extension is built for one DuckDB version: install the release that matches your DuckDB.\n"
    )


def write_wheel(name, platform, version, ext_data, out_dir, wheel_version=None):
    """Write one wheel from the extension bytes. The wheel's own version may differ from the DuckDB version
    (a staging build), but the binary always sits under v<DuckDB version>, where the engine looks for it."""
    wheel_version = wheel_version or version
    pkg = dist_name(name)
    wheel_tag = f"py3-none-{platform_tag(platform)}"
    dist_info = f"{pkg}-{wheel_version}.dist-info"
    data_dir = f"{pkg}-{wheel_version}.data"
    records = []
    wheel_path = os.path.join(out_dir, wheel_filename(name, wheel_version, platform))
    # Written under a temporary name so that a failed build never leaves a file that looks like a wheel.
    partial_path = wheel_path + ".partial"

    def add(zf, arcname, data):
        _add_file(zf, arcname, data)
        records.append((arcname, _record_hash(data), str(len(data))))

    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(partial_path, "w", zipfile.ZIP_DEFLATED) as zf:
        add(zf, f"{pkg}/__init__.py", generate_init_py(name, version, platform))
        add(zf, f"{data_dir}/platlib/.duckdb_extensions/v{version}/{server_platform(platform)}/{server_name(name)}.duckdb_extension", ext_data)
        add(zf, f"{dist_info}/METADATA", generate_metadata(name, wheel_version).encode("utf-8"))
        add(zf, f"{dist_info}/WHEEL", f"Wheel-Version: 1.0\nGenerator: duckdb-extension-build\nRoot-Is-Purelib: false\nTag: {wheel_tag}\n".encode("utf-8"))
        add(zf, f"{dist_info}/top_level.txt", f"{pkg}\n".encode("utf-8"))

        record_buf = io.StringIO()
        writer = csv.writer(record_buf, lineterminator="\n")
        writer.writerows(records)
        writer.writerow([f"{dist_info}/RECORD", "", ""])
        _add_file(zf, f"{dist_info}/RECORD", record_buf.getvalue().encode("utf-8"))
    os.replace(partial_path, wheel_path)
    print(f"  Built: {wheel_path}")
    return wheel_path


def build_extension_wheel(name, platform, version, out_dir, stage_version=None, stage_dir=None):
    """Build one wheel, and with stage_version a second one from the same bytes under that version into
    stage_dir. Returns the wheel path, or None when the server has no binary for the platform."""
    with tempfile.TemporaryDirectory() as tmp:
        ext_path = download_extension(name, version, platform, tmp)
        if ext_path is None:
            return None
        with open(ext_path, "rb") as f:
            ext_data = f.read()
    wheel_path = write_wheel(name, platform, version, ext_data, out_dir)
    if stage_version:
        write_wheel(name, platform, version, ext_data, stage_dir or out_dir, wheel_version=stage_version)
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
    for ext in extensions:
        for plat in platforms:
            print(f"\nBuilding {ext} {args.version} for {plat}...")
            w = build_extension_wheel(ext, plat, args.version, args.out_dir, stage_version=args.stage_version, stage_dir=args.stage_dir)
            if w:
                wheels.append(w)
            else:
                missing.append((ext, plat))
                print(f"  {ext} {args.version} is not on the server for {plat}")

    print(f"\nBuilt {len(wheels)} wheel(s) in {args.out_dir}/")
    if strict and missing:
        sys.exit("Requested platforms not on the server: " + ", ".join(f"{e} {p}" for e, p in missing))


if __name__ == "__main__":
    main()
