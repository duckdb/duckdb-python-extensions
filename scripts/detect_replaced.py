#!/usr/bin/env python3
"""Compare every published extension wheel with the binary extensions.duckdb.org serves today, and write the
findings and the post release items they call for.

Usage:
    uv run --no-project scripts/detect_replaced.py --minimum 1.5.0 --out findings.json --summary summary.md

The workflow dispatches publish.yml with the items and opens an issue from the summary; this script publishes
nothing and changes nothing on the indexes.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from duckdb_extensions.plan import published, stable_versions, stage_attempts
from duckdb_extensions.registry import EXTENSIONS
from duckdb_extensions.replaced import ERROR, HEADERS_ONLY, REBUILT, REPLACED, UNCHANGED, UNKNOWN, WITHDRAWN, NEEDS_POST_RELEASE, detect, post_items

BASELINE = os.path.join(os.path.dirname(__file__), "..", "data", "provenance_baseline.json")


def load_baseline(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def findings_json(minimum, index, baseline, version=None, extensions=None, token=None, now=None, log=None, test_index=None):
    """The findings and the post release items as the JSON-ready dict the workflow reads.

    With a test index, an item whose post release was staged there before and never promoted failed its run
    (a test, or a rejected approval) and is listed as failed, not dispatched again; a run restricted by hand to
    a version or an extension stages it again, as the regular planner does."""
    log = log or sys.stderr
    versions = stable_versions(minimum, token=token)
    if version is not None:
        versions = [v for v in versions if v == version]
    restricted = version is not None or extensions is not None
    extensions = extensions or list(EXTENSIONS)
    findings, state, follow = detect(versions, extensions, index, baseline=baseline, log=log)
    items = post_items(findings, state, follow)
    failed = []
    if test_index is not None and not restricted and items:
        staged = {}
        for extension in sorted({i["extension"] for i in items}):
            try:
                staged[extension] = published(extension, test_index)
            except Exception as exc:  # noqa: BLE001
                staged[extension] = None
                print(f"error {extension}: the test index could not be read ({type(exc).__name__}: {exc}); its item is not dispatched tonight", file=log)
        kept = []
        for item in items:
            state = staged[item["extension"]]
            attempts = stage_attempts(staged, item["extension"], item["wheel_version"]) if state is not None else -1
            if attempts:
                failed.append({**item, "attempts": attempts})
                if attempts > 0:
                    print(f"failed {item['extension']} {item['wheel_version']}: staged {attempts} time(s) without promotion, not dispatched again", file=log)
            else:
                kept.append(item)
        items = kept
    now = now or datetime.now(timezone.utc)
    return {
        "generated": now.isoformat(),
        "findings": [f.to_json() for f in findings if f.outcome != UNCHANGED],
        "compared": len(findings),
        "items": items,
        "failed": failed,
    }


def summary(result):
    """The step summary and issue text: what changed, in plain words, then the items."""
    lines = [f"## Replaced binaries: {len(result['items'])} post release item(s) from {result['compared']} published wheels"]
    for item in result["items"]:
        why = f"changed: {' '.join(item['changed'])}" if item["changed"] else "its requirement changed"
        lines.append(f"- {item['extension']} {item['version']}: {item['wheel_version']} for {' '.join(item['platforms'])} ({why})")
    if result.get("failed"):
        lines.append("")
        lines.append("## Not dispatched: staged before without promotion (a publish run may still wait for approval; if none does, the run failed or was rejected, and only a detect run by hand with the version or extension input stages it again)")
        for item in result["failed"]:
            how = f"{item['attempts']} staging run(s)" if item["attempts"] > 0 else "the test index could not be read tonight"
            lines.append(f"- {item['extension']} {item['version']}: {item['wheel_version']}, {how}")
    by_outcome = {}
    for f in result["findings"]:
        by_outcome.setdefault(f["outcome"], []).append(f)
    for outcome, title in ((REPLACED, "Replaced: the server's binary is from another commit"),
                           (REBUILT, "Rebuilt: the same commit, different bytes"),
                           (WITHDRAWN, "Withdrawn: the server no longer has the file"),
                           (HEADERS_ONLY, "Headers only: the server identifies the same binary by another ETag"),
                           (UNKNOWN, "Unknown: the wheel records no provenance and the baseline has none"),
                           (ERROR, "Error: a request failed, compared again tomorrow")):
        if outcome not in by_outcome:
            continue
        lines.append("")
        lines.append(f"## {title}")
        for f in by_outcome[outcome]:
            old = (f["recorded"] or {}).get("extension_version", "?")
            new = (f["current"] or {}).get("extension_version", "?")
            detail = f"{old} to {new}" if outcome in NEEDS_POST_RELEASE else f.get("detail", "")
            lines.append(" ".join(x for x in ("-", f["extension"], f["version"], f["platform"], detail) if x))
    return "\n".join(lines) + "\n"


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Find published extension wheels whose binary changed on extensions.duckdb.org.")
    parser.add_argument("--minimum", required=True, help="Oldest DuckDB version to compare (e.g. 1.5.0)")
    parser.add_argument("--index", default="https://pypi.org", help="Index the wheels are published on")
    parser.add_argument("--test-index", default=None, help="Staging index; an item staged there before and not promoted is listed as failed, not dispatched")
    parser.add_argument("--baseline", default=BASELINE, help="Provenance of wheels built before wheels recorded it")
    parser.add_argument("--version", default=None, help="Only this DuckDB version")
    parser.add_argument("--extension", action="append", choices=list(EXTENSIONS), help="Only this extension, by project name (repeatable)")
    parser.add_argument("--out", required=True, help="Where to write the findings JSON")
    parser.add_argument("--summary", default=None, help="Where to write the Markdown summary (default: stdout)")
    parser.add_argument("--github-token", default=None, help="Token for the GitHub API (raises the rate limit)")
    args = parser.parse_args(argv)

    result = findings_json(args.minimum, args.index, load_baseline(args.baseline), version=args.version, extensions=args.extension, token=args.github_token, test_index=args.test_index)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, sort_keys=True)
    text = summary(result)
    if args.summary:
        with open(args.summary, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    _main()
