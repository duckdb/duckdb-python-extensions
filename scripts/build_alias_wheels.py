#!/usr/bin/env python3
"""Build noarch alias wheels for DuckDB extension aliases.

Usage:
    uv run scripts/build_alias_wheels.py --version 1.4.4
"""

import argparse
import base64
import csv
import hashlib
import io
import os
import stat
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from duckdb_extensions.registry import EXTENSION_ALIASES

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


def build_alias_wheel(alias, canonical, version, out_dir):
    """Build one noarch alias wheel."""
    pkg = f"duckdb_core_ext_{alias}"
    wheel_tag = "py3-none-any"
    wheel_name = f"{pkg}-{version}-{wheel_tag}.whl"
    dist_info = f"{pkg}-{version}.dist-info"

    records = []
    wheel_path = os.path.join(out_dir, wheel_name)

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Empty __init__.py
        init_data = b""
        arcname = f"{pkg}/__init__.py"
        _add_file(zf, arcname, init_data)
        records.append((arcname, _record_hash(init_data), str(len(init_data))))

        # METADATA
        metadata_content = (
            "Metadata-Version: 2.1\n"
            f"Name: duckdb-core-ext-{alias}\n"
            f"Version: {version}\n"
            f"Summary: Alias for duckdb-core-ext-{canonical}\n"
            "Requires-Python: >=3.8\n"
            f"Requires-Dist: duckdb-core-ext-{canonical}=={version}\n"
        )
        metadata_bytes = metadata_content.encode("utf-8")
        arcname = f"{dist_info}/METADATA"
        _add_file(zf, arcname, metadata_bytes)
        records.append(
            (arcname, _record_hash(metadata_bytes), str(len(metadata_bytes)))
        )

        # WHEEL
        wheel_content = (
            "Wheel-Version: 1.0\n"
            "Generator: duckdb-extension-build\n"
            "Root-Is-Purelib: true\n"
            f"Tag: {wheel_tag}\n"
        )
        wheel_bytes = wheel_content.encode("utf-8")
        arcname = f"{dist_info}/WHEEL"
        _add_file(zf, arcname, wheel_bytes)
        records.append((arcname, _record_hash(wheel_bytes), str(len(wheel_bytes))))

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
        description="Build noarch alias wheels for DuckDB extension aliases."
    )
    parser.add_argument("--version", required=True, help="DuckDB version (e.g. 1.4.4)")
    parser.add_argument("--out-dir", default="dist", help="Output directory")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    wheels = []
    for canonical, aliases in EXTENSION_ALIASES.items():
        for alias in aliases:
            print(f"\nBuilding alias {alias} -> {canonical}...")
            w = build_alias_wheel(alias, canonical, args.version, args.out_dir)
            wheels.append(w)

    print(f"\nBuilt {len(wheels)} alias wheel(s) in {args.out_dir}/")


if __name__ == "__main__":
    main()
