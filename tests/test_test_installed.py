"""Tests for the pure parts of scripts/test_installed.py; the install and load steps need a network and a CLI."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import test_installed


def test_items_for_platform_groups_by_version():
    plan = {
        "items": [
            {"version": "2.0.0", "extension": "tpch", "platforms": ["linux-arm64", "osx-arm64"]},
            {"version": "2.0.0", "extension": "postgres", "platforms": ["linux-arm64"]},
            {"version": "1.5.6", "extension": "ui", "platforms": ["osx-arm64"]},
        ]
    }
    assert test_installed.items_for_platform(plan, "linux-arm64") == {"2.0.0": ["tpch", "postgres"]}
    assert test_installed.items_for_platform(plan, "osx-arm64") == {"2.0.0": ["tpch"], "1.5.6": ["ui"]}
    assert test_installed.items_for_platform(plan, "windows-amd64") == {}


def test_required_releases_are_the_released_dependencies():
    assert test_installed.required_releases("tpch", "2.0.0") == []
    assert test_installed.required_releases("iceberg", "2.0.0") == ["duckdb-core-ext-avro==2.0.0"]


def test_cli_home_has_a_duckdb_directory(tmp_path):
    home = test_installed.cli_home(str(tmp_path))
    assert os.path.isdir(os.path.join(home, ".duckdb"))


def _fake_pip(monkeypatch, outcomes=None):
    """A subprocess.run stand-in recording pip command lines; outcomes gives the return code per call."""
    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)

        class R:
            returncode = outcomes[len(calls) - 1] if outcomes else 0
            stdout = stderr = ""

        return R()

    monkeypatch.setattr(test_installed.subprocess, "run", fake_run)
    monkeypatch.setattr(test_installed.time, "sleep", lambda s: None)
    return calls


def test_install_downloads_from_each_index_then_installs_once(monkeypatch):
    calls = _fake_pip(monkeypatch)
    wanted = [("duckdb-core-ext-avro==2.0.0", "https://pypi.org/simple/"), ("duckdb-core-ext-iceberg==2.0.0.dev1", "https://test.pypi.org/simple/")]

    proc = test_installed.install("python", "/site/iceberg", wanted, "/dl/iceberg")

    assert proc.returncode == 0
    assert [c[3] for c in calls] == ["download", "download", "install"]
    assert calls[0][calls[0].index("--index-url") + 1] == "https://pypi.org/simple/" and calls[0][-1] == "duckdb-core-ext-avro==2.0.0"
    assert calls[1][calls[1].index("--index-url") + 1] == "https://test.pypi.org/simple/" and calls[1][-1] == "duckdb-core-ext-iceberg==2.0.0.dev1"
    assert all(c[c.index("--dest") + 1] == "/dl/iceberg" for c in calls[:2])
    install = calls[2]
    assert install[install.index("--target") + 1] == "/site/iceberg"
    assert "--no-index" in install and install[install.index("--find-links") + 1] == "/dl/iceberg"
    assert install[-2:] == ["duckdb-core-ext-avro==2.0.0", "duckdb-core-ext-iceberg==2.0.0.dev1"]
    assert all("--no-deps" in c and "--only-binary" in c for c in calls)


def test_install_retries_a_failing_download_from_an_index(monkeypatch):
    calls = _fake_pip(monkeypatch, outcomes=[1, 1, 0, 0])

    proc = test_installed.install("python", "/site/tpch", [("duckdb-core-ext-tpch==2.0.0.dev1", "https://test.pypi.org/simple/")], "/dl/tpch")

    assert proc.returncode == 0
    assert [c[3] for c in calls] == ["download", "download", "download", "install"]


def test_install_from_a_directory_is_not_retried_and_a_failed_download_stops(tmp_path, monkeypatch):
    calls = _fake_pip(monkeypatch, outcomes=[1])

    proc = test_installed.install("python", "/site/tpch", [("duckdb-core-ext-tpch==2.0.0.dev1", str(tmp_path))], "/dl/tpch")

    assert proc.returncode == 1
    assert len(calls) == 1 and calls[0][3] == "download"
    assert "--find-links" in calls[0] and "--no-index" in calls[0]


class TestRunWiring:
    def test_dependencies_come_from_the_release_index_into_the_same_target(self, tmp_path, monkeypatch):
        calls = []

        class Pip:
            returncode = 0
            stdout = stderr = ""

        def install(python, target, wanted, downloads):
            calls.append((tuple(wanted), target, downloads))
            os.makedirs(os.path.join(target, ".duckdb_extensions", "v2.0.0", "linux_arm64"), exist_ok=True)
            for r, _ in wanted:
                name = r.split("==")[0].replace("duckdb-core-ext-", "")
                open(os.path.join(target, ".duckdb_extensions", "v2.0.0", "linux_arm64", test_installed.server_name(name) + ".duckdb_extension"), "w").close()
            return Pip()

        monkeypatch.setattr(test_installed.venv, "create", lambda d, with_pip: None)
        monkeypatch.setattr(test_installed, "site_packages", lambda d: ("python", "/unused"))
        monkeypatch.setattr(test_installed, "download_cli", lambda v, p, d: "/bin/duckdb")
        monkeypatch.setattr(test_installed, "install", install)
        monkeypatch.setattr(test_installed, "load_in_cli", lambda cli, ext_dir, name, home: (True, ext_dir))
        results = tmp_path / "r.json"

        ok = test_installed.run("2.0.0", "2.0.0.dev7", "linux-arm64", ["iceberg", "tpch"], "https://test.pypi.org/simple/", str(results))

        assert ok is True
        assert [c[0] for c in calls] == [
            (("duckdb-core-ext-avro==2.0.0", "https://pypi.org/simple/"), ("duckdb-core-ext-iceberg==2.0.0.dev7", "https://test.pypi.org/simple/")),
            (("duckdb-core-ext-tpch==2.0.0.dev7", "https://test.pypi.org/simple/"),),
        ]
        assert calls[0][1] != calls[1][1] and calls[0][2] != calls[1][2]
        data = json.loads(results.read_text())
        assert data["2.0.0 iceberg linux-arm64"]["detail"].endswith(os.path.join("site", "iceberg", ".duckdb_extensions"))

    def test_an_earlier_failure_in_the_file_does_not_fail_this_call(self, tmp_path, monkeypatch):
        results = tmp_path / "r.json"
        results.write_text(json.dumps({"1.5.6 ui linux-arm64": {"ok": False, "detail": "earlier"}}))
        monkeypatch.setattr(test_installed.venv, "create", lambda d, with_pip: None)
        monkeypatch.setattr(test_installed, "site_packages", lambda d: ("python", "/unused"))
        monkeypatch.setattr(test_installed, "download_cli", lambda v, p, d: "/bin/duckdb")

        class Pip:
            returncode = 1
            stdout, stderr = "", "no wheel"

        monkeypatch.setattr(test_installed, "install", lambda *a: Pip())
        assert test_installed.run("2.0.0", "2.0.0.dev7", "linux-arm64", ["tpch"], "https://test.pypi.org/simple/", str(results)) is False
        monkeypatch.setattr(test_installed, "install", lambda python, target, wanted, downloads: (os.makedirs(os.path.join(target, ".duckdb_extensions", "v2.0.0", "linux_arm64"), exist_ok=True), open(os.path.join(target, ".duckdb_extensions", "v2.0.0", "linux_arm64", "tpch.duckdb_extension"), "w").close(), type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())[-1])
        monkeypatch.setattr(test_installed, "load_in_cli", lambda cli, ext_dir, name, home: (True, "loaded"))
        assert test_installed.run("2.0.0", "2.0.0.dev7", "linux-arm64", ["tpch"], "https://test.pypi.org/simple/", str(results)) is True
        data = json.loads(results.read_text())
        assert data["1.5.6 ui linux-arm64"]["ok"] is False and data["2.0.0 tpch linux-arm64"]["ok"] is True


class TestVerdict:
    EXT_DIR = "/venv/lib/python3.12/site-packages/.duckdb_extensions"

    def test_loaded_from_the_pip_dir_passes(self):
        out = f"extension_name,loaded,install_path\npostgres_scanner,true,{self.EXT_DIR}/v2.0.0/linux_arm64/postgres_scanner.duckdb_extension\n"
        assert test_installed.verdict(0, out, "postgres", self.EXT_DIR) is True

    def test_built_in_or_other_directory_fails(self):
        assert test_installed.verdict(0, "extension_name,loaded,install_path\ntpch,true,(BUILT-IN)\n", "tpch", self.EXT_DIR) is False
        assert test_installed.verdict(0, "extension_name,loaded,install_path\ntpch,true,/home/u/.duckdb/extensions/v2.0.0/linux_arm64/tpch.duckdb_extension\n", "tpch", self.EXT_DIR) is False

    def test_failed_load_fails(self):
        assert test_installed.verdict(1, "", "tpch", self.EXT_DIR) is False
        assert test_installed.verdict(0, "extension_name,loaded,install_path\ntpch,false,\n", "tpch", self.EXT_DIR) is False


class TestLoadInCli:
    def test_argv_has_no_flag_the_old_cli_rejects(self, monkeypatch):
        seen = {}

        def fake_run(cmd, capture_output, text, timeout, env):
            seen["cmd"] = cmd
            seen["env"] = env

            class R:
                returncode = 0
                stdout = "extension_name,loaded,install_path\ntpch,true,/sp/.duckdb_extensions/v1.5.6/linux_amd64/tpch.duckdb_extension\n"
                stderr = ""

            return R()

        monkeypatch.setattr(test_installed.subprocess, "run", fake_run)
        ok, detail = test_installed.load_in_cli("/bin/duckdb", "/sp/.duckdb_extensions", "tpch", "/tmp/home")
        assert ok is True
        assert "-no-agent" not in seen["cmd"]
        assert seen["cmd"][:2] == ["/bin/duckdb", "-csv"]
        assert "SET autoinstall_known_extensions=false" in seen["cmd"][3]
        assert "SET extension_directories=['/sp/.duckdb_extensions']" in seen["cmd"][3]
        assert seen["cmd"][5].startswith("LOAD tpch;")
        assert seen["env"]["HOME"] == "/tmp/home" and seen["env"]["USERPROFILE"] == "/tmp/home"


class TestRun:
    def test_setup_failure_records_every_extension_and_merges(self, tmp_path, monkeypatch):
        results = tmp_path / "r.json"
        results.write_text(json.dumps({"1.5.6 ui osx-arm64": {"ok": True, "detail": "earlier"}}))
        monkeypatch.setattr(test_installed, "download_cli", lambda v, p, d: (_ for _ in ()).throw(RuntimeError("no asset")))
        ok = test_installed.run("2.0.0", "2.0.0.dev1", "linux-arm64", ["tpch", "fts"], str(tmp_path), str(results))
        data = json.loads(results.read_text())
        assert ok is False
        assert data["1.5.6 ui osx-arm64"]["ok"] is True
        assert data["2.0.0 tpch linux-arm64"]["ok"] is False and "no asset" in data["2.0.0 tpch linux-arm64"]["detail"]
        assert data["2.0.0 fts linux-arm64"]["ok"] is False

    def test_main_exits_nonzero_on_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(test_installed, "run", lambda *a: False)
        with pytest.raises(SystemExit) as excinfo:
            test_installed._main(["--platform", "linux-arm64", "--version", "2.0.0", "--extension", "tpch", "--results", str(tmp_path / "r.json")])
        assert excinfo.value.code == 1
