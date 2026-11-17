"""Tests for duckdb_extensions.replaced: the record a wheel carries, the comparison with the server, and the
post release items a change calls for."""

import gzip
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from duckdb_extensions.plan import PublishedVersion
from duckdb_extensions.replaced import (
    ERROR,
    HEADERS_ONLY,
    REBUILT,
    REPLACED,
    UNCHANGED,
    UNKNOWN,
    WITHDRAWN,
    Finding,
    Provenance,
    compare,
    detect,
    latest_release,
    metadata_url,
    pins_exactly,
    post_items,
    post_number,
    post_version,
    project_page,
    recorded,
)
from extension_binaries import synthetic_binary

URL = "https://extensions.duckdb.org/v2.0.0/linux_amd64/httpfs.duckdb_extension.gz"
WHEEL = "duckdb_core_ext_httpfs-2.0.0-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
HEADERS = {"etag": '"e1"', "last_modified": "Tue, 20 Oct 2026 12:09:39 GMT"}
OTHER_HEADERS = {"etag": '"e2"', "last_modified": "Wed, 21 Oct 2026 08:00:00 GMT"}


def _binary(extension_version="77392a7207", salt=b""):
    return salt + synthetic_binary("httpfs", "linux_amd64", "v2.0.0", extension_version=extension_version)


def _record(data, headers=HEADERS):
    return Provenance.of_binary(data, headers["etag"], headers["last_modified"], URL)


def _metadata(provenance):
    lines = "\n".join(f"- {k}: {v}" for k, v in provenance.items())
    return f"Metadata-Version: 2.1\nName: duckdb-core-ext-httpfs\nVersion: 2.0.0\n\n# intro\n\n## Provenance\n\n{lines}\n"


class TestProvenance:
    def test_of_binary_reads_footer_and_hash(self):
        data = _binary()
        p = Provenance.of_binary(data, '"e1"', "Tue", URL)
        assert p == Provenance("77392a7207", hashlib.sha256(data).hexdigest(), '"e1"', "Tue", URL)

    def test_from_lines_needs_the_binary_fields(self):
        assert Provenance.from_lines({"extension_version": "a", "sha256": "b"}) == Provenance("a", "b")
        assert Provenance.from_lines({"extension_version": "a"}) is None
        assert Provenance.from_lines(None) is None

    def test_missing_headers_read_as_empty(self):
        p = Provenance.of_binary(_binary(), None, None)
        assert p.etag == "" and p.last_modified == ""


class TestRecorded:
    PAGE = {"files": [{"filename": WHEEL, "url": "https://files.pythonhosted.org/x/" + WHEEL, "core-metadata": {"sha256": "aa"}}]}

    def test_metadata_url_only_when_the_index_serves_it(self):
        assert metadata_url(self.PAGE, WHEEL) == "https://files.pythonhosted.org/x/" + WHEEL + ".metadata"
        assert metadata_url({"files": [{"filename": WHEEL, "url": "u", "core-metadata": False}]}, WHEEL) is None
        assert metadata_url(self.PAGE, "other.whl") is None

    def test_from_the_wheel_metadata(self):
        record = _record(_binary())
        text = _metadata({"extension_version": record.extension_version, "sha256": record.sha256, "etag": record.etag, "last_modified": record.last_modified, "source": URL})
        got = recorded("httpfs", "2.0.0", "linux-amd64", self.PAGE, baseline={"httpfs 2.0.0 linux-amd64": {"extension_version": "old", "sha256": "old"}}, fetch_text=lambda url: text)
        assert got == record

    def test_falls_back_to_the_baseline_when_the_wheel_records_nothing(self):
        text = "Metadata-Version: 2.1\nName: duckdb-core-ext-httpfs\nVersion: 2.0.0\n\nAn old description.\n"
        baseline = {"httpfs 2.0.0 linux-amd64": {"extension_version": "abc", "sha256": "def", "etag": '"e1"', "last_modified": "Tue"}}
        got = recorded("httpfs", "2.0.0", "linux-amd64", self.PAGE, baseline=baseline, fetch_text=lambda url: text)
        assert got == Provenance("abc", "def", '"e1"', "Tue")

    def test_the_baseline_never_stands_in_for_a_post_release(self):
        post = WHEEL.replace("-2.0.0-", "-2.0.0.post1-")
        page = {"files": [{"filename": post, "url": "https://f/" + post, "core-metadata": True}]}
        baseline = {"httpfs 2.0.0 linux-amd64": {"extension_version": "abc", "sha256": "def"}}
        assert recorded("httpfs", "2.0.0", "linux-amd64", page, baseline=baseline, fetch_text=lambda url: "Name: x\n\nold\n", wheel_version="2.0.0.post1") is None

    def test_none_when_neither_has_it(self):
        assert recorded("httpfs", "2.0.0", "linux-amd64", {"files": []}, baseline={}, fetch_text=lambda url: None) is None

    def test_a_missing_project_is_an_empty_page(self):
        import io
        import urllib.error

        def missing(url, accept):
            raise urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO())

        assert project_page("httpfs", "https://pypi.org", missing) == {"files": []}

        def broken(url, accept):
            raise urllib.error.HTTPError(url, 500, "err", {}, io.BytesIO())

        with pytest.raises(urllib.error.HTTPError):
            project_page("httpfs", "https://pypi.org", broken)


class TestLatestRelease:
    def test_a_post_release_with_the_platform_wins_over_the_base(self):
        state = {**_state("2.0.0", ["linux-amd64", "linux-arm64"]), **_state("2.0.0.post1", ["linux-amd64"]), **_state("2.0.0.post2", ["linux-amd64"])}
        assert latest_release(state, "2.0.0", "linux-amd64", "httpfs") == "2.0.0.post2"
        assert latest_release(state, "2.0.0", "linux-arm64", "httpfs") == "2.0.0"
        assert latest_release(state, "2.0.0", "osx-arm64", "httpfs") is None

    def test_staging_versions_and_other_versions_do_not_count(self):
        state = {**_state("2.0.0", ["linux-amd64"]), **_state("2.0.0.post1.dev44", ["linux-amd64"]), **_state("2.0.1", ["linux-amd64"])}
        assert latest_release(state, "2.0.0", "linux-amd64", "httpfs") == "2.0.0"

    def test_a_longer_version_with_the_same_prefix_is_another_version(self):
        state = {**_state("2.0.1", ["linux-amd64"]), **_state("2.0.10.post1", ["linux-amd64"])}
        assert latest_release(state, "2.0.1", "linux-amd64", "httpfs") == "2.0.1"
        assert post_number("2.0.10.post1", "2.0.1") is None and post_number("2.0.1.post3", "2.0.1") == 3 and post_number("2.0.1", "2.0.1") == 0


class TestPinsExactly:
    POST = WHEEL.replace("duckdb_core_ext_httpfs", "duckdb_core_ext_iceberg").replace("-2.0.0-", "-2.0.0.post1-")
    BASE = WHEEL.replace("duckdb_core_ext_httpfs", "duckdb_core_ext_iceberg")

    def _page(self, *names):
        return {"files": [{"filename": n, "url": "https://f/" + n, "core-metadata": True} for n in names]}

    def test_an_exact_pin_in_the_newest_release(self):
        state = {**_state("2.0.0", ["linux-amd64"], extension="iceberg"), **_state("2.0.0.post1", ["linux-amd64"], extension="iceberg")}
        texts = {"https://f/" + self.POST + ".metadata": "Name: duckdb-core-ext-iceberg\nRequires-Dist: duckdb-core-ext-avro==2.0.0\n\nd\n"}
        assert pins_exactly(self._page(self.BASE, self.POST), state, "2.0.0", "avro", fetch_text=lambda url: texts[url]) is True

    def test_a_wildcard_pin_needs_no_post_release(self):
        state = _state("2.0.0", ["linux-amd64"], extension="iceberg")
        text = "Name: duckdb-core-ext-iceberg\nRequires-Dist: duckdb-core-ext-avro==2.0.0.*\n\nd\n"
        assert pins_exactly(self._page(self.BASE), state, "2.0.0", "avro", fetch_text=lambda url: text) is False

    @pytest.mark.parametrize("line", [
        "Requires-Dist: duckdb-core-ext-avro==2.0.0; python_version >= \"3.9\"",
        "Requires-Dist: duckdb-core-ext-avro (==2.0.0)",
        "Requires-Dist: duckdb-core-ext-avro == 2.0.0",
        "Requires-Dist: duckdb_core_ext_avro==2.0.0",
        "Requires-Dist: DuckDB-Core-Ext-Avro==2.0.0",
    ])
    def test_every_spelling_of_an_exact_pin_counts(self, line):
        state = _state("2.0.0", ["linux-amd64"], extension="iceberg")
        assert pins_exactly(self._page(self.BASE), state, "2.0.0", "avro", fetch_text=lambda url: f"Name: x\n{line}\n\nd\n") is True

    def test_a_pin_in_the_description_does_not_count(self):
        state = _state("2.0.0", ["linux-amd64"], extension="iceberg")
        assert pins_exactly(self._page(self.BASE), state, "2.0.0", "avro", fetch_text=lambda url: "Name: x\n\nRequires-Dist: duckdb-core-ext-avro==2.0.0\n") is False

    def test_unreadable_metadata_counts_as_exact_and_no_release_as_nothing(self):
        state = _state("2.0.0", ["linux-amd64"], extension="iceberg")
        assert pins_exactly({"files": [{"filename": self.BASE, "url": "u", "core-metadata": False}]}, state, "2.0.0", "avro", fetch_text=lambda url: None) is True
        assert pins_exactly(self._page(self.BASE), {}, "2.0.0", "avro", fetch_text=lambda url: None) is False


class TestCompare:
    def _head(self, status, headers):
        def head(url):
            self.headed = url
            return status, headers
        return head

    def _get(self, data, headers):
        def get(url):
            self.downloaded = url
            return (None, None) if data is None else (gzip.compress(data), headers)
        return get

    def test_unknown_without_a_record_asks_the_server_nothing(self):
        self.headed = None
        f = compare("httpfs", "2.0.0", "linux-amd64", None, head=self._head(200, HEADERS), get=self._get(_binary(), HEADERS))
        assert f == Finding("2.0.0", "httpfs", "linux-amd64", UNKNOWN)
        assert self.headed is None

    def test_unchanged_headers_download_nothing(self):
        self.downloaded = None
        record = _record(_binary())
        f = compare("httpfs", "2.0.0", "linux-amd64", record, head=self._head(200, dict(HEADERS)), get=self._get(_binary(), HEADERS))
        assert f.outcome == UNCHANGED and f.recorded == record and f.current is None
        assert self.downloaded is None
        assert self.headed == URL

    def test_a_different_last_modified_with_the_same_etag_is_the_same_file(self):
        # The origin answers a HEAD with another Last-Modified than the cache gives on a GET of the same file.
        self.downloaded = None
        record = _record(_binary())
        f = compare("httpfs", "2.0.0", "linux-amd64", record, head=self._head(200, {"etag": HEADERS["etag"], "last_modified": "Wed, 21 Oct 2026 14:20:20 GMT"}), get=self._get(_binary(), HEADERS))
        assert f.outcome == UNCHANGED and self.downloaded is None

    def test_no_etag_on_either_side_means_a_download(self):
        data = _binary()
        f = compare("httpfs", "2.0.0", "linux-amd64", _record(data), head=self._head(200, {"etag": None, "last_modified": HEADERS["last_modified"]}), get=self._get(data, HEADERS))
        assert f.outcome == HEADERS_ONLY and self.downloaded == URL

    def test_headers_only_when_the_binary_is_the_same(self):
        data = _binary()
        f = compare("httpfs", "2.0.0", "linux-amd64", _record(data), head=self._head(200, OTHER_HEADERS), get=self._get(data, OTHER_HEADERS))
        assert f.outcome == HEADERS_ONLY
        assert f.current == _record(data, OTHER_HEADERS)
        assert self.downloaded == URL

    def test_rebuilt_when_the_commit_is_the_same_and_the_bytes_differ(self):
        f = compare("httpfs", "2.0.0", "linux-amd64", _record(_binary()), head=self._head(200, OTHER_HEADERS), get=self._get(_binary(salt=b"x"), OTHER_HEADERS))
        assert f.outcome == REBUILT
        assert f.current.extension_version == f.recorded.extension_version and f.current.sha256 != f.recorded.sha256

    def test_replaced_when_the_commit_differs(self):
        f = compare("httpfs", "2.0.0", "linux-amd64", _record(_binary()), head=self._head(200, OTHER_HEADERS), get=self._get(_binary("deadbeef00"), OTHER_HEADERS))
        assert f.outcome == REPLACED
        assert (f.recorded.extension_version, f.current.extension_version) == ("77392a7207", "deadbeef00")

    def test_withdrawn_when_the_server_lost_the_file(self):
        f = compare("httpfs", "2.0.0", "linux-amd64", _record(_binary()), head=self._head(404, {}), get=self._get(None, None))
        assert f.outcome == WITHDRAWN and f.current is None
        f = compare("httpfs", "2.0.0", "linux-amd64", _record(_binary()), head=self._head(200, OTHER_HEADERS), get=self._get(None, None))
        assert f.outcome == WITHDRAWN

    def test_other_server_errors_raise(self):
        with pytest.raises(RuntimeError, match="HEAD .* returned 403"):
            compare("httpfs", "2.0.0", "linux-amd64", _record(_binary()), head=self._head(403, {}), get=self._get(None, None))

    def test_a_record_without_headers_always_downloads(self):
        data = _binary()
        record = Provenance.from_lines({"extension_version": "77392a7207", "sha256": hashlib.sha256(data).hexdigest()})
        f = compare("httpfs", "2.0.0", "linux-amd64", record, head=self._head(200, HEADERS), get=self._get(data, HEADERS))
        assert f.outcome == HEADERS_ONLY and self.downloaded == URL

    def test_two_missing_etags_are_not_the_same_file(self):
        # The baseline writes a record without headers for a wheel that already differs from the server, so
        # that the difference is downloaded and reported every night.
        record = Provenance.from_lines({"extension_version": "77392a7207", "sha256": hashlib.sha256(_binary()).hexdigest()})
        f = compare("httpfs", "2.0.0", "linux-amd64", record, head=self._head(200, {"etag": None, "last_modified": None}), get=self._get(_binary("deadbeef00"), {"etag": None, "last_modified": None}))
        assert f.outcome == REPLACED and self.downloaded == URL

    def test_json_shape(self):
        f = compare("httpfs", "2.0.0", "linux-amd64", _record(_binary()), head=self._head(200, OTHER_HEADERS), get=self._get(_binary("deadbeef00"), OTHER_HEADERS), wheel_version="2.0.0.post1")
        j = f.to_json()
        assert j["outcome"] == REPLACED and j["recorded"]["extension_version"] == "77392a7207" and j["current"]["etag"] == '"e2"'
        assert j["wheel_version"] == "2.0.0.post1" and j["detail"] == ""
        assert set(j) == {"version", "extension", "platform", "outcome", "recorded", "current", "wheel_version", "detail"}
        assert Finding("2.0.0", "httpfs", "linux-amd64", UNKNOWN).to_json()["wheel_version"] == "2.0.0"


def _state(version, platforms, extra_versions=(), extension="httpfs"):
    tag = {"linux-amd64": "manylinux_2_17_x86_64.manylinux2014_x86_64", "linux-arm64": "manylinux_2_17_aarch64.manylinux2014_aarch64", "osx-arm64": "macosx_12_0_arm64"}
    files = {f"duckdb_core_ext_{extension}-{version}-py3-none-{tag[p]}.whl" for p in platforms}
    state = {version: PublishedVersion(files=files, first_upload=None)}
    for v in extra_versions:
        state[v] = PublishedVersion(files=set(), first_upload=None)
    return state


class TestPostItems:
    def test_first_post_release(self):
        assert post_version(_state("2.0.0", ["linux-amd64"]), "2.0.0") == "2.0.0.post1"

    def test_next_free_post_release(self):
        assert post_version(_state("2.0.0", ["linux-amd64"], extra_versions=["2.0.0.post1", "2.0.0.post3", "2.0.0.post1.dev44"]), "2.0.0") == "2.0.0.post4"

    def test_another_version_does_not_count(self):
        assert post_version(_state("2.0.0", ["linux-amd64"], extra_versions=["1.5.6.post2"]), "2.0.0") == "2.0.0.post1"

    def test_items_cover_every_published_platform_of_the_extension(self):
        findings = [
            Finding("2.0.0", "httpfs", "linux-arm64", REPLACED),
            Finding("2.0.0", "httpfs", "osx-arm64", UNCHANGED),
            Finding("2.0.0", "httpfs", "linux-amd64", HEADERS_ONLY),
        ]
        items = post_items(findings, {"httpfs": _state("2.0.0", ["linux-amd64", "linux-arm64", "osx-arm64"])})
        assert items == [{"version": "2.0.0", "extension": "httpfs", "platforms": ["linux-amd64", "linux-arm64", "osx-arm64"], "wheel_version": "2.0.0.post1", "changed": ["linux-arm64"]}]

    def test_one_item_per_extension_and_version_newest_first(self):
        findings = [
            Finding("1.5.6", "httpfs", "linux-amd64", REBUILT),
            Finding("2.0.0", "httpfs", "linux-amd64", REPLACED),
            Finding("2.0.0", "httpfs", "linux-arm64", REPLACED),
        ]
        items = post_items(findings, {"httpfs": {**_state("2.0.0", ["linux-amd64", "linux-arm64"]), **_state("1.5.6", ["linux-amd64"])}})
        assert [(i["version"], i["wheel_version"], i["changed"]) for i in items] == [("2.0.0", "2.0.0.post1", ["linux-amd64", "linux-arm64"]), ("1.5.6", "1.5.6.post1", ["linux-amd64"])]

    def test_nothing_to_do(self):
        assert post_items([Finding("2.0.0", "httpfs", "linux-amd64", UNCHANGED), Finding("2.0.0", "httpfs", "linux-arm64", WITHDRAWN)], {}) == []

    def test_a_withdrawn_platform_is_left_out_of_the_item(self):
        findings = [Finding("2.0.0", "httpfs", "linux-amd64", REPLACED), Finding("2.0.0", "httpfs", "linux-arm64", WITHDRAWN), Finding("2.0.0", "httpfs", "osx-arm64", UNCHANGED)]
        items = post_items(findings, {"httpfs": _state("2.0.0", ["linux-amd64", "linux-arm64", "osx-arm64"])})
        assert items[0]["platforms"] == ["linux-amd64", "osx-arm64"]

    def test_the_item_counts_platforms_of_the_newest_release(self):
        findings = [Finding("2.0.0", "httpfs", "linux-amd64", REPLACED, wheel_version="2.0.0.post1")]
        state = {**_state("2.0.0", ["linux-amd64", "linux-arm64"]), **_state("2.0.0.post1", ["linux-amd64", "linux-arm64", "osx-arm64"])}
        items = post_items(findings, {"httpfs": state})
        assert items == [{"version": "2.0.0", "extension": "httpfs", "platforms": ["linux-amd64", "linux-arm64", "osx-arm64"], "wheel_version": "2.0.0.post2", "changed": ["linux-amd64"]}]

    def test_an_extension_that_requires_a_changed_one_gets_a_post_release_too(self):
        findings = [Finding("2.0.0", "avro", "linux-amd64", REBUILT)]
        state = {"avro": _state("2.0.0", ["linux-amd64", "linux-arm64"], extension="avro"), "iceberg": _state("2.0.0", ["linux-amd64"], extension="iceberg")}
        items = post_items(findings, state, follow={("2.0.0", "iceberg", "avro"): True})
        assert [(i["extension"], i["wheel_version"], i["platforms"], i["changed"]) for i in items] == [("avro", "2.0.0.post1", ["linux-amd64", "linux-arm64"], ["linux-amd64"]), ("iceberg", "2.0.0.post1", ["linux-amd64"], [])]

    def test_a_dependent_whose_wheel_already_accepts_post_releases_is_not_added(self):
        findings = [Finding("2.0.0", "avro", "linux-amd64", REBUILT)]
        state = {"avro": _state("2.0.0", ["linux-amd64"], extension="avro"), "iceberg": _state("2.0.0", ["linux-amd64"], extension="iceberg")}
        assert [i["extension"] for i in post_items(findings, state, follow={("2.0.0", "iceberg", "avro"): False})] == ["avro"]

    def test_a_dependent_follows_a_requirement_already_released_even_with_no_finding_tonight(self):
        state = {"avro": {**_state("2.0.0", ["linux-amd64"], extension="avro"), **_state("2.0.0.post1", ["linux-amd64"], extension="avro")}, "iceberg": _state("2.0.0", ["linux-amd64"], extension="iceberg")}
        items = post_items([Finding("2.0.0", "avro", "linux-amd64", UNCHANGED, wheel_version="2.0.0.post1")], state, follow={("2.0.0", "iceberg", "avro"): True})
        assert [(i["extension"], i["wheel_version"], i["changed"]) for i in items] == [("iceberg", "2.0.0.post1", [])]

    def test_a_dependent_without_a_wheel_for_that_version_is_not_added(self):
        findings = [Finding("1.5.6", "avro", "linux-amd64", REBUILT)]
        state = {"avro": _state("1.5.6", ["linux-amd64"], extension="avro"), "iceberg": _state("2.0.0", ["linux-amd64"], extension="iceberg")}
        assert [i["extension"] for i in post_items(findings, state)] == ["avro"]


class TestDetect:
    def _page(self, *filenames, upload="2026-10-21T10:00:00Z"):
        return {"files": [{"filename": f, "url": "https://f/" + f, "core-metadata": True, "upload-time": upload} for f in filenames]}

    def test_the_newest_post_release_is_the_record_compared(self):
        """After a post release carried the server's binary, the next night compares against that record and
        finds nothing, instead of asking for another post release."""
        data = _binary("deadbeef00")
        record = _record(data, OTHER_HEADERS)
        post = WHEEL.replace("-2.0.0-", "-2.0.0.post1-")
        texts = {"https://f/" + WHEEL + ".metadata": _metadata({"extension_version": "77392a7207", "sha256": "old", "etag": '"e1"'}),
                 "https://f/" + post + ".metadata": _metadata({"extension_version": record.extension_version, "sha256": record.sha256, "etag": record.etag})}
        asked = []
        findings, state, follow = detect(["2.0.0"], ["httpfs"], "https://pypi.org", fetch_json=lambda url, accept: self._page(WHEEL, post), fetch_text=lambda url: asked.append(url) or texts[url],
                                         head=lambda url: (200, OTHER_HEADERS), get=lambda url: (_ for _ in ()).throw(AssertionError("no download")))
        assert [(f.outcome, f.wheel_version) for f in findings] == [(UNCHANGED, "2.0.0.post1")]
        assert asked == ["https://f/" + post + ".metadata"]
        assert post_items(findings, state, follow) == []

    def test_each_project_page_is_fetched_once_and_dependents_are_included(self):
        avro = WHEEL.replace("httpfs", "avro")
        iceberg = WHEEL.replace("httpfs", "iceberg")
        pages = {"https://pypi.org/simple/duckdb-core-ext-avro/": self._page(avro), "https://pypi.org/simple/duckdb-core-ext-iceberg/": self._page(iceberg)}
        asked = []
        texts = {"https://f/" + avro + ".metadata": _metadata({"extension_version": "77392a7207", "sha256": "old", "etag": '"e1"'}),
                 "https://f/" + iceberg + ".metadata": "Name: duckdb-core-ext-iceberg\nRequires-Dist: duckdb-core-ext-avro==2.0.0\n\nd\n"}
        data = synthetic_binary("avro", "linux_amd64", "v2.0.0", extension_version="deadbeef00")
        findings, state, follow = detect(["2.0.0"], ["avro"], "https://pypi.org", fetch_json=lambda url, accept: asked.append(url) or pages[url], fetch_text=lambda url: texts[url],
                                         head=lambda url: (200, OTHER_HEADERS), get=lambda url: (gzip.compress(data), OTHER_HEADERS))
        assert sorted(asked) == sorted(pages)
        assert [(f.extension, f.outcome) for f in findings] == [("avro", REPLACED)]
        assert follow == {("2.0.0", "iceberg", "avro"): True}
        assert [(i["extension"], i["wheel_version"]) for i in post_items(findings, state, follow)] == [("avro", "2.0.0.post1"), ("iceberg", "2.0.0.post1")]

    def test_a_dependent_whose_wheel_pins_with_a_wildcard_is_not_followed(self):
        avro = WHEEL.replace("httpfs", "avro")
        iceberg = WHEEL.replace("httpfs", "iceberg")
        pages = {"https://pypi.org/simple/duckdb-core-ext-avro/": self._page(avro), "https://pypi.org/simple/duckdb-core-ext-iceberg/": self._page(iceberg)}
        texts = {"https://f/" + avro + ".metadata": _metadata({"extension_version": "77392a7207", "sha256": "old", "etag": '"e1"'}),
                 "https://f/" + iceberg + ".metadata": "Name: duckdb-core-ext-iceberg\nRequires-Dist: duckdb-core-ext-avro==2.0.0.*\n\nRequires-Dist: duckdb-core-ext-avro==2.0.0\n"}
        data = synthetic_binary("avro", "linux_amd64", "v2.0.0", extension_version="deadbeef00")
        findings, state, follow = detect(["2.0.0"], ["avro"], "https://pypi.org", fetch_json=lambda url, accept: pages[url], fetch_text=lambda url: texts[url],
                                         head=lambda url: (200, OTHER_HEADERS), get=lambda url: (gzip.compress(data), OTHER_HEADERS))
        assert follow == {("2.0.0", "iceberg", "avro"): False}
        assert [i["extension"] for i in post_items(findings, state, follow)] == ["avro"]

    def test_the_pin_is_read_once_the_requirement_is_a_post_release_and_a_failure_to_read_it_counts_as_exact(self):
        avro = WHEEL.replace("httpfs", "avro")
        avro_post = avro.replace("-2.0.0-", "-2.0.0.post1-")
        iceberg = WHEEL.replace("httpfs", "iceberg")
        pages = {"https://pypi.org/simple/duckdb-core-ext-avro/": self._page(avro, avro_post), "https://pypi.org/simple/duckdb-core-ext-iceberg/": self._page(iceberg)}

        def text(url):
            if "iceberg" in url:
                raise RuntimeError("GET returned 503")
            return _metadata({"extension_version": "deadbeef00", "sha256": "new", "etag": '"e2"'})

        findings, state, follow = detect(["2.0.0"], ["avro"], "https://pypi.org", fetch_json=lambda url, accept: pages[url], fetch_text=text, head=lambda url: (200, OTHER_HEADERS), get=None)
        assert [(f.extension, f.outcome) for f in findings] == [("avro", UNCHANGED), ("iceberg", ERROR)]
        assert follow == {("2.0.0", "iceberg", "avro"): True}
        assert [(i["extension"], i["wheel_version"]) for i in post_items(findings, state, follow)] == [("iceberg", "2.0.0.post1")]

    def test_no_pin_is_read_while_the_requirement_never_moved(self):
        avro = WHEEL.replace("httpfs", "avro")
        iceberg = WHEEL.replace("httpfs", "iceberg")
        pages = {"https://pypi.org/simple/duckdb-core-ext-avro/": self._page(avro), "https://pypi.org/simple/duckdb-core-ext-iceberg/": self._page(iceberg)}
        asked = []

        def text(url):
            asked.append(url)
            return _metadata({"extension_version": "77392a7207", "sha256": "x", "etag": '"e1"'})

        _, _, follow = detect(["2.0.0"], ["avro"], "https://pypi.org", fetch_json=lambda url, accept: pages[url], fetch_text=text, head=lambda url: (200, HEADERS), get=None)
        assert follow == {} and asked == ["https://f/" + avro + ".metadata"]

    def test_a_failing_project_page_is_a_finding_and_the_rest_goes_on(self):
        text = _metadata({"extension_version": "77392a7207", "sha256": hashlib.sha256(_binary()).hexdigest(), "etag": '"e1"'})

        def page(url, accept):
            if "iceberg" in url:
                raise RuntimeError("GET returned 503")
            return self._page(WHEEL.replace("httpfs", url.split("duckdb-core-ext-")[1].rstrip("/")))

        findings, state, _ = detect(["2.0.0"], ["avro", "httpfs"], "https://pypi.org", fetch_json=page, fetch_text=lambda url: text, head=lambda url: (200, HEADERS), get=None)
        assert [(f.extension, f.outcome, f.detail) for f in findings if f.outcome == ERROR] == [("iceberg", ERROR, "RuntimeError: GET returned 503")]
        assert sorted(f.extension for f in findings if f.outcome == UNCHANGED) == ["avro", "httpfs"]
        assert "iceberg" not in state

    def test_a_failing_request_is_a_finding_and_the_rest_goes_on(self, capsys):
        arm = WHEEL.replace("x86_64.manylinux2014_x86_64", "aarch64.manylinux2014_aarch64")
        text = _metadata({"extension_version": "77392a7207", "sha256": hashlib.sha256(_binary()).hexdigest(), "etag": '"e1"'})

        def head(url):
            if "linux_amd64" in url:
                raise TimeoutError("timed out")
            return 200, HEADERS

        findings, _, _ = detect(["2.0.0"], ["httpfs"], "https://pypi.org", fetch_json=lambda url, accept: self._page(WHEEL, arm), fetch_text=lambda url: text, head=head, get=None, log=sys.stderr)
        assert [(f.platform, f.outcome) for f in findings] == [("linux-amd64", ERROR), ("linux-arm64", UNCHANGED)]
        assert findings[0].detail == "TimeoutError: timed out"
        assert "error httpfs 2.0.0 linux-amd64 TimeoutError: timed out" in capsys.readouterr().err

    def test_a_missing_project_yields_no_findings(self):
        import io
        import urllib.error

        def missing(url, accept):
            raise urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO())

        findings, state, _ = detect(["2.0.0"], ["httpfs"], "https://pypi.org", fetch_json=missing, fetch_text=None, head=None, get=None)
        assert findings == [] and state == {"httpfs": {}}

    def test_only_published_wheels_are_compared(self, capsys):
        data = _binary()
        record = _record(data)
        text = _metadata({"extension_version": record.extension_version, "sha256": record.sha256, "etag": record.etag, "last_modified": record.last_modified})
        page = {"files": [{"filename": WHEEL, "url": "https://f/" + WHEEL, "core-metadata": True, "upload-time": "2026-10-21T10:00:00Z"}]}
        heads = []

        def head(url):
            heads.append(url)
            return 200, OTHER_HEADERS

        findings, state, _ = detect(["2.0.0", "1.5.6"], ["httpfs"], "https://pypi.org", fetch_json=lambda url, accept: page, fetch_text=lambda url: text,
                                    head=head, get=lambda url: (gzip.compress(_binary("deadbeef00")), OTHER_HEADERS), log=sys.stderr)
        assert [(f.version, f.platform, f.outcome) for f in findings] == [("2.0.0", "linux-amd64", REPLACED)]
        assert heads == [URL]
        assert "2.0.0" in state["httpfs"]
        assert "replaced httpfs 2.0.0 linux-amd64" in capsys.readouterr().err
