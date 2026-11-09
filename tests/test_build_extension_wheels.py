"""Tests for the extension wheel build script."""

import csv
import io
import os
import sys
import zipfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
import base64
import hashlib
import build_extension_wheels
from duckdb_extensions.registry import server_platform
from extension_binaries import synthetic_binary


def _fake_download(name, version, platform, out_dir):
    """Mock download that creates a synthetic extension binary with the footer the server's binary would have."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.duckdb_extension")
    with open(path, "wb") as f:
        f.write(synthetic_binary(name, server_platform(platform), f"v{version}"))
    return path


def _fake_download_404(name, version, platform, out_dir):
    """Mock download that simulates a 404."""
    return None


class TestBuildExtensionWheel:
    def test_wheel_filename(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "httpfs", "osx-arm64", "1.5.4", str(tmp_path)
            )
        assert whl is not None
        assert os.path.basename(whl) == (
            "duckdb_core_ext_httpfs-1.5.4-py3-none-macosx_12_0_arm64.whl"
        )

    def test_wheel_structure(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "httpfs", "osx-arm64", "1.5.4", str(tmp_path)
            )
        with zipfile.ZipFile(whl) as zf:
            names = zf.namelist()
            assert "duckdb_core_ext_httpfs/__init__.py" in names
            assert "duckdb_core_ext_httpfs-1.5.4.data/platlib/.duckdb_extensions/v1.5.4/osx_arm64/httpfs.duckdb_extension" in names
            assert "duckdb_core_ext_httpfs-1.5.4.dist-info/METADATA" in names
            assert "duckdb_core_ext_httpfs-1.5.4.dist-info/WHEEL" in names
            assert "duckdb_core_ext_httpfs-1.5.4.dist-info/top_level.txt" in names
            assert "duckdb_core_ext_httpfs-1.5.4.dist-info/RECORD" in names

    def test_metadata_no_requires_dist(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "httpfs", "osx-arm64", "1.5.4", str(tmp_path)
            )
        with zipfile.ZipFile(whl) as zf:
            metadata = zf.read("duckdb_core_ext_httpfs-1.5.4.dist-info/METADATA").decode()
            assert "Requires-Dist" not in metadata
            assert "Name: duckdb-core-ext-httpfs" in metadata

    def test_init_py_has_extension_helpers(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "httpfs", "osx-arm64", "1.5.4", str(tmp_path)
            )
        with zipfile.ZipFile(whl) as zf:
            init = zf.read("duckdb_core_ext_httpfs/__init__.py").decode()
            assert "def get_extensions_dir():" in init
            assert "def get_extension_load_path():" in init
            assert ".duckdb_extensions" in init
            assert "httpfs.duckdb_extension" in init

    def test_record_hashes_valid(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "httpfs", "osx-arm64", "1.5.4", str(tmp_path)
            )
        with zipfile.ZipFile(whl) as zf:
            record_name = [n for n in zf.namelist() if n.endswith("RECORD")][0]
            record_content = zf.read(record_name).decode("utf-8")

            reader = csv.reader(io.StringIO(record_content))
            for row in reader:
                arcname, hash_str, size_str = row
                if arcname == record_name:
                    assert hash_str == ""
                    continue
                data = zf.read(arcname)
                digest = hashlib.sha256(data).digest()
                expected_hash = "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
                assert hash_str == expected_hash
                assert int(size_str) == len(data)

    def test_reproducible_timestamps(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "httpfs", "osx-arm64", "1.5.4", str(tmp_path)
            )
        with zipfile.ZipFile(whl) as zf:
            for info in zf.infolist():
                assert info.date_time == (1980, 1, 1, 0, 0, 0)

    def test_404_returns_none(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download_404,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "jemalloc", "osx-arm64", "1.5.4", str(tmp_path)
            )
        assert whl is None

class TestMuslWheels:
    def test_musl_platform_tag_and_server_dir(self, tmp_path):
        with patch.object(build_extension_wheels, "download_extension", side_effect=_fake_download):
            whl = build_extension_wheels.build_extension_wheel("httpfs", "linux-arm64-musl", "2.0.0", str(tmp_path))
        assert os.path.basename(whl) == "duckdb_core_ext_httpfs-2.0.0-py3-none-musllinux_1_2_aarch64.whl"
        with zipfile.ZipFile(whl) as zf:
            assert "duckdb_core_ext_httpfs-2.0.0.data/platlib/.duckdb_extensions/v2.0.0/linux_arm64_musl/httpfs.duckdb_extension" in zf.namelist()


class TestMain:
    def test_explicit_platform_absent_from_server_fails(self, tmp_path):
        def download(name, version, platform, out_dir):
            return _fake_download(name, version, platform, out_dir) if platform == "osx-arm64" else None

        with patch.object(build_extension_wheels, "download_extension", side_effect=download), pytest.raises(SystemExit) as excinfo:
            build_extension_wheels.main(["--version", "2.0.0", "--extension", "ui", "--platform", "osx-amd64", "--platform", "osx-arm64", "--out-dir", str(tmp_path)])

        assert "ui osx-amd64" in str(excinfo.value)
        assert os.listdir(tmp_path) == ["duckdb_core_ext_ui-2.0.0-py3-none-macosx_12_0_arm64.whl"]

    def test_default_platforms_skip_absent_ones(self, tmp_path):
        def download(name, version, platform, out_dir):
            return _fake_download(name, version, platform, out_dir) if platform.startswith("linux") else None

        with patch.object(build_extension_wheels, "download_extension", side_effect=download):
            build_extension_wheels.main(["--version", "2.0.0", "--extension", "ui", "--out-dir", str(tmp_path)])

        assert len(os.listdir(tmp_path)) == 4

    def test_several_extensions_and_platforms(self, tmp_path):
        with patch.object(build_extension_wheels, "download_extension", side_effect=_fake_download):
            build_extension_wheels.main(["--version", "2.0.0", "--extension", "ui", "--extension", "fts", "--platform", "windows-amd64", "--out-dir", str(tmp_path)])

        assert sorted(os.listdir(tmp_path)) == [
            "duckdb_core_ext_fts-2.0.0-py3-none-win_amd64.whl",
            "duckdb_core_ext_ui-2.0.0-py3-none-win_amd64.whl",
        ]


class TestWheelMetadata:
    def _wheel(self, tmp_path):
        with patch.object(build_extension_wheels, "download_extension", side_effect=_fake_download):
            return build_extension_wheels.build_extension_wheel("legacy_parser", "linux-amd64", "2.0.0", str(tmp_path))

    def test_metadata_fields(self, tmp_path):
        with zipfile.ZipFile(self._wheel(tmp_path)) as zf:
            metadata = zf.read("duckdb_core_ext_legacy_parser-2.0.0.dist-info/METADATA").decode()
        head, body = metadata.split("\n\n", 1)
        fields = dict(line.split(": ", 1) for line in head.splitlines())
        assert fields["Name"] == "duckdb-core-ext-legacy_parser"
        assert fields["Version"] == "2.0.0"
        assert fields["Requires-Python"] == ">=3.8"
        assert "legacy_parser" in fields["Summary"]
        assert "License" not in fields and "Author" not in fields
        assert "Requires-Dist" not in metadata
        assert body.startswith("# duckdb-core-ext-legacy_parser")

    def test_wheel_file_and_top_level(self, tmp_path):
        with zipfile.ZipFile(self._wheel(tmp_path)) as zf:
            wheel = zf.read("duckdb_core_ext_legacy_parser-2.0.0.dist-info/WHEEL").decode()
            top_level = zf.read("duckdb_core_ext_legacy_parser-2.0.0.dist-info/top_level.txt").decode()
        assert "Root-Is-Purelib: false" in wheel
        assert "Tag: py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64" in wheel
        assert top_level == "duckdb_core_ext_legacy_parser\n"

    def test_no_partial_file_is_left_behind(self, tmp_path):
        self._wheel(tmp_path)
        assert [f for f in os.listdir(tmp_path) if f.endswith(".partial")] == []


class TestDownloadExtension:
    def test_404_means_none(self, tmp_path):
        with patch.object(build_extension_wheels, "get_bytes", return_value=None) as get:
            assert build_extension_wheels.download_extension("httpfs", "2.0.0", "linux-amd64", str(tmp_path)) is None
        assert get.call_args[0][0] == "https://extensions.duckdb.org/v2.0.0/linux_amd64/httpfs.duckdb_extension.gz"

    def test_decompresses_to_the_server_file_name(self, tmp_path):
        import gzip

        with patch.object(build_extension_wheels, "get_bytes", return_value=gzip.compress(b"binary")) as get:
            path = build_extension_wheels.download_extension("postgres", "2.0.0", "linux-amd64", str(tmp_path))
        assert get.call_args[0][0].endswith("/linux_amd64/postgres_scanner.duckdb_extension.gz")
        assert os.path.basename(path) == "postgres_scanner.duckdb_extension"
        with open(path, "rb") as f:
            assert f.read() == b"binary"


class TestScannerWheel:
    def test_project_name_differs_from_binary_name(self, tmp_path):
        with patch.object(build_extension_wheels, "download_extension", side_effect=lambda n, v, p, d: _fake_download("postgres_scanner", v, p, d)):
            whl = build_extension_wheels.build_extension_wheel("postgres", "linux-amd64", "2.0.0", str(tmp_path))
        assert os.path.basename(whl) == "duckdb_core_ext_postgres-2.0.0-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
        with zipfile.ZipFile(whl) as zf:
            names = zf.namelist()
            assert "duckdb_core_ext_postgres-2.0.0.data/platlib/.duckdb_extensions/v2.0.0/linux_amd64/postgres_scanner.duckdb_extension" in names
            init = zf.read("duckdb_core_ext_postgres/__init__.py").decode()
            assert '"postgres_scanner.duckdb_extension"' in init
            metadata = zf.read("duckdb_core_ext_postgres-2.0.0.dist-info/METADATA").decode()
            assert "Name: duckdb-core-ext-postgres" in metadata


class TestPartialWheel:
    def test_failed_build_leaves_no_wheel(self, tmp_path):
        def boom(name, version, platform, out_dir):
            path = _fake_download(name, version, platform, out_dir)
            os.chmod(path, 0)
            return path

        with patch.object(build_extension_wheels, "download_extension", side_effect=boom), pytest.raises(PermissionError):
            build_extension_wheels.build_extension_wheel("httpfs", "linux-amd64", "2.0.0", str(tmp_path))
        assert [f for f in os.listdir(tmp_path) if f.endswith(".whl")] == []


class TestStagingWheel:
    def test_stage_version_changes_only_the_wheel_metadata(self, tmp_path):
        prod_dir, stage_dir = tmp_path / "prod", tmp_path / "stage"
        with patch.object(build_extension_wheels, "download_extension", side_effect=_fake_download) as dl:
            whl = build_extension_wheels.build_extension_wheel("tpch", "osx-arm64", "2.0.0", str(prod_dir), stage_version="2.0.0.dev42", stage_dir=str(stage_dir))
        assert dl.call_count == 1
        assert os.path.basename(whl) == "duckdb_core_ext_tpch-2.0.0-py3-none-macosx_12_0_arm64.whl"
        staged = os.listdir(stage_dir)
        assert staged == ["duckdb_core_ext_tpch-2.0.0.dev42-py3-none-macosx_12_0_arm64.whl"]
        with zipfile.ZipFile(stage_dir / staged[0]) as zf:
            names = zf.namelist()
            assert "duckdb_core_ext_tpch-2.0.0.dev42.data/platlib/.duckdb_extensions/v2.0.0/osx_arm64/tpch.duckdb_extension" in names
            metadata = zf.read("duckdb_core_ext_tpch-2.0.0.dev42.dist-info/METADATA").decode()
            assert "Version: 2.0.0.dev42" in metadata
            init = zf.read("duckdb_core_ext_tpch/__init__.py").decode()
            assert '"v2.0.0"' in init
        with zipfile.ZipFile(whl) as prod, zipfile.ZipFile(stage_dir / staged[0]) as stage:
            binary = "data/platlib/.duckdb_extensions/v2.0.0/osx_arm64/tpch.duckdb_extension"
            assert prod.read("duckdb_core_ext_tpch-2.0.0." + binary) == stage.read("duckdb_core_ext_tpch-2.0.0.dev42." + binary)

    def test_main_passes_stage_flags(self, tmp_path):
        with patch.object(build_extension_wheels, "download_extension", side_effect=_fake_download):
            build_extension_wheels.main(["--version", "2.0.0", "--extension", "fts", "--platform", "linux-amd64", "--out-dir", str(tmp_path / "d"), "--stage-version", "2.0.0.dev7", "--stage-dir", str(tmp_path / "s")])
        assert os.listdir(tmp_path / "d") == ["duckdb_core_ext_fts-2.0.0-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"]
        assert os.listdir(tmp_path / "s") == ["duckdb_core_ext_fts-2.0.0.dev7-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"]


class TestRequiresDist:
    def test_iceberg_requires_avro_at_the_same_version(self, tmp_path):
        with patch.object(build_extension_wheels, "download_extension", side_effect=_fake_download):
            whl = build_extension_wheels.build_extension_wheel("iceberg", "linux-amd64", "2.0.0", str(tmp_path))
        with zipfile.ZipFile(whl) as zf:
            metadata = zf.read("duckdb_core_ext_iceberg-2.0.0.dist-info/METADATA").decode()
        assert "Requires-Dist: duckdb-core-ext-avro==2.0.0\n" in metadata
        assert metadata.count("Requires-Dist") == 1
