#!/usr/bin/env python3
"""Write the extension wheel status page: index.html and status.json into a directory.

Usage:
    uv run --no-project scripts/status_page.py --minimum 1.5.0 --out site
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from duckdb_extensions.status import gather, render, to_json


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Write the extension wheel status page.")
    parser.add_argument("--minimum", required=True, help="Oldest DuckDB version to show (e.g. 1.5.0)")
    parser.add_argument("--out", default="site", help="Output directory")
    parser.add_argument("--github-token", default=None, help="Token for the GitHub API (raises the rate limit)")
    args = parser.parse_args(argv)

    status = gather(args.minimum, token=args.github_token)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(render(status))
    with open(os.path.join(args.out, "status.json"), "w", encoding="utf-8") as f:
        f.write(to_json(status))
    for version in status.versions:
        cells = status.extensions[version]
        published = sum(1 for e in cells for p in cells[e] if cells[e][p] == "published")
        print(f"{version}: {published} wheels published", file=sys.stderr)


if __name__ == "__main__":
    _main()
