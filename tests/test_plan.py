"""Tests for duckdb_extensions.plan: what the server has, what PyPI has, and what to build."""

import io
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from duckdb_extensions.plan import (
    Late,
    PublishedVersion,
    Stalled,
    Waiting,
    WorkItem,
    available,
    plan,
    published,
    stable_versions,
)

NOW = datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc)
FRESH = NOW - timedelta(days=2)
OLD = NOW - timedelta(days=15)


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO())


def _two(result):
    assert result.stalled == [] and result.waiting == []
    return result.items, result.late


def _items(result):
    return result.items


def _three(result):
    assert result.waiting == []
    return result.items, result.late, result.stalled


def _is(result):
    assert result.late == [] and result.waiting == []
    return result.items, result.stalled


def _empty(result):
    return result.items == [] and result.late == [] and result.stalled == [] and result.waiting == []


class TestAvailable:
    def test_returns_pairs_the_server_has(self):
        present = {
            "https://extensions.duckdb.org/v2.0.0/linux_amd64/httpfs.duckdb_extension.gz",
            "https://extensions.duckdb.org/v2.0.0/osx_arm64/httpfs.duckdb_extension.gz",
            "https://extensions.duckdb.org/v2.0.0/linux_amd64/ui.duckdb_extension.gz",
        }
        asked = []

        def head(url):
            asked.append(url)
            return 200 if url in present else 404

        result = available("2.0.0", ["httpfs", "ui"], ["linux-amd64", "osx-arm64"], head=head)

        assert result == {("httpfs", "linux-amd64"), ("httpfs", "osx-arm64"), ("ui", "linux-amd64")}
        assert len(asked) == 4
        assert len(set(asked)) == 4

    def test_other_status_raises(self):
        with pytest.raises(RuntimeError, match="503"):
            available("2.0.0", ["httpfs"], ["linux-amd64"], head=lambda url: 503)

    def test_nothing_served(self):
        assert available("9.9.9", ["httpfs"], ["linux-amd64"], head=lambda url: 404) == set()

    def test_skipped_pairs_are_not_asked_for(self):
        asked = []

        def head(url):
            asked.append(url)
            return 200

        result = available("2.0.0", ["httpfs"], ["linux-amd64", "osx-arm64"], head=head, skip={("httpfs", "osx-arm64")})

        assert result == {("httpfs", "linux-amd64")}
        assert len(asked) == 1 and asked[0].endswith("/linux_amd64/httpfs.duckdb_extension.gz")


class TestPublished:
    PAGE = {
        "versions": ["1.5.6", "2.0.0"],
        "files": [
            {"filename": "duckdb_core_ext_httpfs-1.5.6-py3-none-win_amd64.whl", "upload-time": "2026-09-29T07:12:01.000000Z"},
            {"filename": "duckdb_core_ext_httpfs-1.5.6-py3-none-macosx_12_0_arm64.whl", "upload-time": "2026-09-28T23:59:59Z"},
            {"filename": "duckdb_core_ext_httpfs-2.0.0-py3-none-win_amd64.whl", "upload-time": "2026-11-04T03:05:00Z"},
        ],
    }

    def test_groups_files_by_version_with_earliest_upload(self):
        asked = []

        def fetch(url, accept):
            asked.append((url, accept))
            return self.PAGE

        result = published("httpfs", "https://pypi.org", fetch=fetch)

        assert asked == [("https://pypi.org/simple/duckdb-core-ext-httpfs/", "application/vnd.pypi.simple.v1+json")]
        assert result == {
            "1.5.6": PublishedVersion(
                files={
                    "duckdb_core_ext_httpfs-1.5.6-py3-none-win_amd64.whl",
                    "duckdb_core_ext_httpfs-1.5.6-py3-none-macosx_12_0_arm64.whl",
                },
                first_upload=datetime(2026, 9, 28, 23, 59, 59, tzinfo=timezone.utc),
            ),
            "2.0.0": PublishedVersion(
                files={"duckdb_core_ext_httpfs-2.0.0-py3-none-win_amd64.whl"},
                first_upload=datetime(2026, 11, 4, 3, 5, tzinfo=timezone.utc),
            ),
        }

    def test_unknown_project_is_empty(self):
        def fetch(url, accept):
            raise _http_error(url, 404)

        assert published("httpfs", "https://pypi.org", fetch=fetch) == {}

    def test_other_errors_propagate(self):
        def fetch(url, accept):
            raise _http_error(url, 500)

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            published("httpfs", "https://pypi.org", fetch=fetch)
        excinfo.value.close()

    def test_underscore_name_yanked_and_non_wheel_files(self):
        page = {
            "versions": ["0.0.0", "2.0.0"],
            "files": [
                {"filename": "duckdb_core_ext_legacy_parser-2.0.0-py3-none-win_amd64.whl", "upload-time": "2026-11-04T03:05:00Z", "yanked": True},
                {"filename": "duckdb_core_ext_legacy_parser-2.0.0.tar.gz", "upload-time": "2026-11-04T03:05:00Z"},
                {"filename": "duckdb_core_ext_legacy_parser-0.0.0-py3-none-any.whl", "upload-time": "2026-11-05T16:24:01.041878Z"},
            ],
        }
        asked = []

        def fetch(url, accept):
            asked.append(url)
            return page

        result = published("legacy_parser", "https://pypi.org", fetch=fetch)

        assert asked == ["https://pypi.org/simple/duckdb-core-ext-legacy_parser/"]
        assert result["2.0.0"].files == {"duckdb_core_ext_legacy_parser-2.0.0-py3-none-win_amd64.whl"}
        assert set(result) == {"0.0.0", "2.0.0"}

    def test_file_without_upload_time_leaves_first_upload_unknown(self):
        page = {"versions": ["2.0.0"], "files": [{"filename": "duckdb_core_ext_httpfs-2.0.0-py3-none-win_amd64.whl"}]}

        result = published("httpfs", "https://pypi.org", fetch=lambda url, accept: page)

        assert result["2.0.0"].first_upload is None


def _wheel(ext, version, tag):
    return f"duckdb_core_ext_{ext}-{version}-py3-none-{tag}.whl"


class TestPlan:
    def test_fresh_version_plans_everything_the_server_has(self):
        avail = {"2.0.0": {("httpfs", "linux-amd64"), ("httpfs", "osx-arm64"), ("ui", "linux-amd64")}}

        items, late = _two(plan(["2.0.0"], avail, {}, now=NOW))

        assert items == [
            WorkItem("2.0.0", "httpfs", ["linux-amd64", "osx-arm64"]),
            WorkItem("2.0.0", "ui", ["linux-amd64"]),
        ]
        assert late == []

    def test_published_wheels_are_not_planned_again(self):
        avail = {"2.0.0": {("httpfs", "linux-amd64"), ("httpfs", "osx-arm64")}}
        pub = {
            "httpfs": {
                "2.0.0": PublishedVersion(
                    files={_wheel("httpfs", "2.0.0", "manylinux_2_17_x86_64.manylinux2014_x86_64"), _wheel("httpfs", "2.0.0", "macosx_12_0_arm64")},
                    first_upload=FRESH,
                )
            }
        }

        assert _empty(plan(["2.0.0"], avail, pub, now=NOW))

    def test_missing_platform_within_window_is_planned(self):
        avail = {"2.0.0": {("httpfs", "linux-amd64"), ("httpfs", "osx-arm64")}}
        pub = {"httpfs": {"2.0.0": PublishedVersion(files={_wheel("httpfs", "2.0.0", "macosx_12_0_arm64")}, first_upload=FRESH)}}

        items, late = _two(plan(["2.0.0"], avail, pub, now=NOW))

        assert items == [WorkItem("2.0.0", "httpfs", ["linux-amd64"])]
        assert late == []

    def test_missing_platform_after_window_is_late_not_planned(self):
        avail = {"2.0.0": {("httpfs", "linux-amd64"), ("httpfs", "osx-arm64")}}
        pub = {"httpfs": {"2.0.0": PublishedVersion(files={_wheel("httpfs", "2.0.0", "macosx_12_0_arm64")}, first_upload=OLD)}}

        items, late = _two(plan(["2.0.0"], avail, pub, now=NOW))

        assert items == []
        assert late == [Late("2.0.0", "httpfs", "linux-amd64", first_upload=OLD)]

    def test_window_closes_a_margin_before_fourteen_days(self):
        avail = {"2.0.0": {("httpfs", "linux-amd64"), ("httpfs", "osx-arm64")}}
        margin = timedelta(hours=12)

        def with_first_upload(first_upload):
            pub = {"httpfs": {"2.0.0": PublishedVersion(files={_wheel("httpfs", "2.0.0", "macosx_12_0_arm64")}, first_upload=first_upload)}}
            return plan(["2.0.0"], avail, pub, now=NOW)

        planned = [WorkItem("2.0.0", "httpfs", ["linux-amd64"])]
        assert _two(with_first_upload(NOW - timedelta(days=14) + margin + timedelta(seconds=1))) == (planned, [])
        assert with_first_upload(NOW - timedelta(days=14) + margin).items == planned
        late_upload = NOW - timedelta(days=14) + margin - timedelta(seconds=1)
        assert _two(with_first_upload(late_upload)) == ([], [Late("2.0.0", "httpfs", "linux-amd64", first_upload=late_upload)])

    def test_unknown_first_upload_counts_as_within_window(self):
        avail = {"2.0.0": {("httpfs", "linux-amd64"), ("httpfs", "osx-arm64")}}
        pub = {"httpfs": {"2.0.0": PublishedVersion(files={_wheel("httpfs", "2.0.0", "macosx_12_0_arm64")}, first_upload=None)}}

        items, late = _two(plan(["2.0.0"], avail, pub, now=NOW))

        assert items == [WorkItem("2.0.0", "httpfs", ["linux-amd64"])]
        assert late == []

    def test_platform_absent_from_server_is_silently_skipped(self):
        avail = {"2.0.0": {("ui", "linux-amd64")}}

        items, late = _two(plan(["2.0.0"], avail, {}, now=NOW))

        assert items == [WorkItem("2.0.0", "ui", ["linux-amd64"])]
        assert late == []

    def test_other_versions_of_the_project_do_not_matter(self):
        avail = {"2.0.0": {("httpfs", "linux-amd64")}}
        pub = {"httpfs": {"1.5.6": PublishedVersion(files={_wheel("httpfs", "1.5.6", "manylinux_2_17_x86_64.manylinux2014_x86_64")}, first_upload=OLD)}}

        items, late = _two(plan(["2.0.0"], avail, pub, now=NOW))

        assert items == [WorkItem("2.0.0", "httpfs", ["linux-amd64"])]

    def test_order_is_newest_version_then_extension(self):
        avail = {
            "2.0.0": {("httpfs", "linux-amd64"), ("aws", "linux-amd64")},
            "1.5.6": {("ui", "linux-amd64")},
            "1.10.0": {("fts", "linux-amd64")},
        }

        items = _items(plan(["1.5.6", "2.0.0", "1.10.0"], avail, {}, now=NOW))

        assert [(i.version, i.extension) for i in items] == [("2.0.0", "aws"), ("2.0.0", "httpfs"), ("1.10.0", "fts"), ("1.5.6", "ui")]

    def test_platforms_in_an_item_are_sorted(self):
        avail = {"2.0.0": {("httpfs", "windows-amd64"), ("httpfs", "linux-amd64"), ("httpfs", "osx-arm64")}}

        items = _items(plan(["2.0.0"], avail, {}, now=NOW))

        assert items[0].platforms == ["linux-amd64", "osx-arm64", "windows-amd64"]

    def test_version_without_availability_entry_plans_nothing(self):
        assert _empty(plan(["2.0.0"], {}, {}, now=NOW))


class TestStableVersions:
    RELEASES = [
        {"tag_name": "v2.1.0", "draft": False, "prerelease": True},
        {"tag_name": "v2.0.1", "draft": True, "prerelease": False},
        {"tag_name": "v2.0.0", "draft": False, "prerelease": False},
        {"tag_name": "v1.5.6", "draft": False, "prerelease": False},
        {"tag_name": "v1.10.0", "draft": False, "prerelease": False},
        {"tag_name": "v1.4.5", "draft": False, "prerelease": False},
        {"tag_name": "nightly", "draft": False, "prerelease": False},
    ]

    def test_filters_and_sorts_numerically(self):
        fetch = lambda url, accept, token=None: self.RELEASES
        assert stable_versions("1.5.0", fetch=fetch) == ["1.5.6", "1.10.0", "2.0.0"]

    def test_floor_is_inclusive(self):
        fetch = lambda url, accept, token=None: self.RELEASES
        assert stable_versions("1.4.5", fetch=fetch) == ["1.4.5", "1.5.6", "1.10.0", "2.0.0"]


class TestStallCap:
    AVAIL = {"2.0.0": {("motherduck", "linux-amd64"), ("tpch", "linux-amd64")}}

    def _staged(self, n):
        return {"motherduck": {f"2.0.0.dev{i}": PublishedVersion(files=set(), first_upload=FRESH) for i in range(n)}}

    def test_item_staged_too_often_is_stalled_not_planned(self):
        items, late, stalled = _three(plan(["2.0.0"], self.AVAIL, {}, now=NOW, staged=self._staged(5)))

        assert items == [WorkItem("2.0.0", "tpch", ["linux-amd64"])]
        assert stalled == [Stalled("2.0.0", "motherduck", 5)]
        assert late == []

    def test_below_the_cap_is_planned(self):
        items, stalled = _is(plan(["2.0.0"], self.AVAIL, {}, now=NOW, staged=self._staged(4)))

        assert [i.extension for i in items] == ["motherduck", "tpch"]
        assert stalled == []

    def test_only_this_versions_staging_counts(self):
        staged = {"motherduck": {f"1.5.6.dev{i}": PublishedVersion(files=set(), first_upload=FRESH) for i in range(9)}}
        items, stalled = _is(plan(["2.0.0"], self.AVAIL, {}, now=NOW, staged=staged))

        assert [i.extension for i in items] == ["motherduck", "tpch"]
        assert stalled == []

    def test_no_staging_state_disables_the_cap(self):
        items, stalled = _is(plan(["2.0.0"], self.AVAIL, {}, now=NOW, staged=None))

        assert len(items) == 2 and stalled == []

    def test_a_promoted_item_is_never_stalled(self):
        pub = {"motherduck": {"2.0.0": PublishedVersion(files={_wheel("motherduck", "2.0.0", "manylinux_2_17_x86_64.manylinux2014_x86_64")}, first_upload=FRESH)}}
        items, stalled = _is(plan(["2.0.0"], self.AVAIL, pub, now=NOW, staged=self._staged(9)))

        assert [i.extension for i in items] == ["tpch"]
        assert stalled == []


class TestDependencies:
    AVAIL = {"2.0.0": {("iceberg", "linux-amd64"), ("iceberg", "osx-arm64"), ("avro", "linux-amd64"), ("avro", "osx-arm64")}}

    def test_dependent_waits_until_the_dependency_is_released(self):
        result = plan(["2.0.0"], self.AVAIL, {}, now=NOW)

        assert result.items == [WorkItem("2.0.0", "avro", ["linux-amd64", "osx-arm64"])]
        assert result.waiting == [Waiting("2.0.0", "iceberg", "avro")]

    def test_dependent_is_planned_once_the_dependency_wheels_exist_for_its_platforms(self):
        pub = {"avro": {"2.0.0": PublishedVersion(files={_wheel("avro", "2.0.0", "manylinux_2_17_x86_64.manylinux2014_x86_64"), _wheel("avro", "2.0.0", "macosx_12_0_arm64")}, first_upload=FRESH)}}

        result = plan(["2.0.0"], self.AVAIL, pub, now=NOW)

        assert result.items == [WorkItem("2.0.0", "iceberg", ["linux-amd64", "osx-arm64"])]
        assert result.waiting == []

    def test_dependency_missing_on_one_platform_keeps_the_dependent_waiting(self):
        pub = {"avro": {"2.0.0": PublishedVersion(files={_wheel("avro", "2.0.0", "macosx_12_0_arm64")}, first_upload=FRESH)}}

        result = plan(["2.0.0"], self.AVAIL, pub, now=NOW)

        assert [i.extension for i in result.items] == ["avro"]
        assert result.waiting == [Waiting("2.0.0", "iceberg", "avro")]

    def test_requires_names_only_known_extensions(self):
        from duckdb_extensions.registry import EXTENSIONS, REQUIRES

        for name, deps in REQUIRES.items():
            assert name in EXTENSIONS
            for dep in deps:
                assert dep in EXTENSIONS and dep != name


class TestStallCount:
    def test_staging_before_the_first_release_does_not_count(self):
        from duckdb_extensions.plan import stage_attempts

        staged = {
            "ui": {
                "2.0.0.dev1": PublishedVersion(files=set(), first_upload=NOW - timedelta(days=5)),
                "2.0.0.dev2": PublishedVersion(files=set(), first_upload=NOW - timedelta(days=4)),
                "2.0.0.dev3": PublishedVersion(files=set(), first_upload=NOW - timedelta(days=1)),
            }
        }
        assert stage_attempts(staged, "ui", "2.0.0") == 3
        assert stage_attempts(staged, "ui", "2.0.0", since=NOW - timedelta(days=3)) == 1

    def test_a_platform_added_after_a_release_gets_fresh_attempts(self):
        avail = {"2.0.0": {("ui", "linux-amd64"), ("ui", "windows-arm64")}}
        released = NOW - timedelta(days=3)
        pub = {"ui": {"2.0.0": PublishedVersion(files={_wheel("ui", "2.0.0", "manylinux_2_17_x86_64.manylinux2014_x86_64")}, first_upload=released)}}
        staged = {"ui": {f"2.0.0.dev{i}": PublishedVersion(files=set(), first_upload=released - timedelta(days=1)) for i in range(7)}}

        result = plan(["2.0.0"], avail, pub, now=NOW, staged=staged)

        assert result.items == [WorkItem("2.0.0", "ui", ["windows-arm64"])]
        assert result.stalled == []
