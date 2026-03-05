#!/usr/bin/env python3
"""Build platform-specific wheels for duckdb-ext-<name> extension packages.

Usage:
    uv run scripts/build_extension_wheels.py --version 1.4.4
    uv run scripts/build_extension_wheels.py --version 1.4.4 --extension httpfs --platform osx-arm64
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
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from duckdb_extensions.registry import (
    DUCKDB_PLATFORMS,
    EXTENSIONS,
    PLATFORM_TAGS,
    extension_url,
)

REPRODUCIBLE_DATE = (1980, 1, 1, 0, 0, 0)


def _record_hash(data):
    """Return sha256=<urlsafe-base64-nopad> digest for RECORD."""
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _add_file(zf, arcname, data):
    """Add a file to the zip with reproducible timestamps."""
    info = zipfile.ZipInfo(arcname, date_time=REPRODUCIBLE_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (
        stat.S_IFREG | stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH
    ) << 16
    zf.writestr(info, data)


def download_extension(name, version, platform, out_dir):
    """Download and decompress a DuckDB extension. Returns path or None on 404."""
    url = extension_url(name, f"v{version}", platform)
    req = Request(url, headers={"User-Agent": "duckdb-ext-build/1.0"})
    try:
        resp = urlopen(req)
    except HTTPError as e:
        if e.code == 404:
            print(f"  WARNING: {name} not available for {platform} (404), skipping")
            return None
        raise
    compressed = resp.read()
    data = gzip.decompress(compressed)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{name}.duckdb_extension")
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def generate_init_py(name):
    """Generate __init__.py that exposes extension_path()."""
    return (
        f'"""DuckDB {name} extension package."""\n'
        "\n"
        "import os\n"
        "\n"
        "\n"
        "def get_extension_load_path():\n"
        '    """Return the absolute path to the extension binary for LOAD."""\n'
        f'    return os.path.join(os.path.dirname(__file__), "{name}.duckdb_extension")\n'
    ).encode("utf-8")


def generate_metadata(name, version):
    """Generate PEP 566 METADATA for an extension wheel."""
    return (
        "Metadata-Version: 2.1\n"
        f"Name: duckdb-ext-{name}\n"
        f"Version: {version}\n"
        f"Summary: DuckDB {name} extension\n"
        "Requires-Python: >=3.8\n"
        f"Requires-Dist: duckdb-cli=={version}\n"
    )


def build_extension_wheel(name, platform, version, out_dir):
    """Build one extension wheel. Returns wheel path or None."""
    with tempfile.TemporaryDirectory() as tmp:
        ext_path = download_extension(name, version, platform, tmp)
        if ext_path is None:
            return None

        platform_tag = PLATFORM_TAGS[platform]
        pkg = f"duckdb_ext_{name}"
        wheel_tag = f"py3-none-{platform_tag}"
        wheel_name = f"{pkg}-{version}-{wheel_tag}.whl"
        dist_info = f"{pkg}-{version}.dist-info"

        records = []
        wheel_path = os.path.join(out_dir, wheel_name)

        with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # __init__.py
            init_data = generate_init_py(name)
            arcname = f"{pkg}/__init__.py"
            _add_file(zf, arcname, init_data)
            records.append((arcname, _record_hash(init_data), str(len(init_data))))

            # Extension binary
            with open(ext_path, "rb") as f:
                ext_data = f.read()
            arcname = f"{pkg}/{name}.duckdb_extension"
            _add_file(zf, arcname, ext_data)
            records.append((arcname, _record_hash(ext_data), str(len(ext_data))))

            # METADATA
            metadata_bytes = generate_metadata(name, version).encode("utf-8")
            arcname = f"{dist_info}/METADATA"
            _add_file(zf, arcname, metadata_bytes)
            records.append(
                (arcname, _record_hash(metadata_bytes), str(len(metadata_bytes)))
            )

            # WHEEL
            wheel_content = (
                "Wheel-Version: 1.0\n"
                "Generator: duckdb-ext-build\n"
                "Root-Is-Purelib: false\n"
                f"Tag: {wheel_tag}\n"
            )
            wheel_bytes = wheel_content.encode("utf-8")
            arcname = f"{dist_info}/WHEEL"
            _add_file(zf, arcname, wheel_bytes)
            records.append(
                (arcname, _record_hash(wheel_bytes), str(len(wheel_bytes)))
            )

            # top_level.txt
            top_level_bytes = f"{pkg}\n".encode("utf-8")
            arcname = f"{dist_info}/top_level.txt"
            _add_file(zf, arcname, top_level_bytes)
            records.append(
                (arcname, _record_hash(top_level_bytes), str(len(top_level_bytes)))
            )

            # RECORD
            record_buf = io.StringIO()
            writer = csv.writer(record_buf, lineterminator="\n")
            for row in records:
                writer.writerow(row)
            writer.writerow([f"{dist_info}/RECORD", "", ""])
            record_bytes = record_buf.getvalue().encode("utf-8")
            _add_file(zf, f"{dist_info}/RECORD", record_bytes)

    print(f"  Built: {wheel_path}")
    return wheel_path


def main():
    parser = argparse.ArgumentParser(
        description="Build platform-specific wheels for DuckDB extensions."
    )
    parser.add_argument("--version", required=True, help="DuckDB version (e.g. 1.4.4)")
    parser.add_argument("--out-dir", default="dist", help="Output directory")
    parser.add_argument(
        "--extension",
        default=None,
        help="Build a single extension (default: all)",
    )
    parser.add_argument(
        "--platform",
        choices=list(DUCKDB_PLATFORMS.keys()),
        default=None,
        help="Build for a single platform (default: all)",
    )
    args = parser.parse_args()

    extensions = [args.extension] if args.extension else EXTENSIONS
    platforms = [args.platform] if args.platform else list(DUCKDB_PLATFORMS.keys())

    os.makedirs(args.out_dir, exist_ok=True)

    wheels = []
    for ext in extensions:
        for plat in platforms:
            print(f"\nBuilding {ext} for {plat}...")
            w = build_extension_wheel(ext, plat, args.version, args.out_dir)
            if w:
                wheels.append(w)

    print(f"\nBuilt {len(wheels)} wheel(s) in {args.out_dir}/")


if __name__ == "__main__":
    main()
