#!/usr/bin/env python3
"""Record what the wheels published before wheels carried a provenance section hold, so the nightly comparison
covers them too.

Usage:
    uv run --no-project scripts/provenance_baseline.py --minimum 1.5.0 --out data/provenance_baseline.json

For every published wheel without provenance in its metadata, the wheel is downloaded from the index and the
binary inside it read (extension_version from the footer, sha256), then the server's file is downloaded and
compared. When both agree, the entry records the server's ETag and Last-Modified, so the nightly can skip the
download while they hold. When they differ, the entry records no headers, so the nightly downloads and reports
the difference every night until a post release carries the server's binary; the difference is printed here.
The output file is updated after every wheel, so an interrupted run continues where it stopped.
"""

import argparse
import gzip
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from duckdb_extension_wheel.net import get_bytes, get_bytes_headers, get_json, get_text
from duckdb_extension_wheel.wheel import dist_name, read_provenance
from duckdb_extensions.plan import parse_version, published, stable_versions
from duckdb_extensions.registry import EXTENSIONS, PLATFORMS, extension_url, project_name, server_name, server_platform, wheel_filename
from duckdb_extensions.replaced import Provenance, baseline_key, metadata_url, project_page


def binary_in_wheel(wheel_bytes, extension, version, platform):
    """The extension binary a published wheel carries."""
    dist = dist_name(project_name(extension))
    path = f"{dist}-{version}.data/platlib/.duckdb_extensions/v{version}/{server_platform(platform)}/{server_name(extension)}.duckdb_extension"
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as zf:
        return zf.read(path)


def baseline_entry(wheel_binary, server_file, url):
    """What the baseline records for one wheel, given the binary it carries and the server's file (bytes and
    headers) today; the headers are kept only when the two binaries agree."""
    wheel = Provenance.of_binary(wheel_binary, source=url)
    compressed, headers = server_file
    if compressed is None:
        return wheel, "withdrawn"
    server = Provenance.of_binary(gzip.decompress(compressed), headers.get("etag"), headers.get("last_modified"), url)
    if server.sha256 == wheel.sha256:
        return server, "same"
    outcome = "replaced" if server.extension_version != wheel.extension_version else "rebuilt"
    return wheel, f"{outcome}: wheel {wheel.extension_version}, server {server.extension_version}"


def needs_baseline(page, filename, fetch_text=get_text):
    """A wheel whose metadata already records provenance needs no baseline entry."""
    url = metadata_url(page, filename)
    if url is None:
        return True
    text = fetch_text(url)
    return not (text and Provenance.from_lines(read_provenance(text)))


def file_url(page, filename):
    return next(f["url"] for f in page["files"] if f["filename"] == filename)


def build(minimum, index, out, version=None, extensions=None, token=None, log=None, fetch_json=get_json, fetch_text=get_text, fetch_bytes=get_bytes, fetch_file=get_bytes_headers):
    log = log or sys.stderr
    baseline = {}
    if os.path.exists(out):
        with open(out) as f:
            baseline = json.load(f)
    versions = stable_versions(minimum, token=token)
    if version is not None:
        versions = [v for v in versions if v == version]
    extensions = extensions or list(EXTENSIONS)
    differences = []
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    for extension in sorted(extensions):
        page = project_page(extension, index, fetch_json)
        state = published(extension, index, fetch=lambda url, accept: page)
        for v in sorted(versions, key=parse_version, reverse=True):
            if v not in state:
                continue
            for platform in PLATFORMS:
                filename = wheel_filename(extension, v, platform)
                key = baseline_key(extension, v, platform)
                if filename not in state[v].files or key in baseline:
                    continue
                if not needs_baseline(page, filename, fetch_text):
                    print(f"{key}: the wheel records its provenance", file=log)
                    continue
                wheel_binary = binary_in_wheel(fetch_bytes(file_url(page, filename)), extension, v, platform)
                url = extension_url(extension, v, platform)
                entry, verdict = baseline_entry(wheel_binary, fetch_file(url), url)
                baseline[key] = {"extension_version": entry.extension_version, "sha256": entry.sha256, "etag": entry.etag, "last_modified": entry.last_modified, "source": entry.source}
                print(f"{key}: {verdict}", file=log)
                if verdict != "same":
                    differences.append(f"{key}: {verdict}")
                with open(out, "w", encoding="utf-8") as f:
                    json.dump(baseline, f, indent=1, sort_keys=True)
    return baseline, differences


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Record the provenance of wheels published before wheels carried one.")
    parser.add_argument("--minimum", required=True, help="Oldest DuckDB version (e.g. 1.5.0)")
    parser.add_argument("--index", default="https://pypi.org", help="Index the wheels are published on")
    parser.add_argument("--out", required=True, help="The baseline JSON to write (existing entries are kept)")
    parser.add_argument("--version", default=None, help="Only this DuckDB version")
    parser.add_argument("--extension", action="append", choices=list(EXTENSIONS), help="Only this extension, by project name (repeatable)")
    parser.add_argument("--github-token", default=None, help="Token for the GitHub API (raises the rate limit)")
    args = parser.parse_args(argv)
    baseline, differences = build(args.minimum, args.index, args.out, version=args.version, extensions=args.extension, token=args.github_token)
    print(f"{len(baseline)} entries in {args.out}; {len(differences)} wheel(s) differ from the server today", file=sys.stderr)
    for d in differences:
        print(f"  {d}", file=sys.stderr)


if __name__ == "__main__":
    _main()
