"""Tests for scripts/detect_replaced.py: the JSON the workflow dispatches from and the summary it posts."""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import detect_replaced
from duckdb_extensions.plan import PublishedVersion
from duckdb_extensions.replaced import HEADERS_ONLY, REPLACED, UNCHANGED, UNKNOWN, Finding, Provenance

NOW = datetime(2026, 11, 18, 5, 0, tzinfo=timezone.utc)
TAG = "manylinux_2_17_x86_64.manylinux2014_x86_64"
OLD = Provenance("77392a7207", "aa", '"e1"', "Tue")
NEW = Provenance("deadbeef00", "bb", '"e2"', "Wed")


def _detect(versions, extensions, index, baseline=None, log=None):
    findings = [
        Finding("2.0.0", "httpfs", "linux-amd64", REPLACED, OLD, NEW),
        Finding("2.0.0", "httpfs", "linux-arm64", UNCHANGED, OLD),
        Finding("2.0.0", "ui", "linux-amd64", HEADERS_ONLY, OLD, Provenance("77392a7207", "aa", '"e3"', "Thu")),
        Finding("1.5.6", "ui", "linux-amd64", UNKNOWN),
    ]
    state = {"httpfs": {"2.0.0": PublishedVersion(files={f"duckdb_core_ext_httpfs-2.0.0-py3-none-{TAG}.whl", "duckdb_core_ext_httpfs-2.0.0-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"}, first_upload=None)}, "ui": {}}
    return findings, state, {}


def test_findings_json_and_items():
    with patch.object(detect_replaced, "stable_versions", return_value=["2.0.0", "1.5.6"]), patch.object(detect_replaced, "detect", side_effect=_detect):
        result = detect_replaced.findings_json("1.5.0", "https://pypi.org", {}, now=NOW)
    assert result["generated"] == "2026-11-18T05:00:00+00:00"
    assert result["compared"] == 4
    assert [f["outcome"] for f in result["findings"]] == [REPLACED, HEADERS_ONLY, UNKNOWN]
    assert result["items"] == [{"version": "2.0.0", "extension": "httpfs", "platforms": ["linux-amd64", "linux-arm64"], "wheel_version": "2.0.0.post1", "changed": ["linux-amd64"]}]
    assert result["failed"] == []
    assert json.loads(json.dumps(result)) == result


def test_an_item_staged_before_without_promotion_is_failed_not_dispatched():
    staged = {"httpfs": {"2.0.0.post1.dev123": PublishedVersion(files={"x"}, first_upload=None), "2.0.0.dev9": PublishedVersion(files={"y"}, first_upload=None)}}
    with patch.object(detect_replaced, "stable_versions", return_value=["2.0.0", "1.5.6"]), patch.object(detect_replaced, "detect", side_effect=_detect), \
         patch.object(detect_replaced, "published", side_effect=lambda e, index: staged.get(e, {})):
        result = detect_replaced.findings_json("1.5.0", "https://pypi.org", {}, now=NOW, test_index="https://test.pypi.org")
    assert result["items"] == []
    assert result["failed"] == [{"version": "2.0.0", "extension": "httpfs", "platforms": ["linux-amd64", "linux-arm64"], "wheel_version": "2.0.0.post1", "changed": ["linux-amd64"], "attempts": 1}]
    text = detect_replaced.summary(result)
    assert "## Not dispatched: staged before without promotion" in text and "- httpfs 2.0.0: 2.0.0.post1, 1 staging run(s)" in text


def test_only_the_post_version_counts_and_only_on_the_test_index():
    staged = {"httpfs": {"2.0.0.dev9": PublishedVersion(files={"y"}, first_upload=None), "2.0.0.post2.dev5": PublishedVersion(files={"z"}, first_upload=None)}}
    asked = []

    def published(extension, index):
        asked.append(index)
        return staged.get(extension, {})

    with patch.object(detect_replaced, "stable_versions", return_value=["2.0.0", "1.5.6"]), patch.object(detect_replaced, "detect", side_effect=_detect), \
         patch.object(detect_replaced, "published", side_effect=published):
        result = detect_replaced.findings_json("1.5.0", "https://pypi.org", {}, now=NOW, test_index="https://test.pypi.org")
    assert [i["wheel_version"] for i in result["items"]] == ["2.0.0.post1"] and result["failed"] == []
    assert asked == ["https://test.pypi.org"]


def test_an_unreadable_test_index_keeps_the_item_back_for_the_night():
    def published(extension, index):
        raise RuntimeError("GET returned 503")

    with patch.object(detect_replaced, "stable_versions", return_value=["2.0.0", "1.5.6"]), patch.object(detect_replaced, "detect", side_effect=_detect), \
         patch.object(detect_replaced, "published", side_effect=published):
        result = detect_replaced.findings_json("1.5.0", "https://pypi.org", {}, now=NOW, test_index="https://test.pypi.org")
    assert result["items"] == [] and result["failed"][0]["attempts"] == -1
    assert "the test index could not be read tonight" in detect_replaced.summary(result)


def test_a_restricted_run_ignores_the_cap():
    staged = {"httpfs": {"2.0.0.post1.dev123": PublishedVersion(files={"x"}, first_upload=None)}}
    with patch.object(detect_replaced, "stable_versions", return_value=["2.0.0", "1.5.6"]), patch.object(detect_replaced, "detect", side_effect=_detect), \
         patch.object(detect_replaced, "published", side_effect=lambda e, index: staged.get(e, {})):
        result = detect_replaced.findings_json("1.5.0", "https://pypi.org", {}, now=NOW, test_index="https://test.pypi.org", extensions=["httpfs"])
    assert [i["wheel_version"] for i in result["items"]] == ["2.0.0.post1"] and result["failed"] == []


def test_a_dependent_item_says_why():
    items = {"items": [{"version": "2.0.0", "extension": "iceberg", "platforms": ["linux-amd64"], "wheel_version": "2.0.0.post1", "changed": []}], "compared": 1, "findings": [], "failed": []}
    assert "- iceberg 2.0.0: 2.0.0.post1 for linux-amd64 (its requirement changed)\n" in detect_replaced.summary(items)


def test_summary_names_every_outcome_in_plain_words():
    with patch.object(detect_replaced, "stable_versions", return_value=["2.0.0", "1.5.6"]), patch.object(detect_replaced, "detect", side_effect=_detect):
        text = detect_replaced.summary(detect_replaced.findings_json("1.5.0", "https://pypi.org", {}, now=NOW))
    assert text.startswith("## Replaced binaries: 1 post release item(s) from 4 published wheels\n- httpfs 2.0.0: 2.0.0.post1 for linux-amd64 linux-arm64 (changed: linux-amd64)\n")
    assert "## Replaced: the server's binary is from another commit\n- httpfs 2.0.0 linux-amd64 77392a7207 to deadbeef00\n" in text
    assert "## Headers only" in text and "- ui 2.0.0 linux-amd64\n" in text
    assert "## Unknown" in text and "- ui 1.5.6 linux-amd64\n" in text
    assert "Rebuilt" not in text and "Withdrawn" not in text


def test_main_writes_both_files_and_filters(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"httpfs 2.0.0 linux-amd64": {"extension_version": "a", "sha256": "b"}}))
    seen = {}

    def detect(versions, extensions, index, baseline=None, log=None):
        seen.update(versions=versions, extensions=extensions, index=index, baseline=baseline)
        return [], {}, {}

    with patch.object(detect_replaced, "stable_versions", return_value=["2.0.0", "1.5.6"]), patch.object(detect_replaced, "detect", side_effect=detect):
        detect_replaced._main(["--minimum", "1.5.0", "--version", "2.0.0", "--extension", "httpfs", "--baseline", str(baseline), "--out", str(tmp_path / "f.json"), "--summary", str(tmp_path / "s.md")])
    assert seen == {"versions": ["2.0.0"], "extensions": ["httpfs"], "index": "https://pypi.org", "baseline": {"httpfs 2.0.0 linux-amd64": {"extension_version": "a", "sha256": "b"}}}
    assert json.loads((tmp_path / "f.json").read_text())["items"] == []
    assert (tmp_path / "s.md").read_text().startswith("## Replaced binaries: 0 post release item(s) from 0 published wheels\n")


def test_missing_baseline_is_empty(tmp_path):
    assert detect_replaced.load_baseline(str(tmp_path / "none.json")) == {}
    assert detect_replaced.load_baseline(None) == {}
