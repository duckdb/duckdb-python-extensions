"""Tests for the alias wheel build script."""

import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import build_alias_wheels


class TestBuildAliasWheel:
    def test_wheel_is_noarch(self, tmp_path):
        whl = build_alias_wheels.build_alias_wheel(
            "s3", "httpfs", "1.4.4", str(tmp_path)
        )
        assert "py3-none-any" in os.path.basename(whl)

    def test_wheel_filename(self, tmp_path):
        whl = build_alias_wheels.build_alias_wheel(
            "md", "motherduck", "1.4.4", str(tmp_path)
        )
        assert os.path.basename(whl) == "duckdb_ext_md-1.4.4-py3-none-any.whl"

    def test_metadata_depends_on_canonical(self, tmp_path):
        whl = build_alias_wheels.build_alias_wheel(
            "s3", "httpfs", "1.4.4", str(tmp_path)
        )
        with zipfile.ZipFile(whl) as zf:
            metadata = zf.read("duckdb_ext_s3-1.4.4.dist-info/METADATA").decode()
            assert "Requires-Dist: duckdb-ext-httpfs==1.4.4" in metadata
            assert "Name: duckdb-ext-s3" in metadata

    def test_init_py_is_empty(self, tmp_path):
        whl = build_alias_wheels.build_alias_wheel(
            "s3", "httpfs", "1.4.4", str(tmp_path)
        )
        with zipfile.ZipFile(whl) as zf:
            init = zf.read("duckdb_ext_s3/__init__.py")
            assert init == b""

    def test_wheel_structure(self, tmp_path):
        whl = build_alias_wheels.build_alias_wheel(
            "postgres_scanner", "postgres", "1.4.4", str(tmp_path)
        )
        with zipfile.ZipFile(whl) as zf:
            names = zf.namelist()
            assert "duckdb_ext_postgres_scanner/__init__.py" in names
            assert "duckdb_ext_postgres_scanner-1.4.4.dist-info/METADATA" in names
            assert "duckdb_ext_postgres_scanner-1.4.4.dist-info/WHEEL" in names
            assert "duckdb_ext_postgres_scanner-1.4.4.dist-info/RECORD" in names

    def test_reproducible_timestamps(self, tmp_path):
        whl = build_alias_wheels.build_alias_wheel(
            "s3", "httpfs", "1.4.4", str(tmp_path)
        )
        with zipfile.ZipFile(whl) as zf:
            for info in zf.infolist():
                assert info.date_time == (1980, 1, 1, 0, 0, 0)
