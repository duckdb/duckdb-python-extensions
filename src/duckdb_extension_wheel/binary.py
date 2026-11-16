"""Read DuckDB extension binaries: footer, platform, version, ABI type."""

import os
import re
from dataclasses import dataclass


class WheelError(ValueError):
    pass


PLATFORMS = {
    "linux-amd64": ("linux_amd64", "manylinux_2_17_x86_64.manylinux2014_x86_64"),
    "linux-amd64-musl": ("linux_amd64_musl", "musllinux_1_2_x86_64"),
    "linux-arm64": ("linux_arm64", "manylinux_2_17_aarch64.manylinux2014_aarch64"),
    "linux-arm64-musl": ("linux_arm64_musl", "musllinux_1_2_aarch64"),
    "osx-amd64": ("osx_amd64", "macosx_12_0_x86_64"),
    "osx-arm64": ("osx_arm64", "macosx_12_0_arm64"),
    "windows-amd64": ("windows_amd64", "win_amd64"),
    "windows-arm64": ("windows_arm64", "win_arm64"),
}

PIP_PLATFORMS = {server: pip for pip, (server, _) in PLATFORMS.items()}


@dataclass(frozen=True)
class Footer:
    platform: str
    version: str
    extension_version: str
    abi_type: str


def read_footer(data):
    if len(data) < 512:
        raise WheelError("not a DuckDB extension: file is shorter than 512 bytes")

    footer_bytes = data[-512:]
    metadata = footer_bytes[:256]

    fields = [metadata[i * 32:(i + 1) * 32].rstrip(b"\0").decode("latin-1") for i in range(8)]
    fields = list(reversed(fields))

    magic = fields[0]
    if magic != "4":
        raise WheelError("not a DuckDB extension: magic value is not 4")

    platform = fields[1]
    version = fields[2]
    extension_version = fields[3]
    abi_field = fields[4]

    if abi_field == "C_STRUCT":
        abi_type = "C_STRUCT"
    elif abi_field == "C_STRUCT_UNSTABLE":
        abi_type = "C_STRUCT_UNSTABLE"
    elif abi_field == "CPP" or abi_field == "":
        abi_type = "CPP"
    else:
        raise WheelError(f"unknown ABI type: {abi_field}")

    return Footer(platform, version, extension_version, abi_type)


@dataclass(frozen=True)
class Extension:
    name: str
    platform: str
    duckdb_version: str
    abi_type: str
    data: bytes
    # The install information DuckDB writes beside an extension it installs from a repository; a load of a
    # community extension needs it to trust the community keys. None for a core extension.
    info: bytes = None


# DuckDB gained the extension_directories setting in 1.5.0; before that no wrapper can make it search the
# shared directory, so a wheel for an older version would install a file nothing finds.
MINIMUM_DUCKDB_VERSION = (1, 5, 0)


def _release(duckdb_version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", duckdb_version):
        raise WheelError(f"DuckDB version is not a release version: {duckdb_version}")
    if tuple(int(part) for part in duckdb_version.split(".")) < MINIMUM_DUCKDB_VERSION:
        floor = ".".join(str(part) for part in MINIMUM_DUCKDB_VERSION)
        raise WheelError(f"DuckDB {duckdb_version} is older than {floor}, the first version that searches the shared extension directory")
    return duckdb_version


def _contains_symbol(data, entry, allow_v2):
    """Whether the binary contains the entry point as a whole symbol, with the leading underscore Mach-O adds
    and the _v2 suffix of the second C API allowed."""
    names = [entry] + ([entry + b"_v2"] if allow_v2 else [])
    for name in names:
        start = 0
        while True:
            at = data.find(name, start)
            if at < 0:
                break
            before = data[at - 1 : at]
            after = data[at + len(name) : at + len(name) + 1]
            if before == b"_":
                before = data[at - 2 : at - 1]
            if not _symbol_char(before) and not _symbol_char(after):
                return True
            start = at + 1
    return False


_SYMBOL_CHARS = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")


def _symbol_char(byte):
    return len(byte) == 1 and byte[0] in _SYMBOL_CHARS


def extension_from_bytes(name, data, duckdb_version=None, info=None):
    if not name or not re.fullmatch(r"[a-z0-9_]+", name):
        raise WheelError(f"extension name must be lowercase letters, digits and underscores: {name}")

    footer = read_footer(data)

    if footer.abi_type in ("CPP", "C_STRUCT_UNSTABLE"):
        if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", footer.version):
            raise WheelError(f"footer version is not a release version: {footer.version}")

    if footer.platform not in PIP_PLATFORMS:
        raise WheelError(f"no wheel tag for platform: {footer.platform}")

    pip_platform = PIP_PLATFORMS[footer.platform]

    if footer.abi_type == "C_STRUCT":
        if duckdb_version is None:
            raise WheelError(f"built against the DuckDB C API {footer.version}, which does not say which DuckDB version it is for; give it with --duckdb-version")
        version = _release(duckdb_version)
    elif footer.abi_type in ("CPP", "C_STRUCT_UNSTABLE"):
        version = _release(footer.version[1:])
        if duckdb_version is not None:
            if _release(duckdb_version) != version:
                raise WheelError(f"given DuckDB version {duckdb_version} does not match footer version {footer.version}")

    entry = f"{name}_duckdb_cpp_init" if footer.abi_type == "CPP" else f"{name}_init_c_api"
    if not _contains_symbol(data, entry.encode("ascii"), allow_v2=footer.abi_type != "CPP"):
        raise WheelError(f"does not contain entry point: {entry}")

    return Extension(name, pip_platform, version, footer.abi_type, data, info)


def _read_info(path, info_path, core):
    """The install information for the binary at path: the file given, else <path>.info beside it, else none
    for a core extension, else an error, since DuckDB 2.0 refuses a community extension without it."""
    if info_path is None and os.path.isfile(path + ".info"):
        info_path = path + ".info"
    if info_path is None:
        if core:
            return None
        raise WheelError(
            f"{path}: no install information: put the {os.path.basename(path)}.info that INSTALL wrote beside "
            "the binary, pass it with --info, or say --core-extension for a binary signed with a DuckDB core key"
        )
    try:
        with open(info_path, "rb") as f:
            info = f.read()
    except (IOError, OSError) as e:
        raise WheelError(f"{info_path}: cannot read: {e}")
    if not info:
        raise WheelError(f"{info_path}: install information is empty")
    return info


def inspect_extension(path, duckdb_version=None, info_path=None, core=False):
    file_name = os.path.basename(path)
    if not file_name.endswith(".duckdb_extension"):
        raise WheelError(f"{path}: file name does not end with .duckdb_extension")

    name = file_name[:-len(".duckdb_extension")]

    try:
        with open(path, "rb") as f:
            data = f.read()
    except (IOError, OSError) as e:
        raise WheelError(f"{path}: cannot read: {e}")

    info = _read_info(path, info_path, core)

    try:
        return extension_from_bytes(name, data, duckdb_version=duckdb_version, info=info)
    except WheelError as e:
        raise WheelError(f"{path}: {e}") from None
