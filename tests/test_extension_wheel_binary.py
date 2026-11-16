"""The entry point a binary must contain, matched as a whole symbol."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
import duckdb_extension_wheel as tool
from extension_binaries import synthetic_binary


def test_an_entry_point_that_only_ends_with_the_name_is_refused(tmp_path):
    binary = tmp_path / "pch.duckdb_extension"
    binary.write_bytes(synthetic_binary("tpch", "linux_arm64", "v2.0.0"))
    with pytest.raises(tool.WheelError, match="does not contain entry point: pch_duckdb_cpp_init"):
        tool.inspect_extension(str(binary), core=True)


@pytest.mark.parametrize("entry", ["_tpch_duckdb_cpp_init", "tpch_duckdb_cpp_init"])
def test_an_entry_point_with_or_without_the_mach_o_underscore_is_accepted(tmp_path, entry):
    binary = tmp_path / "tpch.duckdb_extension"
    binary.write_bytes(synthetic_binary("tpch", "osx_arm64", "v2.0.0", entry=entry))
    assert tool.inspect_extension(str(binary), core=True).name == "tpch"


def test_an_entry_point_inside_a_longer_name_is_refused(tmp_path):
    binary = tmp_path / "pch.duckdb_extension"
    binary.write_bytes(synthetic_binary("pch", "linux_arm64", "v2.0.0", entry="x_pch_duckdb_cpp_init"))
    with pytest.raises(tool.WheelError, match="pch_duckdb_cpp_init"):
        tool.inspect_extension(str(binary), core=True)


@pytest.mark.parametrize("entry", ["geo_init_c_api", "geo_init_c_api_v2"])
def test_c_api_entry_points(tmp_path, entry):
    binary = tmp_path / "geo.duckdb_extension"
    binary.write_bytes(synthetic_binary("geo", "linux_arm64", "v1.5.6", "C_STRUCT", entry=entry))
    assert tool.inspect_extension(str(binary), duckdb_version="2.0.0", core=True).abi_type == "C_STRUCT"
