"""Tests for duckdb_extension_wheel.check: CLI download and conformance check."""

import io
import os
import subprocess
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
import duckdb_extension_wheel as tool
import duckdb_extension_wheel.check as check
from duckdb_extension_wheel.cli import _read_description, main
from extension_binaries import host_server_platform, synthetic_binary

on_a_known_host = pytest.mark.skipif(sys.platform == "win32" or host_server_platform() is None, reason="needs a DuckDB platform and a shebang-capable host")


def test_cli_asset_urls_newest_layout_first_with_musl():
    assert check.cli_asset_urls("2.0.0", "linux-amd64-musl") == [
        "https://github.com/duckdb/duckdb/releases/download/v2.0.0/duckdb-cli-linux-amd64-musl.tar.gz",
        "https://github.com/duckdb/duckdb/releases/download/v2.0.0/duckdb_cli-linux-amd64-musl.zip",
    ]
    assert check.cli_asset_urls("1.5.6", "windows-arm64") == [
        "https://github.com/duckdb/duckdb/releases/download/v1.5.6/duckdb-cli-windows-arm64.tar.gz",
        "https://github.com/duckdb/duckdb/releases/download/v1.5.6/duckdb_cli-windows-arm64.zip",
    ]


class TestDownloadCli:
    def _tar(self, names):
        import tarfile

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name in names:
                info = tarfile.TarInfo(name)
                info.size = 5
                tf.addfile(info, io.BytesIO(b"bin!!"))
        return buf.getvalue()

    def _zip(self, names):
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name in names:
                zf.writestr(name, b"bin!!")
        return buf.getvalue()

    def test_tarball_first_then_zip_fallback_picks_the_binary(self, tmp_path, monkeypatch):
        answers = {
            "https://github.com/duckdb/duckdb/releases/download/v1.5.6/duckdb-cli-windows-amd64.tar.gz": None,
            "https://github.com/duckdb/duckdb/releases/download/v1.5.6/duckdb_cli-windows-amd64.zip": self._zip(["LICENSE", "duckdb.exe"]),
        }
        monkeypatch.setattr(check, "get_bytes", lambda url: answers[url])
        path = check.download_cli("1.5.6", "windows-amd64", str(tmp_path))
        assert os.path.basename(path) == "duckdb.exe"
        with open(path, "rb") as f:
            assert f.read() == b"bin!!"

    def test_tarball_with_a_directory_prefix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(check, "get_bytes", lambda url: self._tar(["./duckdb"]) if url.endswith(".tar.gz") else None)
        assert os.path.basename(check.download_cli("2.0.0", "linux-arm64", str(tmp_path))) == "duckdb"

    def test_archive_without_binary_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(check, "get_bytes", lambda url: self._tar(["LICENSE"]) if url.endswith(".tar.gz") else None)
        with pytest.raises(RuntimeError, match="no duckdb binary"):
            check.download_cli("2.0.0", "linux-arm64", str(tmp_path))

    def test_no_asset_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(check, "get_bytes", lambda url: None)
        with pytest.raises(RuntimeError, match="no CLI release asset"):
            check.download_cli("9.9.9", "linux-arm64", str(tmp_path))


def _host_wheel(tmp_path, **metadata):
    binary = tmp_path / "geo.duckdb_extension"
    binary.write_bytes(synthetic_binary("geo", host_server_platform(), "v2.0.0"))
    return tool.write_wheel(tool.inspect_extension(str(binary), core=True), tool.WheelMetadata(name="acme-geo", version="1.0.0", **metadata), str(tmp_path / "dist"))


def test_non_utf8_description_file_is_refused(tmp_path):
    bad_file = tmp_path / "desc.txt"
    bad_file.write_bytes(b"\xff\xfe invalid UTF-8")
    with pytest.raises(tool.WheelError, match=str(bad_file)):
        _read_description(str(bad_file))


def test_read_wheel_refuses_two_extension_members(tmp_path):
    wheel = tmp_path / "multi.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("multi_ext-1.0.0.data/platlib/.duckdb_extensions/v2.0.0/linux_amd64/ext1.duckdb_extension", b"binary1")
        zf.writestr("multi_ext-1.0.0.data/platlib/.duckdb_extensions/v2.0.0/linux_amd64/ext2.duckdb_extension", b"binary2")
    with pytest.raises(tool.WheelError, match="exactly one extension binary, found 2"):
        check.read_wheel(str(wheel))


@on_a_known_host
class TestCheckFailures:
    def test_a_missing_cli_release_fails_the_check(self, tmp_path, monkeypatch):
        def no_asset(version, platform, out_dir):
            raise RuntimeError("no asset")

        monkeypatch.setattr(check, "download_cli", no_asset)
        ok, detail = check.check_wheel(_host_wheel(tmp_path), pip_args=("--no-deps",))
        assert (ok, detail) == (False, "cli: no asset")

    def test_a_cli_path_that_does_not_exist_fails_the_check(self, tmp_path):
        ok, detail = check.check_wheel(_host_wheel(tmp_path), cli=str(tmp_path / "nonexistent_duckdb"), pip_args=("--no-deps",))
        assert ok is False
        assert detail.startswith("cli: ")

    def test_a_pip_timeout_fails_the_check(self, tmp_path, monkeypatch):
        real_run = subprocess.run

        def pip_times_out(cmd, **kwargs):
            if cmd[1:3] == ["-m", "pip"]:
                raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
            return real_run(cmd, **kwargs)

        monkeypatch.setattr(subprocess, "run", pip_times_out)
        ok, detail = check.check_wheel(_host_wheel(tmp_path), cli="/bin/false", pip_args=("--no-deps",))
        assert ok is False
        assert detail.startswith("pip: ") and "timeout" in detail


@pytest.mark.parametrize("field", ["requires", "project_urls", "keywords"])
def test_metadata_refuses_one_string_for_a_list(field):
    with pytest.raises(tool.WheelError, match=f"{field} must be a list of strings"):
        tool.WheelMetadata(name="acme-geo", version="1.0.0", **{field: "single-string"})


@on_a_known_host
def test_metadata_without_requires_python_has_no_such_line(tmp_path):
    with zipfile.ZipFile(_host_wheel(tmp_path, requires_python=None)) as zf:
        metadata = zf.read("acme_geo-1.0.0.dist-info/METADATA").decode("utf-8")
    assert "Requires-Python" not in metadata


def test_a_name_with_a_letter_that_only_folds_to_ascii_is_refused():
    with pytest.raises(tool.WheelError):
        tool.WheelMetadata(name="\u212a", version="1.0").validate()


def test_no_binaries_make_no_wheels(tmp_path):
    assert tool.build_wheels([], tool.WheelMetadata(name="acme-geo", version="1.0.0"), str(tmp_path), core=True) == []


def test_build_into_an_unusable_directory_is_an_error(tmp_path, capsys):
    binary = tmp_path / "geo.duckdb_extension"
    binary.write_bytes(synthetic_binary("geo", "linux_amd64", "v2.0.0"))
    (tmp_path / "afile").write_text("")
    assert main(["build", "--core-extension", "--name", "acme-geo", "--version", "1.0.0", "--out-dir", str(tmp_path / "afile" / "sub"), str(binary)]) == 1
    assert capsys.readouterr().err.startswith("error: ")


@on_a_known_host
class TestCheckEnvironmentFailures:
    def test_a_cli_that_cannot_run_fails_the_check(self, tmp_path):
        cli = tmp_path / "duckdb"
        cli.write_text("not a program")
        ok, detail = check.check_wheel(_host_wheel(tmp_path), cli=str(cli), pip_args=("--no-deps",))
        assert ok is False
        assert detail.startswith("cli: ")

    def test_a_truncated_cli_download_fails_the_check(self, tmp_path, monkeypatch):
        monkeypatch.setattr(check, "get_bytes", lambda url: b"\x1f\x8b\x08\x00truncated" if url.endswith(".tar.gz") else None)
        ok, detail = check.check_wheel(_host_wheel(tmp_path), pip_args=("--no-deps",))
        assert ok is False
        assert detail.startswith("cli: ")
