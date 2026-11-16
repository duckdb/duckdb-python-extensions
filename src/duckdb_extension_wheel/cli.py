"""Command-line interface for duckdb-extension-wheel."""

import argparse
import os
import sys

from . import check as check_module
from .binary import WheelError
from .wheel import WheelMetadata, build_wheels


def _read_description(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (IOError, OSError, UnicodeDecodeError) as e:
        raise WheelError(f"cannot read description file {path}: {e}")

    suffix = os.path.splitext(path)[1].lower()
    if suffix in (".md", ".markdown"):
        content_type = "text/markdown"
    elif suffix == ".rst":
        content_type = "text/x-rst"
    else:
        content_type = "text/plain"

    return text, content_type


def _build_command(args):
    description = None
    description_content_type = None

    if args.description_file:
        description, description_content_type = _read_description(args.description_file)

    metadata = WheelMetadata(
        name=args.name,
        version=args.version,
        requires=args.requires or (),
        summary=args.summary,
        description=description,
        description_content_type=description_content_type,
        license_expression=args.license,
        project_urls=args.project_url or (),
        requires_python=args.requires_python,
        keywords=args.keyword or (),
    )

    wheels = build_wheels(args.binaries, metadata, args.out_dir, duckdb_version=args.duckdb_version, info_path=args.info, core=args.core_extension)

    for wheel in wheels:
        print(wheel)

    return 0


def _check_command(args):
    pip_args = []
    if args.no_deps:
        pip_args.append("--no-deps")
    if args.index_url:
        pip_args.extend(["--index-url", args.index_url])
    for link in args.find_links or []:
        pip_args.extend(["--find-links", link])

    ok, detail = check_module.check_wheel(args.wheel, cli=args.cli, pip_args=tuple(pip_args), unsigned=args.unsigned)
    if ok:
        wheel_name = os.path.basename(args.wheel)
        print(f"ok: {wheel_name} loaded from its .duckdb_extensions directory")
        return 0
    sys.stderr.write(f"fail: {detail}\n")
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="duckdb-extension-wheel", description="Build wheels from DuckDB extension binaries.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build wheels from extension binaries")
    build_parser.add_argument("--name", required=True, help="Project name")
    build_parser.add_argument("--version", required=True, help="Wheel version")
    build_parser.add_argument("--duckdb-version", help="DuckDB version (required for C_STRUCT binaries)")
    build_parser.add_argument("--info", help="The <name>.duckdb_extension.info file DuckDB wrote when it installed the extension (default: the one beside the binary)")
    build_parser.add_argument("--core-extension", action="store_true", help="The binary is signed with a DuckDB core key and needs no install information (one found beside it is still shipped)")
    build_parser.add_argument("--requires", action="append", help="Dependency (repeatable)")
    build_parser.add_argument("--summary", help="One-line summary")
    build_parser.add_argument("--description-file", help="Path to description file")
    build_parser.add_argument("--license", help="License expression (SPDX)")
    build_parser.add_argument("--project-url", action="append", help="Project URL as 'Label, URL' (repeatable)")
    build_parser.add_argument("--requires-python", default=">=3.8", help="Python version requirement")
    build_parser.add_argument("--keyword", action="append", help="Keyword (repeatable)")
    build_parser.add_argument("--out-dir", default="dist", help="Output directory")
    build_parser.add_argument("binaries", nargs="+", help="Extension binary paths")

    check_parser = subparsers.add_parser("check", help="Check that a wheel loads in the DuckDB CLI")
    check_parser.add_argument("wheel", help="Wheel file to check")
    check_parser.add_argument("--cli", help="Path to DuckDB CLI (default: download the matching release)")
    check_parser.add_argument("--no-deps", action="store_true", help="Pass --no-deps to pip")
    check_parser.add_argument("--index-url", help="Pass --index-url URL to pip")
    check_parser.add_argument("--find-links", action="append", help="Pass --find-links DIR to pip (repeatable)")
    check_parser.add_argument("--unsigned", action="store_true", help="Pass -unsigned to the CLI")

    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            return _build_command(args)
        elif args.command == "check":
            return _check_command(args)
    except (WheelError, OSError) as e:
        sys.stderr.write(f"error: {e}\n")
        return 1
