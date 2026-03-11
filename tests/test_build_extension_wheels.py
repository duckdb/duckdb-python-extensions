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
import build_extension_wheels


def _fake_download(name, version, platform, out_dir):
    """Mock download that creates a fake extension binary."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.duckdb_extension")
    with open(path, "wb") as f:
        f.write(b"fake-extension-binary-content")
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
                "httpfs", "osx-arm64", "1.4.4", str(tmp_path)
            )
        assert whl is not None
        assert os.path.basename(whl) == (
            "duckdb_core_ext_httpfs-1.4.4-py3-none-macosx_12_0_arm64.whl"
        )

    def test_wheel_structure(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "httpfs", "osx-arm64", "1.4.4", str(tmp_path)
            )
        with zipfile.ZipFile(whl) as zf:
            names = zf.namelist()
            assert "duckdb_core_ext_httpfs/__init__.py" in names
            assert "duckdb_core_ext_httpfs-1.4.4.data/platlib/.duckdb_extensions/v1.4.4/osx_arm64/httpfs.duckdb_extension" in names
            assert "duckdb_core_ext_httpfs-1.4.4.dist-info/METADATA" in names
            assert "duckdb_core_ext_httpfs-1.4.4.dist-info/WHEEL" in names
            assert "duckdb_core_ext_httpfs-1.4.4.dist-info/top_level.txt" in names
            assert "duckdb_core_ext_httpfs-1.4.4.dist-info/RECORD" in names

    def test_metadata_no_requires_dist(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "httpfs", "osx-arm64", "1.4.4", str(tmp_path)
            )
        with zipfile.ZipFile(whl) as zf:
            metadata = zf.read("duckdb_core_ext_httpfs-1.4.4.dist-info/METADATA").decode()
            assert "Requires-Dist" not in metadata
            assert "Name: duckdb-core-ext-httpfs" in metadata

    def test_init_py_has_extension_helpers(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "httpfs", "osx-arm64", "1.4.4", str(tmp_path)
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
                "httpfs", "osx-arm64", "1.4.4", str(tmp_path)
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
                expected_hash = build_extension_wheels._record_hash(data)
                assert hash_str == expected_hash
                assert int(size_str) == len(data)

    def test_reproducible_timestamps(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "httpfs", "osx-arm64", "1.4.4", str(tmp_path)
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
                "jemalloc", "osx-arm64", "1.4.4", str(tmp_path)
            )
        assert whl is None

    def test_linux_platform_tag(self, tmp_path):
        with patch.object(
            build_extension_wheels,
            "download_extension",
            side_effect=_fake_download,
        ):
            whl = build_extension_wheels.build_extension_wheel(
                "httpfs", "linux-amd64", "1.4.4", str(tmp_path)
            )
        assert "manylinux_2_17_x86_64" in os.path.basename(whl)
