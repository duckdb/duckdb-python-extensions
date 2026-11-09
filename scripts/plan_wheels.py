#!/usr/bin/env python3
"""Print, as JSON, the extension wheels the index lacks for every stable DuckDB release at or above a floor.

Usage:
    uv run --no-project scripts/plan_wheels.py --minimum 1.5.0 [--index https://test.pypi.org] [--limit 200]
    uv run --no-project scripts/plan_wheels.py --minimum 1.5.0 --version 2.0.0 --extension httpfs

Pairs whose wheel is already on the index are not asked of the server, so a steady-state run makes few requests.

Output: {"items": [{"version", "extension", "platforms": [...]}], "late": [{"version", "extension", "platform", "first_upload"}],
         "failed": [{"version", "extension", "attempts"}], "waiting": [{"version", "extension", "requires"}],
         "unregistered": [{"version", "extension", "platforms": [...]}]}
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from duckdb_extensions.discover import configured_extensions, unregistered
from duckdb_extensions.plan import available, plan, published, stable_versions
from duckdb_extensions.registry import EXTENSIONS, PLATFORMS, wheel_filename

MAX_ITEMS = 256


def plan_json(minimum, index, limit=None, version=None, extensions=None, token=None, now=None, log=sys.stderr, test_index=None):
    """The work items, late pairs, failed and waiting items as the JSON-ready dict the workflows read.

    With a test index, an item staged there before without reaching the index failed its test and is left
    out, listed as failed; a run restricted to one version or extension by hand stages it again."""
    versions = stable_versions(minimum, token=token)
    if version is not None:
        versions = [v for v in versions if v == version]
    restricted = version is not None or extensions is not None
    extensions = extensions or list(EXTENSIONS)
    print(f"Stable releases considered: {' '.join(versions) or 'none'}", file=log)

    on_index = {e: published(e, index) for e in extensions}
    served = {}
    for v in versions:
        have = {
            (e, p)
            for e in extensions
            for p in PLATFORMS
            if v in on_index[e] and wheel_filename(e, v, p) in on_index[e][v].files
        }
        served[v] = available(v, extensions, list(PLATFORMS), skip=have)
    now = now or datetime.now(timezone.utc)
    result = plan(versions, served, on_index, now=now)
    if test_index is not None and not restricted and result.items:
        # The test index is asked only about the extensions that have something to build.
        staged = {i.extension: published(i.extension, test_index) for i in result.items}
        result = plan(versions, served, on_index, now=now, staged=staged)
    # A GitHub Actions matrix holds at most 256 jobs.
    items = result.items[: min(limit, MAX_ITEMS) if limit is not None else MAX_ITEMS]

    for item in items:
        print(f"build {item.extension} {item.version}: {' '.join(item.platforms)}", file=log)
    for entry in result.late:
        print(f"late {entry.extension} {entry.version} {entry.platform}: release first uploaded {entry.first_upload.isoformat()}", file=log)
    for entry in result.failed:
        print(f"failed {entry.extension} {entry.version}: staged {entry.attempts} time(s) without promotion, not retried", file=log)
    for entry in result.waiting:
        print(f"waiting {entry.extension} {entry.version}: needs the released {entry.requires} wheels first", file=log)

    # Served extensions the registry does not know yet: reported, never built, since a new project needs a name
    # decision and publishers first.
    new_names = []
    if not restricted:
        for v in versions:
            for name, plats in unregistered(v, configured_extensions(v, token=token)).items():
                new_names.append({"version": v, "extension": name, "platforms": plats})
                print(f"unregistered {name} {v}: served for {' '.join(plats)}, not in the registry", file=log)

    return {
        "items": [{"version": i.version, "extension": i.extension, "platforms": i.platforms} for i in items],
        "late": [{"version": l.version, "extension": l.extension, "platform": l.platform, "first_upload": l.first_upload.isoformat()} for l in result.late],
        "failed": [{"version": f.version, "extension": f.extension, "attempts": f.attempts} for f in result.failed],
        "waiting": [{"version": w.version, "extension": w.extension, "requires": w.requires} for w in result.waiting],
        "unregistered": new_names,
    }


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Plan the extension wheels to build and publish.")
    parser.add_argument("--minimum", required=True, help="Oldest DuckDB version to consider (e.g. 1.5.0)")
    parser.add_argument("--index", default="https://pypi.org", help="Package index base URL")
    parser.add_argument("--limit", type=int, default=None, help="At most this many work items, newest version first")
    parser.add_argument("--version", default=None, help="Consider only this DuckDB version")
    parser.add_argument("--extension", action="append", choices=list(EXTENSIONS), help="Consider only this extension, by project name (repeatable)")
    parser.add_argument("--github-token", default=None, help="Token for the GitHub API (raises the rate limit)")
    parser.add_argument("--test-index", default=None, help="Staging index base URL; items staged there before without promotion are left out as failed")
    args = parser.parse_args(argv)

    result = plan_json(args.minimum, args.index, limit=args.limit, version=args.version, extensions=args.extension, token=args.github_token, test_index=args.test_index)
    json.dump(result, sys.stdout)
    print()


if __name__ == "__main__":
    _main()
