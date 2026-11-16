"""The behavior of duckdb-extension-wheel: what it reads from a binary, the wheel it writes, its command line and
its conformance check, and that the pipeline's wheels match the reference wheels byte for byte."""

import base64
import csv
import hashlib
import io
import json
import os
import stat
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
import build_extension_wheels
from extension_binaries import REFERENCE_CASES, host_server_platform, reference_binary, synthetic_binary

import duckdb_extension_wheel as tool
from duckdb_extension_wheel import check as tool_check
from duckdb_extension_wheel.cli import main as tool_main


DATA = os.path.join(os.path.dirname(__file__), "data")
REFERENCE_DIR = os.path.join(DATA, "reference_wheels")
LINUX_TAG = "manylinux_2_17_x86_64.manylinux2014_x86_64"


def _write(tmp_path, file_name, data):
    path = tmp_path / file_name
    path.write_bytes(data)
    return str(path)


def _binary(tmp_path, name="geo", platform="linux_amd64", version="v2.0.0", abi_type="CPP", **kw):
    return _write(tmp_path, f"{name}.duckdb_extension", synthetic_binary(name, platform, version, abi_type, **kw))


def _metadata(**kw):
    kw.setdefault("name", "acme-duckdb-geo")
    kw.setdefault("version", "0.3.1")
    return tool.WheelMetadata(**kw)


def _members(path):
    with zipfile.ZipFile(path) as zf:
        return [(i.filename, i.date_time, i.external_attr, i.compress_type, zf.read(i.filename)) for i in zf.infolist()]


def _reference(case):
    name, platform, _, wheel_version = case[:4]
    from duckdb_extensions.registry import wheel_filename

    return os.path.join(REFERENCE_DIR, wheel_filename(name, wheel_version, platform))


def _metadata_text(path):
    with zipfile.ZipFile(path) as zf:
        return zf.read(next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))).decode("utf-8")


class TestFooter:
    """Against the footers of 48 binaries served by extensions.duckdb.org for 1.5.0 and 2.0.0."""

    with open(os.path.join(DATA, "footers", "expected.json")) as f:
        EXPECTED = json.load(f)

    @pytest.mark.parametrize("rel", sorted(EXPECTED))
    def test_real_footers(self, rel):
        with open(os.path.join(DATA, "footers", rel), "rb") as f:
            data = f.read()
        footer = tool.read_footer(data)
        expected = self.EXPECTED[rel]
        assert (footer.platform, footer.version, footer.extension_version, footer.abi_type) == (
            expected["platform"],
            expected["version"],
            expected["extension_version"],
            expected["abi_type"],
        )

    def test_empty_abi_field_reads_as_cpp(self):
        assert tool.read_footer(synthetic_binary("geo", "linux_amd64", "v2.0.0", abi_type="")).abi_type == "CPP"

    def test_reads_only_the_last_512_bytes(self):
        data = b"x" * 10_000 + synthetic_binary("geo", "osx_arm64", "v1.5.6")
        assert tool.read_footer(data).platform == "osx_arm64"

    @pytest.mark.parametrize(
        "data, message",
        [
            (synthetic_binary("geo", "linux_amd64", "v2.0.0")[-511:], "not a DuckDB extension"),
            (synthetic_binary("geo", "linux_amd64", "v2.0.0", magic="3"), "not a DuckDB extension"),
            (b"\0" * 4096, "not a DuckDB extension"),
            (synthetic_binary("geo", "linux_amd64", "v2.0.0", abi_type="C_STRUCT_V9"), "C_STRUCT_V9"),
        ],
    )
    def test_invalid_footers(self, data, message):
        with pytest.raises(tool.WheelError, match=message):
            tool.read_footer(data)


class TestInspect:
    """name, platform, DuckDB version and ABI type of a binary."""

    def test_cpp_binary(self, tmp_path):
        ext = tool.inspect_extension(_binary(tmp_path, "geo", "linux_arm64_musl", "v2.0.0"), core=True)
        assert (ext.name, ext.platform, ext.duckdb_version, ext.abi_type) == ("geo", "linux-arm64-musl", "2.0.0", "CPP")

    @pytest.mark.parametrize("server_platform", ["linux_amd64", "linux_amd64_musl", "linux_arm64", "linux_arm64_musl", "osx_amd64", "osx_arm64", "windows_amd64", "windows_arm64"])
    def test_every_server_platform_maps_to_its_pip_platform(self, tmp_path, server_platform):
        ext = tool.inspect_extension(_binary(tmp_path, platform=server_platform), core=True)
        assert tool.PLATFORMS[ext.platform][0] == server_platform

    def test_cpp_binary_with_agreeing_duckdb_version(self, tmp_path):
        assert tool.inspect_extension(_binary(tmp_path), duckdb_version="2.0.0", core=True).duckdb_version == "2.0.0"

    def test_cpp_binary_with_disagreeing_duckdb_version(self, tmp_path):
        with pytest.raises(tool.WheelError, match="v2.0.0"):
            tool.inspect_extension(_binary(tmp_path), duckdb_version="1.5.6", core=True)

    def test_unstable_c_api_binary_carries_the_duckdb_version(self, tmp_path):
        ext = tool.inspect_extension(_binary(tmp_path, version="v1.5.0", abi_type="C_STRUCT_UNSTABLE"), core=True)
        assert (ext.duckdb_version, ext.abi_type) == ("1.5.0", "C_STRUCT_UNSTABLE")

    def test_c_api_binary_needs_the_duckdb_version(self, tmp_path):
        with pytest.raises(tool.WheelError, match="duckdb.version"):
            tool.inspect_extension(_binary(tmp_path, version="v1.5.6", abi_type="C_STRUCT"), core=True)

    def test_c_api_binary_takes_the_given_duckdb_version(self, tmp_path):
        ext = tool.inspect_extension(_binary(tmp_path, version="v1.5.6", abi_type="C_STRUCT"), duckdb_version="2.0.0", core=True)
        assert (ext.duckdb_version, ext.abi_type) == ("2.0.0", "C_STRUCT")

    @pytest.mark.parametrize("platform", ["wasm_eh", "wasm_mvp", "wasm_threads", "windows_amd64_mingw", "linux_amd64_gcc4"])
    def test_platform_without_a_wheel_tag(self, tmp_path, platform):
        with pytest.raises(tool.WheelError, match=platform):
            tool.inspect_extension(_binary(tmp_path, platform=platform), core=True)

    @pytest.mark.parametrize("version", ["v2.0.1-dev42", "5f5512b827", "2.0.0", "v2.0"])
    def test_development_or_malformed_footer_version(self, tmp_path, version):
        with pytest.raises(tool.WheelError, match="version"):
            tool.inspect_extension(_binary(tmp_path, version=version), core=True)

    @pytest.mark.parametrize("given", ["v2.0.0", "2.0", "2.0.0-dev1", "", "2.0.0\n"])
    def test_malformed_given_duckdb_version(self, tmp_path, given):
        with pytest.raises(tool.WheelError, match="version"):
            tool.inspect_extension(_binary(tmp_path, version="v1.5.6", abi_type="C_STRUCT"), duckdb_version=given, core=True)

    def test_binary_without_its_entry_point(self, tmp_path):
        path = _write(tmp_path, "geo.duckdb_extension", synthetic_binary("geo", "linux_amd64", "v2.0.0", entry="tpch_duckdb_cpp_init"))
        with pytest.raises(tool.WheelError, match="geo_duckdb_cpp_init"):
            tool.inspect_extension(path, core=True)

    def test_c_api_binary_needs_the_c_api_entry_point(self, tmp_path):
        path = _write(tmp_path, "geo.duckdb_extension", synthetic_binary("geo", "linux_amd64", "v1.5.6", "C_STRUCT", entry="geo_duckdb_cpp_init"))
        with pytest.raises(tool.WheelError, match="geo_init_c_api"):
            tool.inspect_extension(path, duckdb_version="2.0.0", core=True)

    @pytest.mark.parametrize("file_name", ["geo.so", "geo.duckdb_extension.gz", "Geo.duckdb_extension", "my-geo.duckdb_extension", ".duckdb_extension"])
    def test_file_name_that_is_not_an_extension_name(self, tmp_path, file_name):
        path = _write(tmp_path, file_name, synthetic_binary("geo", "linux_amd64", "v2.0.0"))
        with pytest.raises(tool.WheelError):
            tool.inspect_extension(path, core=True)

    def test_from_bytes_names_the_extension(self):
        ext = tool.extension_from_bytes("geo", synthetic_binary("geo", "osx_amd64", "v2.0.0"))
        assert (ext.name, ext.platform, ext.data[-512:]) == ("geo", "osx-amd64", synthetic_binary("geo", "osx_amd64", "v2.0.0")[-512:])


class TestRealBinaries:
    """On whole served binaries small enough to keep: demo_capi for linux_amd64."""

    def _path(self, version):
        return os.path.join(DATA, "binaries", version, "linux_amd64", "demo_capi.duckdb_extension")

    def test_c_api_demo_for_2_0_0(self):
        ext = tool.inspect_extension(self._path("v2.0.0"), duckdb_version="2.0.0", core=True)
        assert (ext.name, ext.platform, ext.duckdb_version, ext.abi_type) == ("demo_capi", "linux-amd64", "2.0.0", "C_STRUCT")

    def test_c_api_demo_for_2_0_0_names_its_c_api_version_when_no_duckdb_version_is_given(self):
        with pytest.raises(tool.WheelError, match="v1.5.6"):
            tool.inspect_extension(self._path("v2.0.0"), core=True)

    def test_unstable_c_api_demo_for_1_5_0(self):
        ext = tool.inspect_extension(self._path("v1.5.0"), core=True)
        assert (ext.duckdb_version, ext.abi_type) == ("1.5.0", "C_STRUCT_UNSTABLE")


class TestWheelLayout:
    """The wheel layout, for any project name and wheel version."""

    def test_file_name_and_members(self, tmp_path):
        ext = tool.inspect_extension(_binary(tmp_path), core=True)
        path = tool.write_wheel(ext, _metadata(name="Acme.DuckDB-Geo"), str(tmp_path / "out"))
        assert os.path.basename(path) == f"acme_duckdb_geo-0.3.1-py3-none-{LINUX_TAG}.whl"
        with zipfile.ZipFile(path) as zf:
            assert zf.namelist() == [
                "acme_duckdb_geo/__init__.py",
                "acme_duckdb_geo-0.3.1.data/platlib/.duckdb_extensions/v2.0.0/linux_amd64/geo.duckdb_extension",
                "acme_duckdb_geo-0.3.1.dist-info/METADATA",
                "acme_duckdb_geo-0.3.1.dist-info/WHEEL",
                "acme_duckdb_geo-0.3.1.dist-info/top_level.txt",
                "acme_duckdb_geo-0.3.1.dist-info/RECORD",
            ]

    def test_binary_bytes_wheel_file_and_top_level(self, tmp_path):
        binary = _binary(tmp_path)
        path = tool.write_wheel(tool.inspect_extension(binary, core=True), _metadata(), str(tmp_path / "out"))
        with zipfile.ZipFile(path) as zf:
            data = zf.read("acme_duckdb_geo-0.3.1.data/platlib/.duckdb_extensions/v2.0.0/linux_amd64/geo.duckdb_extension")
            wheel = zf.read("acme_duckdb_geo-0.3.1.dist-info/WHEEL").decode()
            top_level = zf.read("acme_duckdb_geo-0.3.1.dist-info/top_level.txt").decode()
        with open(binary, "rb") as f:
            assert data == f.read()
        assert wheel == f"Wheel-Version: 1.0\nGenerator: duckdb-extension-build\nRoot-Is-Purelib: false\nTag: py3-none-{LINUX_TAG}\n"
        assert top_level == "acme_duckdb_geo\n"

    def test_init_module_points_at_the_binary(self, tmp_path):
        path = tool.write_wheel(tool.inspect_extension(_binary(tmp_path), core=True), _metadata(), str(tmp_path / "out"))
        with zipfile.ZipFile(path) as zf:
            source = zf.read("acme_duckdb_geo/__init__.py").decode()
        site = tmp_path / "site"
        (site / "acme_duckdb_geo").mkdir(parents=True)
        (site / "acme_duckdb_geo" / "__init__.py").write_text(source)
        namespace = {"__file__": str(site / "acme_duckdb_geo" / "__init__.py")}
        exec(compile(source, namespace["__file__"], "exec"), namespace)
        assert namespace["get_extensions_dir"]() == os.path.join(str(site), ".duckdb_extensions")
        assert namespace["get_extension_load_path"]() == os.path.join(str(site), ".duckdb_extensions", "v2.0.0", "linux_amd64", "geo.duckdb_extension")

    def test_record_hashes_sizes_and_reproducible_entries(self, tmp_path):
        path = tool.write_wheel(tool.inspect_extension(_binary(tmp_path), core=True), _metadata(), str(tmp_path / "out"))
        with zipfile.ZipFile(path) as zf:
            rows = list(csv.reader(io.StringIO(zf.read("acme_duckdb_geo-0.3.1.dist-info/RECORD").decode())))
            assert [r[0] for r in rows] == zf.namelist()
            assert rows[-1] == ["acme_duckdb_geo-0.3.1.dist-info/RECORD", "", ""]
            for arcname, digest, size in rows[:-1]:
                assert digest == "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(zf.read(arcname)).digest()).rstrip(b"=").decode()
                assert int(size) == len(zf.read(arcname))
            for info in zf.infolist():
                assert info.date_time == (1980, 1, 1, 0, 0, 0)
                assert info.external_attr >> 16 == stat.S_IFREG | 0o644
                assert info.compress_type == zipfile.ZIP_DEFLATED

    def test_same_inputs_same_bytes(self, tmp_path):
        ext = tool.inspect_extension(_binary(tmp_path), core=True)
        first = tool.write_wheel(ext, _metadata(), str(tmp_path / "a"))
        second = tool.write_wheel(ext, _metadata(), str(tmp_path / "b"))
        with open(first, "rb") as a, open(second, "rb") as b:
            assert a.read() == b.read()

    def test_a_failed_write_leaves_nothing(self, tmp_path, monkeypatch):
        ext = tool.inspect_extension(_binary(tmp_path), core=True)

        def boom(self, info, data, *args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(zipfile.ZipFile, "writestr", boom)
        with pytest.raises(OSError, match="disk full"):
            tool.write_wheel(ext, _metadata(), str(tmp_path / "out"))
        assert os.listdir(tmp_path / "out") == []

    @pytest.mark.parametrize("name", ["3d-geo", "geo ext", "-geo", "geo-", "", "café"])
    def test_names_that_make_no_importable_module(self, tmp_path, name):
        with pytest.raises(tool.WheelError):
            tool.write_wheel(tool.inspect_extension(_binary(tmp_path), core=True), _metadata(name=name), str(tmp_path / "out"))


class TestMetadata:
    """the METADATA file."""

    def _text(self, tmp_path, **kw):
        path = tool.write_wheel(tool.inspect_extension(_binary(tmp_path), core=True), _metadata(**kw), str(tmp_path / "out"))
        return _metadata_text(path)

    def test_only_the_required_fields(self, tmp_path):
        assert self._text(tmp_path) == "Metadata-Version: 2.1\nName: acme-duckdb-geo\nVersion: 0.3.1\nRequires-Python: >=3.8\n"

    def test_every_field_in_order(self, tmp_path):
        text = self._text(
            tmp_path,
            requires=["duckdb-cli==2.0.0", 'shapely>=2; python_version >= "3.9"'],
            summary="Geometry for DuckDB",
            description="# Geo\n\nLong text.\n",
            description_content_type="text/markdown",
            project_urls=["Source, https://example.com/geo", "Issues, https://example.com/geo/issues"],
            requires_python=">=3.9",
            keywords=["duckdb", "geo"],
        )
        assert text == (
            "Metadata-Version: 2.1\n"
            "Name: acme-duckdb-geo\n"
            "Version: 0.3.1\n"
            "Summary: Geometry for DuckDB\n"
            "Keywords: duckdb,geo\n"
            "Requires-Python: >=3.9\n"
            "Requires-Dist: duckdb-cli==2.0.0\n"
            'Requires-Dist: shapely>=2; python_version >= "3.9"\n'
            "Project-URL: Source, https://example.com/geo\n"
            "Project-URL: Issues, https://example.com/geo/issues\n"
            "Description-Content-Type: text/markdown\n"
            "\n"
            "# Geo\n\nLong text.\n"
        )

    def test_license_expression_raises_the_metadata_version(self, tmp_path):
        text = self._text(tmp_path, license_expression="MIT OR Apache-2.0", summary="Geo")
        assert text == "Metadata-Version: 2.4\nName: acme-duckdb-geo\nVersion: 0.3.1\nSummary: Geo\nLicense-Expression: MIT OR Apache-2.0\nRequires-Python: >=3.8\n"

    def test_description_without_a_content_type_is_plain_text(self, tmp_path):
        assert self._text(tmp_path, description="Plain.\n").endswith("Description-Content-Type: text/plain\n\nPlain.\n")

    def test_wheel_version_is_independent_of_the_duckdb_version(self, tmp_path):
        path = tool.write_wheel(tool.inspect_extension(_binary(tmp_path), core=True), _metadata(version="2026.11.9.post1"), str(tmp_path / "out"))
        with zipfile.ZipFile(path) as zf:
            assert "acme_duckdb_geo-2026.11.9.post1.data/platlib/.duckdb_extensions/v2.0.0/linux_amd64/geo.duckdb_extension" in zf.namelist()

    @pytest.mark.parametrize(
        "fields",
        [
            {"version": "2.0.0-1"},
            {"version": "v2.0.0"},
            {"version": "2.0.0.DEV1"},
            {"version": "01.2"},
            {"version": ""},
            {"summary": "two\nlines"},
            {"summary": "carriage\rreturn"},
            {"requires": ["ok", ""]},
            {"requires": ["a\nRequires-Dist: b"]},
            {"project_urls": ["https://example.com"]},
            {"keywords": ["a,b"]},
            {"requires_python": ">=3.8\nX: y"},
            {"license_expression": "MIT\n"},
            {"name": "geo\n"},
            {"version": "1.0\n"},
            {"project_urls": ["Source, https://example.com\nRequires-Dist: other"]},
            {"keywords": ["a\nb"]},
            {"description": "d", "description_content_type": "text/plain\nX: y"},
        ],
    )
    def test_invalid_fields(self, tmp_path, fields):
        with pytest.raises(tool.WheelError):
            self._text(tmp_path, **fields)


class TestBuildWheels:
    """several binaries in one call."""

    def test_one_wheel_per_platform_in_the_given_order(self, tmp_path):
        paths = []
        for platform in ("windows_arm64", "linux_amd64", "osx_arm64"):
            (tmp_path / platform).mkdir()
            paths.append(_binary(tmp_path / platform, platform=platform))
        wheels = tool.build_wheels(paths, _metadata(), str(tmp_path / "out"), core=True)
        assert [os.path.basename(w) for w in wheels] == [
            "acme_duckdb_geo-0.3.1-py3-none-win_arm64.whl",
            f"acme_duckdb_geo-0.3.1-py3-none-{LINUX_TAG}.whl",
            "acme_duckdb_geo-0.3.1-py3-none-macosx_12_0_arm64.whl",
        ]

    def _two(self, tmp_path, first, second):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        return [_binary(tmp_path / "a", **first), _binary(tmp_path / "b", **second)]

    @pytest.mark.parametrize(
        "first, second, message",
        [
            ({"name": "geo"}, {"name": "geom", "platform": "osx_arm64"}, "geom"),
            ({"version": "v2.0.0"}, {"version": "v1.5.6", "platform": "osx_arm64"}, "1.5.6"),
            ({}, {}, "linux_amd64|linux-amd64"),
        ],
    )
    def test_binaries_that_do_not_belong_in_one_project_release(self, tmp_path, first, second, message):
        with pytest.raises(tool.WheelError, match=message):
            tool.build_wheels(self._two(tmp_path, first, second), _metadata(), str(tmp_path / "out"), core=True)
        assert not (tmp_path / "out").exists() or os.listdir(tmp_path / "out") == []

    def test_a_refused_binary_stops_every_wheel(self, tmp_path):
        paths = self._two(tmp_path, {}, {"platform": "wasm_eh"})
        with pytest.raises(tool.WheelError, match="wasm_eh"):
            tool.build_wheels(paths, _metadata(), str(tmp_path / "out"), core=True)
        assert not (tmp_path / "out").exists() or os.listdir(tmp_path / "out") == []

    def test_a_refusal_names_the_binary(self, tmp_path):
        paths = self._two(tmp_path, {}, {"platform": "wasm_eh"})
        with pytest.raises(tool.WheelError) as excinfo:
            tool.build_wheels(paths, _metadata(), str(tmp_path / "out"), core=True)
        assert paths[1] in str(excinfo.value)

    def test_c_api_binaries_for_every_platform_with_the_duckdb_version(self, tmp_path):
        paths = self._two(tmp_path, {"version": "v1.5.6", "abi_type": "C_STRUCT"}, {"version": "v1.5.6", "abi_type": "C_STRUCT", "platform": "windows_amd64"})
        wheels = tool.build_wheels(paths, _metadata(), str(tmp_path / "out"), duckdb_version="2.0.0", core=True)
        with zipfile.ZipFile(wheels[1]) as zf:
            assert "acme_duckdb_geo-0.3.1.data/platlib/.duckdb_extensions/v2.0.0/windows_amd64/geo.duckdb_extension" in zf.namelist()


class TestCommandLine:
    """duckdb-extension-wheel build."""

    def test_build_prints_the_wheels(self, tmp_path, capsys):
        (tmp_path / "x").mkdir()
        binaries = [_binary(tmp_path), _binary(tmp_path / "x", platform="osx_arm64")]
        status = tool_main(["build", "--core-extension", "--name", "acme-duckdb-geo", "--version", "0.3.1", "--out-dir", str(tmp_path / "dist"), *binaries])
        out = capsys.readouterr().out.split()
        assert status == 0
        assert [os.path.basename(p) for p in out] == [f"acme_duckdb_geo-0.3.1-py3-none-{LINUX_TAG}.whl", "acme_duckdb_geo-0.3.1-py3-none-macosx_12_0_arm64.whl"]
        assert sorted(os.listdir(tmp_path / "dist")) == sorted(os.path.basename(p) for p in out)

    def test_every_option_reaches_the_metadata(self, tmp_path, capsys):
        description = tmp_path / "README.rst"
        description.write_text("Geo\n===\n")
        status = tool_main([
            "build", "--core-extension", "--name", "acme-duckdb-geo", "--version", "0.3.1",
            "--requires", "duckdb-cli==2.0.0", "--requires", "shapely>=2",
            "--summary", "Geometry", "--description-file", str(description), "--license", "MIT",
            "--project-url", "Source, https://example.com/geo", "--requires-python", ">=3.10",
            "--keyword", "duckdb", "--keyword", "geo", "--out-dir", str(tmp_path / "dist"), _binary(tmp_path),
        ])
        assert status == 0
        assert _metadata_text(capsys.readouterr().out.strip()) == (
            "Metadata-Version: 2.4\nName: acme-duckdb-geo\nVersion: 0.3.1\nSummary: Geometry\nKeywords: duckdb,geo\n"
            "License-Expression: MIT\nRequires-Python: >=3.10\nRequires-Dist: duckdb-cli==2.0.0\nRequires-Dist: shapely>=2\n"
            "Project-URL: Source, https://example.com/geo\nDescription-Content-Type: text/x-rst\n\nGeo\n===\n"
        )

    @pytest.mark.parametrize("suffix, content_type", [(".md", "text/markdown"), (".markdown", "text/markdown"), (".rst", "text/x-rst"), (".txt", "text/plain"), ("", "text/plain")])
    def test_description_content_type_follows_the_suffix(self, tmp_path, capsys, suffix, content_type):
        description = tmp_path / f"DESCRIPTION{suffix}"
        description.write_text("Text.\n")
        tool_main(["build", "--core-extension", "--name", "g", "--version", "1", "--description-file", str(description), "--out-dir", str(tmp_path / "d"), _binary(tmp_path)])
        assert f"Description-Content-Type: {content_type}\n" in _metadata_text(capsys.readouterr().out.strip())

    def test_c_api_binary_with_duckdb_version(self, tmp_path, capsys):
        binary = _binary(tmp_path, version="v1.5.6", abi_type="C_STRUCT")
        assert tool_main(["build", "--core-extension", "--name", "g", "--version", "1", "--duckdb-version", "2.0.0", "--out-dir", str(tmp_path / "d"), binary]) == 0
        with zipfile.ZipFile(capsys.readouterr().out.strip()) as zf:
            assert "g-1.data/platlib/.duckdb_extensions/v2.0.0/linux_amd64/geo.duckdb_extension" in zf.namelist()

    @pytest.mark.parametrize(
        "extra, binary_kw",
        [
            ([], {"version": "v1.5.6", "abi_type": "C_STRUCT"}),
            (["--duckdb-version", "1.5.6"], {}),
            ([], {"platform": "wasm_eh"}),
            (["--project-url", "nolabel"], {}),
        ],
    )
    def test_refusals_exit_1_with_a_message_and_no_wheel(self, tmp_path, capsys, extra, binary_kw):
        status = tool_main(["build", "--core-extension", "--name", "g", "--version", "1", *extra, "--out-dir", str(tmp_path / "d"), _binary(tmp_path, **binary_kw)])
        captured = capsys.readouterr()
        assert status == 1
        assert captured.err.startswith("error: ")
        assert captured.out == ""
        assert not (tmp_path / "d").exists() or os.listdir(tmp_path / "d") == []

    def test_missing_file_is_a_refusal(self, tmp_path, capsys):
        assert tool_main(["build", "--core-extension", "--name", "g", "--version", "1", "--out-dir", str(tmp_path / "d"), str(tmp_path / "geo.duckdb_extension")]) == 1
        assert capsys.readouterr().err.startswith("error: ")

    @pytest.mark.parametrize("argv", [["build", "--version", "1", "x.duckdb_extension"], ["build", "--name", "g", "x.duckdb_extension"], ["build", "--name", "g", "--version", "1"], []])
    def test_usage_errors_exit_2(self, argv):
        with pytest.raises(SystemExit) as excinfo:
            tool_main(argv)
        assert excinfo.value.code == 2


class TestReferenceWheels:
    """The pipeline's wheels and an author's equivalent call reproduce the reference wheels byte for byte."""

    @pytest.mark.parametrize("case", REFERENCE_CASES, ids=lambda c: f"{c[0]}-{c[1]}-{c[3]}")
    def test_pipeline_wheels_match_the_reference(self, tmp_path, case):
        name, platform, version, wheel_version = case[:4]
        path = build_extension_wheels.write_wheel(name, platform, version, reference_binary(case), str(tmp_path), wheel_version=wheel_version)
        assert os.path.basename(path) == os.path.basename(_reference(case))
        assert _members(path) == _members(_reference(case))

    @pytest.mark.parametrize("case", REFERENCE_CASES, ids=lambda c: f"{c[0]}-{c[1]}-{c[3]}")
    def test_command_line_reproduces_the_reference(self, tmp_path, capsys, case):
        name, platform, version, wheel_version, server_file = case[:5]
        head, body = _metadata_text(_reference(case)).split("\n\n", 1)
        fields = [line.split(": ", 1) for line in head.splitlines()]
        argv = ["build", "--core-extension", "--name", dict(fields)["Name"], "--version", wheel_version, "--duckdb-version", version, "--summary", dict(fields)["Summary"]]
        argv += [a for key, value in fields if key == "Requires-Dist" for a in ("--requires", value)]
        argv += [a for key, value in fields if key == "Project-URL" for a in ("--project-url", value)]
        description = tmp_path / "description.md"
        description.write_bytes(body.encode("utf-8"))
        binary = _write(tmp_path, f"{server_file}.duckdb_extension", reference_binary(case))
        assert tool_main([*argv, "--description-file", str(description), "--out-dir", str(tmp_path / "dist"), binary]) == 0
        path = capsys.readouterr().out.strip()
        assert os.path.basename(path) == os.path.basename(_reference(case))
        assert _members(path) == _members(_reference(case))


class TestPipeline:
    """a binary the tool refuses fails the pipeline's item after the other platforms are built."""

    def _download_with_wrong_platform_for(self, refused):
        from duckdb_extensions.registry import server_platform

        def download(name, version, platform, out_dir):
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"{name}.duckdb_extension")
            footer_platform = "linux_arm64" if platform == refused else server_platform(platform)
            with open(path, "wb") as f:
                f.write(synthetic_binary(name, footer_platform, f"v{version}"))
            return path

        return download

    def test_explicit_platforms(self, tmp_path):
        from unittest.mock import patch

        with patch.object(build_extension_wheels, "download_extension", side_effect=self._download_with_wrong_platform_for("linux-amd64")), pytest.raises(SystemExit) as excinfo:
            build_extension_wheels.main(["--version", "2.0.0", "--extension", "fts", "--platform", "linux-amd64", "--platform", "osx-arm64", "--out-dir", str(tmp_path)])
        assert "fts linux-amd64" in str(excinfo.value)
        assert os.listdir(tmp_path) == ["duckdb_core_ext_fts-2.0.0-py3-none-macosx_12_0_arm64.whl"]

    def test_default_platforms(self, tmp_path):
        from unittest.mock import patch

        with patch.object(build_extension_wheels, "download_extension", side_effect=self._download_with_wrong_platform_for("osx-amd64")), pytest.raises(SystemExit) as excinfo:
            build_extension_wheels.main(["--version", "2.0.0", "--extension", "fts", "--out-dir", str(tmp_path)])
        assert "fts osx-amd64" in str(excinfo.value)
        assert len(os.listdir(tmp_path)) == 7


def _stand_in_cli(tmp_path, mode):
    """A stand-in for the DuckDB CLI that logs its arguments and answers the check's query: the extension loaded
    from the first extension directory when the file is there (mode "pass"), or from elsewhere (mode "elsewhere")."""
    script = tmp_path / "duckdb"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, re, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['STAND_IN_CLI_LOG'], 'a') as f:\n"
        "    f.write(json.dumps({'args': args, 'home_has_duckdb': os.path.isdir(os.path.join(os.environ['HOME'], '.duckdb'))}) + '\\n')\n"
        "setup = args[args.index('-cmd') + 1]\n"
        "ext_dir = re.search(r\"extension_directories=\\['(.*?)'\\]\", setup).group(1)\n"
        "name = re.search(r'LOAD (\\w+);', args[args.index('-c') + 1]).group(1)\n"
        "found = [os.path.join(r, f) for r, _, fs in os.walk(ext_dir) for f in fs if f == name + '.duckdb_extension']\n"
        f"path = found[0] if found and {mode!r} == 'pass' else '/elsewhere/' + name + '.duckdb_extension'\n"
        "print('extension_name,loaded,install_path')\n"
        "print(name + ',true,' + path)\n"
    )
    script.chmod(0o755)
    return str(script)


@pytest.mark.skipif(sys.platform == "win32" or host_server_platform() is None, reason="the stand-in CLI is a script with a shebang line")
class TestCheck:
    """Without a network: a real venv and pip, a stand-in CLI."""

    def _wheel(self, tmp_path, platform=None):
        binary = _binary(tmp_path, platform=platform or host_server_platform())
        return tool.write_wheel(tool.inspect_extension(binary, core=True), _metadata(), str(tmp_path / "dist"))

    def test_reads_what_the_wheel_installs(self, tmp_path):
        found = tool_check.read_wheel(self._wheel(tmp_path, "osx_amd64"))
        assert (found.name, found.duckdb_version, found.platform) == ("geo", "2.0.0", "osx-amd64")

    def test_a_wheel_without_an_extension_is_refused(self, tmp_path):
        path = tmp_path / "other-1-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("other/__init__.py", "")
        with pytest.raises(tool.WheelError):
            tool_check.read_wheel(str(path))

    def test_pass_loads_from_the_venv_directory_only(self, tmp_path, monkeypatch, capsys):
        log = tmp_path / "cli.log"
        monkeypatch.setenv("STAND_IN_CLI_LOG", str(log))
        status = tool_main(["check", self._wheel(tmp_path), "--no-deps", "--cli", _stand_in_cli(tmp_path, "pass")])
        assert status == 0, capsys.readouterr()
        call = json.loads(log.read_text().splitlines()[-1])
        setup = call["args"][call["args"].index("-cmd") + 1]
        assert "SET autoinstall_known_extensions=false" in setup
        assert setup.count("extension_directories=[") == 1 and setup.split("extension_directories=['")[1].split("'")[0].endswith(".duckdb_extensions")
        assert "-unsigned" not in call["args"]
        assert call["home_has_duckdb"] is True

    def test_a_load_from_elsewhere_fails(self, tmp_path, monkeypatch, capsys):
        log = tmp_path / "cli.log"
        monkeypatch.setenv("STAND_IN_CLI_LOG", str(log))
        assert tool_main(["check", self._wheel(tmp_path), "--no-deps", "--cli", _stand_in_cli(tmp_path, "elsewhere")]) == 1
        assert log.exists()
        assert "/elsewhere/geo.duckdb_extension" in capsys.readouterr().err

    def test_unsigned_reaches_the_cli(self, tmp_path, monkeypatch):
        log = tmp_path / "cli.log"
        monkeypatch.setenv("STAND_IN_CLI_LOG", str(log))
        tool_main(["check", self._wheel(tmp_path), "--no-deps", "--unsigned", "--cli", _stand_in_cli(tmp_path, "pass")])
        assert "-unsigned" in json.loads(log.read_text().splitlines()[-1])["args"]

    def test_a_wheel_for_another_platform_fails_at_pip(self, tmp_path, monkeypatch, capsys):
        log = tmp_path / "cli.log"
        monkeypatch.setenv("STAND_IN_CLI_LOG", str(log))
        other = "windows_arm64" if host_server_platform() != "windows_arm64" else "linux_amd64"
        assert tool_main(["check", self._wheel(tmp_path, other), "--no-deps", "--cli", _stand_in_cli(tmp_path, "pass")]) == 1
        assert "pip" in capsys.readouterr().err
        assert not log.exists()


class TestVersionFloor:
    def test_binary_for_duckdb_before_1_5_is_refused(self):
        from duckdb_extension_wheel import WheelError, extension_from_bytes
        from tests.extension_binaries import synthetic_binary

        with pytest.raises(WheelError, match="1.4.4 is older than 1.5.0"):
            extension_from_bytes("tpch", synthetic_binary("tpch", "linux_amd64", "v1.4.4"))

    def test_c_api_binary_with_an_old_duckdb_version_is_refused(self):
        from duckdb_extension_wheel import WheelError, extension_from_bytes
        from tests.extension_binaries import synthetic_binary

        with pytest.raises(WheelError, match="older than 1.5.0"):
            extension_from_bytes("demo_capi", synthetic_binary("demo_capi", "linux_amd64", "v1.2.0", "C_STRUCT"), duckdb_version="1.4.4")

    def test_1_5_0_is_accepted(self):
        from duckdb_extension_wheel import extension_from_bytes
        from tests.extension_binaries import synthetic_binary

        assert extension_from_bytes("tpch", synthetic_binary("tpch", "linux_amd64", "v1.5.0")).duckdb_version == "1.5.0"


class TestMetadataEdges:
    def test_none_for_a_list_field_means_empty(self):
        from duckdb_extension_wheel import WheelMetadata

        md = WheelMetadata(name="acme-geo", version="1.0", requires=None, project_urls=None, keywords=None)
        assert md.requires == () and md.project_urls == () and md.keywords == ()

    def test_a_python_keyword_is_not_a_project_name(self):
        from duckdb_extension_wheel import WheelError, WheelMetadata

        with pytest.raises(WheelError, match="importable"):
            WheelMetadata(name="class", version="1.0").validate()


class TestSymbolSearch:
    def test_whole_symbol_only(self):
        from duckdb_extension_wheel.binary import _contains_symbol

        assert _contains_symbol(b"\0tpch_duckdb_cpp_init\0", b"tpch_duckdb_cpp_init", allow_v2=False)
        assert _contains_symbol(b"\0_tpch_duckdb_cpp_init\0", b"tpch_duckdb_cpp_init", allow_v2=False), "Mach-O underscore"
        assert not _contains_symbol(b"\0xtpch_duckdb_cpp_init\0", b"tpch_duckdb_cpp_init", allow_v2=False)
        assert not _contains_symbol(b"\0tpch_duckdb_cpp_init2\0", b"tpch_duckdb_cpp_init", allow_v2=False)
        assert _contains_symbol(b"\0odbc_init_c_api_v2\0", b"odbc_init_c_api", allow_v2=True)
        assert not _contains_symbol(b"\0odbc_init_c_api_v2\0", b"odbc_init_c_api", allow_v2=False)
        assert _contains_symbol(b"tpch_duckdb_cpp_init", b"tpch_duckdb_cpp_init", allow_v2=False), "at the very start"


class TestInstallInformation:
    """A community extension needs the .info file DuckDB wrote beside it; a core extension says so and needs none."""

    def _binary(self, tmp_path, name="h3"):
        from tests.extension_binaries import synthetic_binary

        tmp_path.mkdir(parents=True, exist_ok=True)
        path = tmp_path / f"{name}.duckdb_extension"
        path.write_bytes(synthetic_binary(name, "linux_amd64", "v2.0.0"))
        return path

    def test_without_info_and_without_core_flag_is_an_error(self, tmp_path):
        from duckdb_extension_wheel import WheelError, inspect_extension

        with pytest.raises(WheelError, match="no install information"):
            inspect_extension(str(self._binary(tmp_path)))

    def test_info_beside_the_binary_is_found_and_shipped(self, tmp_path):
        from duckdb_extension_wheel import WheelMetadata, inspect_extension, write_wheel

        binary = self._binary(tmp_path)
        (tmp_path / "h3.duckdb_extension.info").write_bytes(b"install-info-bytes")
        ext = inspect_extension(str(binary))
        assert ext.info == b"install-info-bytes"
        whl = write_wheel(ext, WheelMetadata(name="acme-h3", version="1.0"), str(tmp_path / "dist"))
        with zipfile.ZipFile(whl) as zf:
            names = zf.namelist()
            binary_member = "acme_h3-1.0.data/platlib/.duckdb_extensions/v2.0.0/linux_amd64/h3.duckdb_extension"
            assert binary_member in names and binary_member + ".info" in names
            assert zf.read(binary_member + ".info") == b"install-info-bytes"
            record = zf.read("acme_h3-1.0.dist-info/RECORD").decode()
            assert binary_member + ".info," in record

    def test_info_flag_names_the_file(self, tmp_path):
        from duckdb_extension_wheel import inspect_extension

        binary = self._binary(tmp_path)
        info = tmp_path / "elsewhere.info"
        info.write_bytes(b"given")
        assert inspect_extension(str(binary), info_path=str(info)).info == b"given"

    def test_empty_info_is_an_error(self, tmp_path):
        from duckdb_extension_wheel import WheelError, inspect_extension

        binary = self._binary(tmp_path)
        (tmp_path / "h3.duckdb_extension.info").write_bytes(b"")
        with pytest.raises(WheelError, match="empty"):
            inspect_extension(str(binary))

    def test_core_extension_ships_no_info_and_keeps_the_layout(self, tmp_path):
        from duckdb_extension_wheel import WheelMetadata, inspect_extension, write_wheel

        ext = inspect_extension(str(self._binary(tmp_path, "tpch")), core=True)
        assert ext.info is None
        whl = write_wheel(ext, WheelMetadata(name="duckdb-core-ext-tpch", version="2.0.0"), str(tmp_path / "dist"))
        with zipfile.ZipFile(whl) as zf:
            assert not any(n.endswith(".info") for n in zf.namelist())

    def test_info_flag_with_several_binaries_is_an_error(self, tmp_path):
        from duckdb_extension_wheel import WheelError, WheelMetadata, build_wheels

        a = self._binary(tmp_path / "a")
        b = self._binary(tmp_path / "b")
        info = tmp_path / "x.info"
        info.write_bytes(b"i")
        with pytest.raises(WheelError, match="one binary"):
            build_wheels([str(a), str(b)], WheelMetadata(name="acme-h3", version="1.0"), str(tmp_path / "dist"), info_path=str(info))

    def test_command_line_build_with_info_beside_the_binary(self, tmp_path, capsys):
        from duckdb_extension_wheel.cli import main as tool_main

        binary = self._binary(tmp_path)
        (tmp_path / "h3.duckdb_extension.info").write_bytes(b"i")
        assert tool_main(["build", "--name", "acme-h3", "--version", "1.0", "--out-dir", str(tmp_path / "dist"), str(binary)]) == 0
        assert tool_main(["build", "--name", "acme-h3", "--version", "1.1", "--out-dir", str(tmp_path / "dist"), str(self._binary(tmp_path / "other"))]) == 1
        assert "no install information" in capsys.readouterr().err
