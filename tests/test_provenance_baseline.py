"""Tests for scripts/provenance_baseline.py on synthetic wheels and a fake server."""

import gzip
import hashlib
import json
import os
import sys
import zipfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))
import build_extension_wheels
import provenance_baseline
from extension_binaries import synthetic_binary

TAG = "manylinux_2_17_x86_64.manylinux2014_x86_64"
WHEEL = f"duckdb_core_ext_httpfs-2.0.0-py3-none-{TAG}.whl"
URL = "https://extensions.duckdb.org/v2.0.0/linux_amd64/httpfs.duckdb_extension.gz"
HEADERS = {"etag": '"e1"', "last_modified": "Tue"}


def _binary(extension_version="77392a7207", salt=b""):
    return salt + synthetic_binary("httpfs", "linux_amd64", "v2.0.0", extension_version=extension_version)


def _old_wheel(tmp_path, data):
    """A wheel as published before provenance existed: the pipeline's layout without the section."""
    path = build_extension_wheels.write_wheel("httpfs", "linux-amd64", "2.0.0", data, str(tmp_path), ext=None)
    with zipfile.ZipFile(path) as zf, zipfile.ZipFile(str(tmp_path / "old.whl"), "w") as out:
        for info in zf.infolist():
            content = zf.read(info.filename)
            if info.filename.endswith("METADATA"):
                content = content.decode().split("\n\n## Provenance\n")[0].encode() + b"\n"
            out.writestr(info, content)
    return (tmp_path / "old.whl").read_bytes()


class TestBaselineEntry:
    def test_same_binary_keeps_the_servers_headers(self):
        data = _binary()
        entry, verdict = provenance_baseline.baseline_entry(data, (gzip.compress(data), HEADERS), URL)
        assert verdict == "same"
        assert (entry.extension_version, entry.sha256, entry.etag, entry.last_modified, entry.source) == ("77392a7207", hashlib.sha256(data).hexdigest(), '"e1"', "Tue", URL)

    def test_a_differing_server_binary_records_the_wheel_without_headers(self):
        data = _binary()
        entry, verdict = provenance_baseline.baseline_entry(data, (gzip.compress(_binary("deadbeef00")), HEADERS), URL)
        assert verdict == "replaced: wheel 77392a7207, server deadbeef00"
        assert entry.sha256 == hashlib.sha256(data).hexdigest() and entry.etag == "" and entry.last_modified == ""

    def test_rebuilt_is_the_same_commit_with_other_bytes(self):
        _, verdict = provenance_baseline.baseline_entry(_binary(), (gzip.compress(_binary(salt=b"x")), HEADERS), URL)
        assert verdict == "rebuilt: wheel 77392a7207, server 77392a7207"

    def test_withdrawn(self):
        entry, verdict = provenance_baseline.baseline_entry(_binary(), (None, None), URL)
        assert verdict == "withdrawn" and entry.etag == ""


class TestBuild:
    def _page(self, core_metadata):
        return {"files": [{"filename": WHEEL, "url": "https://f/" + WHEEL, "core-metadata": core_metadata, "upload-time": "2026-10-21T10:00:00Z"}]}

    def test_old_wheel_gets_an_entry_and_the_file_is_written_as_it_goes(self, tmp_path):
        data = _binary()
        wheel = _old_wheel(tmp_path, data)
        out = tmp_path / "baseline.json"
        with patch.object(provenance_baseline, "stable_versions", return_value=["2.0.0"]):
            baseline, differences = provenance_baseline.build("1.5.0", "https://pypi.org", str(out), extensions=["httpfs"],
                                                               fetch_json=lambda url, accept: self._page(True), fetch_text=lambda url: "Metadata-Version: 2.1\n\nold\n",
                                                               fetch_bytes=lambda url: wheel, fetch_file=lambda url: (gzip.compress(data), HEADERS))
        assert differences == []
        assert baseline == {"httpfs 2.0.0 linux-amd64": {"extension_version": "77392a7207", "sha256": hashlib.sha256(data).hexdigest(), "etag": '"e1"', "last_modified": "Tue", "source": URL}}
        assert json.loads(out.read_text()) == baseline

    def test_a_wheel_that_records_provenance_is_skipped(self, tmp_path, capsys):
        text = "Metadata-Version: 2.1\n\n## Provenance\n\n- extension_version: a\n- sha256: b\n"
        with patch.object(provenance_baseline, "stable_versions", return_value=["2.0.0"]):
            baseline, _ = provenance_baseline.build("1.5.0", "https://pypi.org", str(tmp_path / "b.json"), extensions=["httpfs"],
                                                    fetch_json=lambda url, accept: self._page(True), fetch_text=lambda url: text,
                                                    fetch_bytes=lambda url: (_ for _ in ()).throw(AssertionError("not downloaded")), fetch_file=None)
        assert baseline == {} and "records its provenance" in capsys.readouterr().err

    def test_existing_entries_are_kept_and_not_redone(self, tmp_path):
        out = tmp_path / "b.json"
        out.write_text(json.dumps({"httpfs 2.0.0 linux-amd64": {"extension_version": "x", "sha256": "y"}}))
        with patch.object(provenance_baseline, "stable_versions", return_value=["2.0.0"]):
            baseline, _ = provenance_baseline.build("1.5.0", "https://pypi.org", str(out), extensions=["httpfs"],
                                                    fetch_json=lambda url, accept: self._page(False), fetch_text=None,
                                                    fetch_bytes=lambda url: (_ for _ in ()).throw(AssertionError("not downloaded")), fetch_file=None)
        assert baseline == {"httpfs 2.0.0 linux-amd64": {"extension_version": "x", "sha256": "y"}}

    def test_the_output_directory_is_created_and_a_missing_project_is_skipped(self, tmp_path):
        import io
        import urllib.error

        def page(url, accept):
            if "httpfs" in url:
                raise urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO())
            return self._page(False)

        out = tmp_path / "data" / "baseline.json"
        with patch.object(provenance_baseline, "stable_versions", return_value=["2.0.0"]):
            baseline, _ = provenance_baseline.build("1.5.0", "https://pypi.org", str(out), extensions=["httpfs", "avro"], fetch_json=page, fetch_text=None,
                                                    fetch_bytes=lambda url: (_ for _ in ()).throw(AssertionError("not downloaded")), fetch_file=None)
        assert baseline == {} and out.parent.is_dir()

    def test_a_difference_is_reported(self, tmp_path, capsys):
        wheel = _old_wheel(tmp_path, _binary())
        with patch.object(provenance_baseline, "stable_versions", return_value=["2.0.0"]):
            baseline, differences = provenance_baseline.build("1.5.0", "https://pypi.org", str(tmp_path / "b.json"), extensions=["httpfs"],
                                                               fetch_json=lambda url, accept: self._page(False), fetch_text=None,
                                                               fetch_bytes=lambda url: wheel, fetch_file=lambda url: (gzip.compress(_binary("deadbeef00")), HEADERS))
        assert differences == ["httpfs 2.0.0 linux-amd64: replaced: wheel 77392a7207, server deadbeef00"]
        assert baseline["httpfs 2.0.0 linux-amd64"]["etag"] == ""
