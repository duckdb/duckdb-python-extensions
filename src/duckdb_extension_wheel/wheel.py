"""Write pip wheels from DuckDB extension binaries."""

import base64
import csv
import hashlib
import io
import os
import keyword
import re
import stat
import zipfile
from dataclasses import dataclass

from .binary import PLATFORMS, WheelError, inspect_extension

REPRODUCIBLE_DATE = (1980, 1, 1, 0, 0, 0)


def record_hash(data):
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def dist_name(project_name):
    return re.sub(r"[-_.]+", "_", project_name).lower()


def wheel_filename(project_name, version, platform):
    return f"{dist_name(project_name)}-{version}-py3-none-{PLATFORMS[platform][1]}.whl"


@dataclass(frozen=True)
class WheelMetadata:
    name: str
    version: str
    requires: tuple = ()
    summary: str = None
    description: str = None
    description_content_type: str = None
    license_expression: str = None
    project_urls: tuple = ()
    requires_python: str = ">=3.8"
    keywords: tuple = ()
    # Further "key: value" lines for the Provenance section, after the binary's own extension_version and sha256.
    provenance: tuple = ()

    def __post_init__(self):
        if isinstance(self.requires, str):
            raise WheelError("requires must be a list of strings, not one string")
        if isinstance(self.project_urls, str):
            raise WheelError("project_urls must be a list of strings, not one string")
        if isinstance(self.keywords, str):
            raise WheelError("keywords must be a list of strings, not one string")
        object.__setattr__(self, "provenance", tuple(tuple(item) for item in (self.provenance or ())))
        object.__setattr__(self, "requires", tuple(self.requires or ()))
        object.__setattr__(self, "project_urls", tuple(self.project_urls or ()))
        object.__setattr__(self, "keywords", tuple(self.keywords or ()))

    def validate(self):
        if not re.fullmatch(r"([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9])", self.name):
            raise WheelError(f"name is not a valid project name: {self.name}")

        dist = dist_name(self.name)
        if not dist.isidentifier() or keyword.iskeyword(dist):
            raise WheelError(f"name does not produce an importable Python module name: {self.name}")

        if not re.fullmatch(r"([1-9][0-9]*!)?(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*((a|b|rc)(0|[1-9][0-9]*))?(\.post(0|[1-9][0-9]*))?(\.dev(0|[1-9][0-9]*))?", self.version):
            raise WheelError(f"version is not canonical: {self.version}")

        for field_name, field_value in [
            ("name", self.name),
            ("version", self.version),
            ("summary", self.summary),
            ("license_expression", self.license_expression),
            ("requires_python", self.requires_python),
            ("description_content_type", self.description_content_type),
        ]:
            if field_value is not None and ("\n" in field_value or "\r" in field_value):
                raise WheelError(f"{field_name} contains a line break: {field_value!r}")

        for req in self.requires:
            if not req:
                raise WheelError("empty requirement")
            if "\n" in req or "\r" in req:
                raise WheelError(f"requirement contains a line break: {req!r}")

        for url in self.project_urls:
            if "\n" in url or "\r" in url:
                raise WheelError(f"project URL contains a line break: {url!r}")
            if ", " not in url:
                raise WheelError(f"project URL must be 'Label, URL': {url}")

        for kw in self.keywords:
            if "\n" in kw or "\r" in kw:
                raise WheelError(f"keyword contains a line break: {kw!r}")
            if "," in kw:
                raise WheelError(f"keyword contains a comma: {kw}")


PROVENANCE_HEADING = "## Provenance"


def provenance_section(extension, extra=()):
    """The Markdown that records which binary a wheel carries: the footer's extension_version, the sha256 of
    the binary, then the caller's lines, each "- key: value"."""
    items = [("extension_version", extension.extension_version), ("sha256", hashlib.sha256(extension.data).hexdigest())]
    items.extend(extra)
    for key, value in items:
        if not re.fullmatch(r"[a-z0-9_]+", key) or "\n" in str(value):
            raise WheelError(f"provenance line is not 'key: value' on one line: {key!r}")
    return PROVENANCE_HEADING + "\n\n" + "\n".join(f"- {key}: {value}" for key, value in items) + "\n"


def read_provenance(metadata_text):
    """The provenance recorded in a METADATA file's description as a dict, or None when it has no such section."""
    _, sep, body = metadata_text.partition("\n\n")
    if not sep:
        return None
    found = None
    for line in body.splitlines():
        if line.rstrip() == PROVENANCE_HEADING:
            found = {}
            continue
        if found is None:
            continue
        if line.startswith("- ") and ": " in line:
            key, _, value = line[2:].partition(": ")
            found[key.strip()] = value.strip()
        elif line.strip() == "" and not found:
            continue
        elif line.startswith("#"):
            break
    return found


def _generate_metadata(metadata, provenance=None):
    lines = []

    if metadata.license_expression:
        lines.append("Metadata-Version: 2.4")
    else:
        lines.append("Metadata-Version: 2.1")

    lines.append(f"Name: {metadata.name}")
    lines.append(f"Version: {metadata.version}")

    if metadata.summary:
        lines.append(f"Summary: {metadata.summary}")

    if metadata.keywords:
        lines.append(f"Keywords: {','.join(metadata.keywords)}")

    if metadata.license_expression:
        lines.append(f"License-Expression: {metadata.license_expression}")

    if metadata.requires_python is not None:
        lines.append(f"Requires-Python: {metadata.requires_python}")

    for req in metadata.requires:
        lines.append(f"Requires-Dist: {req}")

    for url in metadata.project_urls:
        lines.append(f"Project-URL: {url}")

    description = metadata.description or ""
    if provenance:
        description = (description.rstrip("\n") + "\n\n" if description else "") + provenance
    if description:
        content_type = metadata.description_content_type or ("text/markdown" if not metadata.description else "text/plain")
        lines.append(f"Description-Content-Type: {content_type}")
        return "\n".join(lines) + "\n\n" + description
    else:
        return "\n".join(lines) + "\n"


def _generate_init_py(extension):
    server_platform = PLATFORMS[extension.platform][0]
    return (
        f'"""DuckDB {extension.name} extension, DuckDB {extension.duckdb_version}, {server_platform}."""\n'
        "\n"
        "import os\n"
        "\n"
        '_EXTENSIONS_DIR = ".duckdb_extensions"\n'
        "\n"
        "\n"
        "def get_extensions_dir():\n"
        '    """The shared extension directory in site-packages that DuckDB can search and install into."""\n'
        "    return os.path.join(os.path.dirname(os.path.dirname(__file__)), _EXTENSIONS_DIR)\n"
        "\n"
        "\n"
        "def get_extension_load_path():\n"
        '    """The extension binary, for LOAD by path."""\n'
        f'    return os.path.join(get_extensions_dir(), "v{extension.duckdb_version}", "{server_platform}", "{extension.name}.duckdb_extension")\n'
    )


def _zip_entry(zf, arcname, data, add_to_record=None):
    info = zipfile.ZipInfo(arcname, date_time=REPRODUCIBLE_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    zf.writestr(info, data)
    if add_to_record is not None:
        add_to_record.append((arcname, record_hash(data), str(len(data))))


def write_wheel(extension, metadata, out_dir):
    metadata.validate()

    dist = dist_name(metadata.name)
    wheel_tag = f"py3-none-{PLATFORMS[extension.platform][1]}"
    server_platform = PLATFORMS[extension.platform][0]
    wheel_path = os.path.join(out_dir, wheel_filename(metadata.name, metadata.version, extension.platform))
    partial_path = wheel_path + ".partial"

    dist_info = f"{dist}-{metadata.version}.dist-info"
    data_dir = f"{dist}-{metadata.version}.data"

    records = []

    os.makedirs(out_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(partial_path, "w", zipfile.ZIP_DEFLATED) as zf:
            _zip_entry(zf, f"{dist}/__init__.py", _generate_init_py(extension).encode("utf-8"), records)

            binary_path = f"{data_dir}/platlib/.duckdb_extensions/v{extension.duckdb_version}/{server_platform}/{extension.name}.duckdb_extension"
            _zip_entry(zf, binary_path, extension.data, records)
            if extension.info is not None:
                _zip_entry(zf, binary_path + ".info", extension.info, records)

            metadata_text = _generate_metadata(metadata, provenance_section(extension, metadata.provenance))
            _zip_entry(zf, f"{dist_info}/METADATA", metadata_text.encode("utf-8"), records)

            wheel_content = f"Wheel-Version: 1.0\nGenerator: duckdb-extension-build\nRoot-Is-Purelib: false\nTag: {wheel_tag}\n"
            _zip_entry(zf, f"{dist_info}/WHEEL", wheel_content.encode("utf-8"), records)

            _zip_entry(zf, f"{dist_info}/top_level.txt", f"{dist}\n".encode("utf-8"), records)

            record_buf = io.StringIO()
            writer = csv.writer(record_buf, lineterminator="\n")
            writer.writerows(records)
            writer.writerow([f"{dist_info}/RECORD", "", ""])

            _zip_entry(zf, f"{dist_info}/RECORD", record_buf.getvalue().encode("utf-8"))

        os.replace(partial_path, wheel_path)
    except Exception:
        if os.path.exists(partial_path):
            os.remove(partial_path)
        raise

    return wheel_path


def build_wheels(paths, metadata, out_dir, duckdb_version=None, info_path=None, core=False):
    if info_path is not None and len(paths) != 1:
        raise WheelError("--info names one file, so give one binary with it; with several binaries put each <name>.duckdb_extension.info beside its binary")
    extensions = []
    for path in paths:
        ext = inspect_extension(path, duckdb_version=duckdb_version, info_path=info_path, core=core)
        extensions.append(ext)

    if not extensions:
        return []

    first = extensions[0]
    platforms_seen = set()

    for ext in extensions:
        if ext.name != first.name:
            raise WheelError(f"binaries have different names: {first.name} and {ext.name}")

        if ext.duckdb_version != first.duckdb_version:
            raise WheelError(f"binaries have different DuckDB versions: {first.duckdb_version} and {ext.duckdb_version}")

        if ext.platform in platforms_seen:
            raise WheelError(f"duplicate platform: {ext.platform}")

        platforms_seen.add(ext.platform)

    wheels = []
    for ext in extensions:
        wheel_path = write_wheel(ext, metadata, out_dir)
        wheels.append(wheel_path)

    return wheels
