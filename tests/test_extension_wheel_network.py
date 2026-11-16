"""The check end to end, opt in with DUCKDB_EXTENSION_WHEEL_NETWORK=1: binaries served by extensions.duckdb.org for the
host platform, wheels built by the command line, checked with the DuckDB CLI release downloaded from GitHub."""

import gzip
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
from extension_binaries import host_server_platform

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(os.environ.get("DUCKDB_EXTENSION_WHEEL_NETWORK") != "1", reason="needs a network; set DUCKDB_EXTENSION_WHEEL_NETWORK=1"),
    pytest.mark.skipif(host_server_platform() is None, reason="no DuckDB build for this platform"),
]


def _served(tmp_path, name, version):
    from duckdb_extension_wheel.net import get_bytes

    data = get_bytes(f"https://extensions.duckdb.org/v{version}/{host_server_platform()}/{name}.duckdb_extension.gz")
    assert data is not None
    path = tmp_path / "binaries" / f"{name}.duckdb_extension"
    path.parent.mkdir()
    path.write_bytes(gzip.decompress(data))
    return str(path)


def _build(tmp_path, capsys, binary, *extra):
    from duckdb_extension_wheel.cli import main

    assert main(["build", "--core-extension", "--name", "acme-check", "--version", "0.1.0", *extra, "--out-dir", str(tmp_path / "dist"), binary]) == 0
    return capsys.readouterr().out.strip()


def test_cpp_extension_loads_in_its_cli(tmp_path, capsys):
    from duckdb_extension_wheel.cli import main

    wheel = _build(tmp_path, capsys, _served(tmp_path, "tpch", "2.0.0"))
    assert main(["check", wheel]) == 0, capsys.readouterr()


def test_c_api_extension_loads_in_the_cli_of_the_given_version(tmp_path, capsys):
    from duckdb_extension_wheel.cli import main

    wheel = _build(tmp_path, capsys, _served(tmp_path, "demo_capi", "2.0.0"), "--duckdb-version", "2.0.0")
    assert main(["check", wheel]) == 0, capsys.readouterr()


def test_a_cli_of_another_version_fails(tmp_path, capsys):
    from duckdb_extension_wheel.check import download_cli
    from duckdb_extension_wheel.cli import main

    wheel = _build(tmp_path, capsys, _served(tmp_path, "tpch", "2.0.0"))
    pip_platform = next(key for key, value in __import__("duckdb_extension_wheel").PLATFORMS.items() if value[0] == host_server_platform())
    (tmp_path / "cli").mkdir()
    cli = download_cli("1.5.6", pip_platform, str(tmp_path / "cli"))
    assert main(["check", wheel, "--cli", cli]) == 1
    captured = capsys.readouterr()
    assert "not found" in (captured.out + captured.err).lower()
