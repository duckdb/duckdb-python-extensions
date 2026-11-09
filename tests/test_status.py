"""Tests for duckdb_extensions.status: the state of each cell and the rendered page."""

import json
from datetime import datetime, timedelta, timezone

from duckdb_extensions.plan import PublishedVersion
from duckdb_extensions.status import (
    ABSENT,
    AVAILABLE,
    LATE,
    PUBLISHED,
    STAGED,
    WAITING,
    Status,
    apply_requirements,
    classify,
    module_wheels,
    render,
    to_json,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
TAG = "manylinux_2_17_x86_64.manylinux2014_x86_64"


def _pub(version, files, first_upload=NOW - timedelta(days=1)):
    return {version: PublishedVersion(files=set(files), first_upload=first_upload)}


class TestClassify:
    SERVED = {("tpch", "linux-amd64"), ("tpch", "osx-arm64")}

    def test_absent_when_the_server_lacks_it(self):
        assert classify("2.0.0", "tpch", "windows-arm64", self.SERVED, {}, {}, NOW) == ABSENT

    def test_published_wins(self):
        on_pypi = _pub("2.0.0", [f"duckdb_core_ext_tpch-2.0.0-py3-none-{TAG}.whl"])
        on_test = _pub("2.0.0.dev5", [f"duckdb_core_ext_tpch-2.0.0.dev5-py3-none-{TAG}.whl"])
        assert classify("2.0.0", "tpch", "linux-amd64", self.SERVED, on_pypi, on_test, NOW) == PUBLISHED

    def test_staged_when_only_a_dev_wheel_exists_for_the_platform(self):
        on_test = _pub("2.0.0.dev5", [f"duckdb_core_ext_tpch-2.0.0.dev5-py3-none-{TAG}.whl"])
        assert classify("2.0.0", "tpch", "linux-amd64", self.SERVED, {}, on_test, NOW) == STAGED
        assert classify("2.0.0", "tpch", "osx-arm64", self.SERVED, {}, on_test, NOW) == AVAILABLE

    def test_staging_of_another_version_does_not_count(self):
        on_test = _pub("1.5.6.dev5", [f"duckdb_core_ext_tpch-1.5.6.dev5-py3-none-{TAG}.whl"])
        assert classify("2.0.0", "tpch", "linux-amd64", self.SERVED, {}, on_test, NOW) == AVAILABLE

    def test_late_when_the_release_is_old_and_the_platform_missing(self):
        on_pypi = _pub("2.0.0", [f"duckdb_core_ext_tpch-2.0.0-py3-none-macosx_12_0_arm64.whl"], first_upload=NOW - timedelta(days=20))
        assert classify("2.0.0", "tpch", "linux-amd64", self.SERVED, on_pypi, {}, NOW) == LATE
        assert classify("2.0.0", "tpch", "osx-arm64", self.SERVED, on_pypi, {}, NOW) == PUBLISHED


class TestRequirements:
    def test_dependent_waits_until_the_dependency_is_published_on_that_platform(self):
        cells = {"iceberg": {"linux-amd64": AVAILABLE, "osx-arm64": AVAILABLE, "windows-arm64": ABSENT}, "avro": {"linux-amd64": PUBLISHED, "osx-arm64": AVAILABLE}}
        on_pypi = {"avro": _pub("2.0.0", [f"duckdb_core_ext_avro-2.0.0-py3-none-{TAG}.whl"])}
        apply_requirements(cells, "2.0.0", on_pypi)
        assert cells["iceberg"] == {"linux-amd64": AVAILABLE, "osx-arm64": WAITING, "windows-arm64": ABSENT}


def test_module_wheels_groups_python_tags_and_platforms():
    files = [
        "duckdb-2.0.0-cp311-cp311-win_amd64.whl",
        "duckdb-2.0.0-cp311-cp311-macosx_11_0_arm64.whl",
        "duckdb-2.0.0-cp312-cp312-win_amd64.whl",
        "duckdb-2.0.0.tar.gz",
        "duckdb-1.5.6-cp311-cp311-win_amd64.whl",
    ]
    assert module_wheels(files, "2.0.0") == {"cp311": ["macosx_11_0_arm64", "win_amd64"], "cp312": ["win_amd64"]}


def _status():
    status = Status(generated=NOW, versions=["2.0.0", "1.5.6"])
    from duckdb_extensions.registry import EXTENSIONS, PLATFORMS

    for v in status.versions:
        status.extensions[v] = {e: {p: ABSENT for p in PLATFORMS} for e in EXTENSIONS}
    status.extensions["2.0.0"]["tpch"]["linux-amd64"] = PUBLISHED
    status.extensions["2.0.0"]["tpch"]["osx-arm64"] = STAGED
    status.extensions["2.0.0"]["iceberg"]["osx-arm64"] = WAITING
    status.cli = {"2.0.0": {"linux-amd64": True, "osx-arm64": False}, "1.5.6": {}}
    status.module = {"2.0.0": {"cp312": ["win_amd64"]}, "1.5.6": {}}
    status.unregistered = {"2.0.0": {"glue": ["linux-amd64", "osx-arm64"]}, "1.5.6": {}}
    return status


def test_render_shows_every_section_and_the_counts():
    page = render(_status())
    assert "<h2>DuckDB 2.0.0</h2>" in page and "<h2>DuckDB 1.5.6</h2>" in page
    assert "1 published, 1 staged, 1 waiting" in page
    assert 'class="published" title="on pypi.org"' in page
    assert "duckdb-core-ext-tpch" in page and "duckdb-core-ext-postgres" in page
    assert "<h2>duckdb-cli</h2>" in page and "duckdb-cli 2.0.0" in page
    assert "<h3>duckdb 2.0.0</h3>" in page and "cp312" in page and "no wheels on pypi.org" in page
    assert "Generated 2026-11-08 12:00 UTC" in page
    assert "not in the registry yet, so no wheels: glue (linux-amd64 osx-arm64)" in page
    assert "status.json" in page


def test_json_round_trips_and_carries_the_legend():
    data = json.loads(to_json(_status()))
    assert data["versions"] == ["2.0.0", "1.5.6"]
    assert data["extensions"]["2.0.0"]["tpch"]["linux-amd64"] == "published"
    assert data["duckdb_cli"]["2.0.0"]["linux-amd64"] is True
    assert data["duckdb_module"]["2.0.0"] == {"cp312": ["win_amd64"]}
    assert set(data["legend"]) == {PUBLISHED, STAGED, WAITING, LATE, AVAILABLE, ABSENT}
    assert data["unregistered"]["2.0.0"] == {"glue": ["linux-amd64", "osx-arm64"]}
