"""Tests for scripts/plan_wheels.py: the JSON the workflows read."""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import plan_wheels
from duckdb_extensions.plan import PublishedVersion

NOW = datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc)
WIN = "duckdb_core_ext_httpfs-2.0.0-py3-none-win_amd64.whl"


def _served(version, extensions, platforms, skip=frozenset()):
    """Stands in for the server: httpfs and ui exist on two platforms; records what was not asked."""
    pairs = {(e, p) for e in extensions for p in platforms if e in ("httpfs", "ui") and p in ("linux-amd64", "windows-amd64")}
    _served.skipped[version] = set(skip)
    return pairs - set(skip)


def test_json_shape_limit_and_skip(capsys):
    _served.skipped = {}
    pub = {"httpfs": {"2.0.0": PublishedVersion(files={WIN}, first_upload=NOW)}}
    with patch.object(plan_wheels, "stable_versions", return_value=["1.5.6", "2.0.0"]), \
         patch.object(plan_wheels, "published", side_effect=lambda e, index: pub.get(e, {})), \
         patch.object(plan_wheels, "available", side_effect=_served):
        result = plan_wheels.plan_json("1.5.0", "https://pypi.org", limit=3, extensions=["httpfs", "ui"], now=NOW)

    assert result == {
        "items": [
            {"version": "2.0.0", "extension": "httpfs", "platforms": ["linux-amd64"]},
            {"version": "2.0.0", "extension": "ui", "platforms": ["linux-amd64", "windows-amd64"]},
            {"version": "1.5.6", "extension": "httpfs", "platforms": ["linux-amd64", "windows-amd64"]},
        ],
        "late": [],
        "failed": [],
        "waiting": [],
    }
    assert _served.skipped == {"2.0.0": {("httpfs", "windows-amd64")}, "1.5.6": set()}
    assert json.loads(json.dumps(result)) == result


def test_version_filter_and_stdout(capsys):
    with patch.object(plan_wheels, "stable_versions", return_value=["1.5.6", "2.0.0"]), \
         patch.object(plan_wheels, "published", return_value={}), \
         patch.object(plan_wheels, "available", side_effect=lambda v, e, p, skip=frozenset(): {("httpfs", "linux-amd64")}):
        plan_wheels._main(["--minimum", "1.5.0", "--version", "2.0.0", "--extension", "httpfs"])

    out = json.loads(capsys.readouterr().out)
    assert out == {"items": [{"version": "2.0.0", "extension": "httpfs", "platforms": ["linux-amd64"]}], "late": [], "failed": [], "waiting": []}


def test_limit_is_clamped_to_the_matrix_maximum():
    versions = [f"1.{i}.0" for i in range(300)]
    with patch.object(plan_wheels, "stable_versions", return_value=versions), \
         patch.object(plan_wheels, "published", return_value={}), \
         patch.object(plan_wheels, "available", side_effect=lambda v, e, p, skip=frozenset(): {("httpfs", "linux-amd64")}):
        result = plan_wheels.plan_json("1.0.0", "https://pypi.org", limit=1000, extensions=["httpfs"], now=NOW)
    assert len(result["items"]) == 256


def test_no_retry_applies_only_to_unrestricted_runs_and_asks_the_test_index_only_for_planned_extensions():
    staged = {"2.0.0.dev1": PublishedVersion(files=set(), first_upload=NOW)}
    asked = []

    def published(e, index):
        if "test.pypi" in index:
            asked.append(e)
            return staged
        return {}

    with patch.object(plan_wheels, "stable_versions", return_value=["2.0.0"]), \
         patch.object(plan_wheels, "published", side_effect=published), \
         patch.object(plan_wheels, "available", side_effect=lambda v, e, p, skip=frozenset(): {(x, "linux-amd64") for x in e if x in ("httpfs", "tpch")}):
        nightly = plan_wheels.plan_json("1.5.0", "https://pypi.org", now=NOW, test_index="https://test.pypi.org")
        asked_nightly = sorted(asked)
        asked.clear()
        by_extension = plan_wheels.plan_json("1.5.0", "https://pypi.org", now=NOW, test_index="https://test.pypi.org", extensions=["httpfs"])
        by_version = plan_wheels.plan_json("1.5.0", "https://pypi.org", now=NOW, test_index="https://test.pypi.org", version="2.0.0")
    assert nightly["items"] == []
    assert [f["extension"] for f in nightly["failed"]] == ["httpfs", "tpch"]
    assert asked_nightly == ["httpfs", "tpch"]
    assert asked == []
    assert by_extension["items"] == [{"version": "2.0.0", "extension": "httpfs", "platforms": ["linux-amd64"]}]
    assert [i["extension"] for i in by_version["items"]] == ["httpfs", "tpch"]
    assert by_extension["failed"] == [] and by_version["failed"] == []
