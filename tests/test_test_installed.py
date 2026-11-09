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


def test_install_retries_a_failing_index_install_within_the_run(monkeypatch):
    outcomes = [1, 1, 0]
    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)

        class R:
            returncode = outcomes[len(calls) - 1]
            stdout = stderr = ""

        return R()

    monkeypatch.setattr(test_installed.subprocess, "run", fake_run)
    monkeypatch.setattr(test_installed.time, "sleep", lambda s: None)
    assert test_installed.install("python", "https://test.pypi.org/simple/", ["duckdb-core-ext-tpch==2.0.0.dev1"], "/site/tpch").returncode == 0
    assert len(calls) == 3


def test_install_from_a_directory_is_not_retried(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)

        class R:
            returncode = 1
            stdout = stderr = ""

        return R()

    monkeypatch.setattr(test_installed.subprocess, "run", fake_run)
    assert test_installed.install("python", str(tmp_path), ["duckdb-core-ext-tpch==2.0.0.dev1"], "/site/tpch").returncode == 1
    assert len(calls) == 1


def test_install_uses_find_links_for_a_directory_and_a_target(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, capture_output, text, timeout):
        seen["cmd"] = cmd

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(test_installed.subprocess, "run", fake_run)
    test_installed.install("python", str(tmp_path), ["duckdb-core-ext-tpch==2.0.0.dev1"], "/site/tpch")
    assert "--find-links" in seen["cmd"] and "--no-index" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--target") + 1] == "/site/tpch"
    test_installed.install("python", "https://test.pypi.org/simple/", ["duckdb-core-ext-tpch==2.0.0.dev1"], "/site/tpch")
    assert seen["cmd"][seen["cmd"].index("--index-url") + 1] == "https://test.pypi.org/simple/"
    assert "--extra-index-url" not in seen["cmd"]
    assert "--only-binary" in seen["cmd"] and "--no-deps" in seen["cmd"]


class TestRunWiring:
    def test_dependencies_come_from_the_release_index_into_the_same_target(self, tmp_path, monkeypatch):
        calls = []

        class Pip:
            returncode = 0
            stdout = stderr = ""

        def install(python, index, requirements, target):
            calls.append((index, tuple(requirements), target))
            os.makedirs(os.path.join(target, ".duckdb_extensions", "v2.0.0", "linux_arm64"), exist_ok=True)
            for r in requirements:
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
        assert [(c[0], c[1]) for c in calls] == [
            ("https://pypi.org/simple/", ("duckdb-core-ext-avro==2.0.0",)),
            ("https://test.pypi.org/simple/", ("duckdb-core-ext-iceberg==2.0.0.dev7",)),
            ("https://test.pypi.org/simple/", ("duckdb-core-ext-tpch==2.0.0.dev7",)),
        ]
        assert calls[0][2] == calls[1][2] and calls[1][2] != calls[2][2]
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
        monkeypatch.setattr(test_installed, "install", lambda python, index, reqs, target: (os.makedirs(os.path.join(target, ".duckdb_extensions", "v2.0.0", "linux_arm64"), exist_ok=True), open(os.path.join(target, ".duckdb_extensions", "v2.0.0", "linux_arm64", "tpch.duckdb_extension"), "w").close(), type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())[-1])
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
