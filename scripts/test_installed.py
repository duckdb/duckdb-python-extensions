#!/usr/bin/env python3
"""Install extension packages from an index into a fresh environment and load each one in the matching DuckDB CLI.

Usage:
    uv run --no-project scripts/test_installed.py --platform linux-arm64 --index https://test.pypi.org/simple/ \
        --version 2.0.0 --wheel-version 2.0.0.dev42 --extension httpfs --extension tpch --results results.json
    uv run --no-project scripts/test_installed.py --platform linux-arm64 --plan plan.json --stage-suffix .dev42 --results results.json

The DuckDB CLI of the version is downloaded from the GitHub release, so the check does not depend on the
duckdb-cli package. Each extension gets its own install target, holding only it and the released wheels its
LOAD requires, and is loaded with that target's directory as the only extension directory and no network
for extensions, so a pass means the wheel's binary is the one the engine loaded and nothing else was needed. Results are
merged into the JSON file as {"<version> <extension> <platform>": {"ok": bool, "detail": str}} after every
extension, and the exit status is non-zero when any failed.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import venv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import duckdb_extension_wheel.check as check
from duckdb_extension_wheel.check import cli_home, download_cli, site_packages

from duckdb_extensions.registry import EXTENSIONS, PLATFORMS, REQUIRES, project_name, server_name


# A staging upload can take a few minutes to appear on the index, so an install is retried within the run.
INSTALL_ATTEMPTS = 4
INSTALL_RETRY_DELAY = 60


def install(python, index, requirements, target):
    """pip install into target from a simple index URL, or from a local directory of wheels when index is a
    directory; a failing install from a URL is retried a few times, a failure here is a failed test."""
    local = os.path.isdir(index)
    source = ["--no-index", "--find-links", index] if local else ["--index-url", index]
    cmd = [python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--only-binary", ":all:", "--no-deps", "--target", target, *source, *requirements]
    attempts = 1 if local else INSTALL_ATTEMPTS
    for attempt in range(attempts):
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode == 0 or attempt + 1 == attempts:
            return proc
        time.sleep(INSTALL_RETRY_DELAY)
    return proc


def verdict(returncode, stdout, name, ext_dir):
    """Whether the CLI's answer shows the extension loaded from ext_dir and not from anywhere else."""
    return check.verdict(returncode, stdout, server_name(name), ext_dir)


def load_in_cli(cli, ext_dir, name, home):
    """Run LOAD in the CLI with ext_dir as the only extension directory and no autoinstall; returns (ok, detail).

    An output mode flag such as -csv also keeps the 2.0 CLI from printing its agent-mode notice."""
    return check.load_in_cli(cli, ext_dir, server_name(name), home)


def required_releases(name, version):
    """pip requirements for the released wheels the extension's LOAD needs; they are on pypi.org before the
    extension is ever planned."""
    return [f"{project_name(dep)}=={version}" for dep in REQUIRES.get(name, [])]


def _record(results, results_path, key, ok, detail):
    results[key] = {"ok": ok, "detail": detail[-2000:]}
    print(("ok   " if ok else "FAIL ") + key + ("" if ok else ": " + detail.splitlines()[-1] if detail else ""), file=sys.stderr)
    with open(results_path, "w") as f:
        json.dump(results, f, indent=1, sort_keys=True)


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def _describe(e):
    if isinstance(e, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
        return f"{e!r}: {(_text(e.stderr) or _text(e.stdout))[-1000:]}"
    return repr(e)


def run(version, wheel_version, platform, extensions, index, results_path, release_index="https://pypi.org/simple/"):
    """Test every extension of one version, each in its own install target so that a missing requirement
    shows; each result is written as soon as it is known. Returns whether every extension tested here passed."""
    results = {}
    if os.path.exists(results_path):
        with open(results_path) as f:
            results = json.load(f)
    keys = [f"{version} {name} {platform}" for name in extensions]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            env_dir = os.path.join(tmp, "venv")
            venv.create(env_dir, with_pip=True)
            python, _ = site_packages(env_dir)
            cli = download_cli(version, platform, tmp)
            home = cli_home(tmp)
        except Exception as e:
            for key in keys:
                _record(results, results_path, key, False, f"setup: {_describe(e)}")
            return False
        for name, key in zip(extensions, keys):
            target = os.path.join(tmp, "site", name)
            ext_dir = os.path.join(target, ".duckdb_extensions")
            try:
                needed = required_releases(name, version)
                pip = install(python, release_index if not os.path.isdir(index) else index, needed, target) if needed else None
                if pip is None or pip.returncode == 0:
                    pip = install(python, index, [f"{project_name(name)}=={wheel_version}"], target)
                if pip.returncode != 0:
                    _record(results, results_path, key, False, "pip: " + (pip.stdout + pip.stderr).strip())
                    continue
                expected = os.path.join(ext_dir, f"v{version}", PLATFORMS[platform][0], f"{server_name(name)}.duckdb_extension")
                if not os.path.isfile(expected):
                    _record(results, results_path, key, False, f"missing {expected}")
                    continue
                ok, detail = load_in_cli(cli, ext_dir, name, home)
            except Exception as e:
                ok, detail = False, _describe(e)
            _record(results, results_path, key, ok, detail)
    return all(results[key]["ok"] for key in keys)


def items_for_platform(plan, platform):
    """{version: [project names]} of the plan's items that have the platform."""
    by_version = {}
    for item in plan["items"]:
        if platform in item["platforms"]:
            by_version.setdefault(item["version"], []).append(item["extension"])
    return by_version


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Install staged extension wheels and load them in the DuckDB CLI.")
    parser.add_argument("--platform", required=True, choices=list(PLATFORMS))
    parser.add_argument("--index", default="https://test.pypi.org/simple/", help="Simple index URL, or a local directory of wheels, to install from")
    parser.add_argument("--results", required=True, help="JSON file to write results into (merged with existing content)")
    one = parser.add_argument_group("one version")
    one.add_argument("--version", help="DuckDB version (e.g. 2.0.0)")
    one.add_argument("--wheel-version", default=None, help="Version of the wheels on the index (default: --version)")
    one.add_argument("--extension", action="append", choices=list(EXTENSIONS), help="Project name (repeatable)")
    many = parser.add_argument_group("every version of a plan")
    many.add_argument("--plan", help="plan.json from plan_wheels.py; tests every item that has the platform")
    many.add_argument("--stage-suffix", default="", help="Appended to each version to get the wheel version on the index (e.g. .dev42)")
    args = parser.parse_args(argv)

    if args.plan:
        with open(args.plan) as f:
            plan = json.load(f)
        ok = True
        for version, extensions in sorted(items_for_platform(plan, args.platform).items()):
            ok = run(version, version + args.stage_suffix, args.platform, extensions, args.index, args.results) and ok
    elif args.version and args.extension:
        ok = run(args.version, args.wheel_version or args.version, args.platform, args.extension, args.index, args.results)
    else:
        parser.error("give --plan, or --version with at least one --extension")
    print("all passed" if ok else "some failed", file=sys.stderr)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _main()
