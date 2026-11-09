"""Conformance check: install a wheel and load it in the matching DuckDB CLI."""

import http.client
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from dataclasses import dataclass

from .binary import PIP_PLATFORMS, PLATFORMS, WheelError
from .net import get_bytes

_RELEASES = "https://github.com/duckdb/duckdb/releases/download"


@dataclass(frozen=True)
class WheelInfo:
    name: str
    duckdb_version: str
    platform: str


def cli_asset_urls(version, platform):
    """Candidate URLs of the CLI archive for the release, newest layout first (2.0.0 renamed the assets)."""
    base, musl = platform.rsplit("-musl", 1)[0], platform.endswith("-musl")
    suffix = "-musl" if musl else ""
    return [
        f"{_RELEASES}/v{version}/duckdb-cli-{base}{suffix}.tar.gz",
        f"{_RELEASES}/v{version}/duckdb_cli-{base}{suffix}.zip",
    ]


def _cli_member(names):
    """The archive member that is the CLI binary, or None."""
    for name in names:
        if os.path.basename(name) in ("duckdb", "duckdb.exe"):
            return name
    return None


def download_cli(version, platform, out_dir):
    """The DuckDB CLI binary of the version for the platform, extracted into out_dir."""
    for url in cli_asset_urls(version, platform):
        data = get_bytes(url)
        if data is None:
            continue
        if url.endswith(".tar.gz"):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                member = _cli_member(m.name for m in tf.getmembers() if m.isfile())
                if member is None:
                    raise RuntimeError(f"{url} holds no duckdb binary")
                with tf.extractfile(member) as f:
                    content = f.read()
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                member = _cli_member(i.filename for i in zf.infolist() if not i.is_dir())
                if member is None:
                    raise RuntimeError(f"{url} holds no duckdb binary")
                content = zf.read(member)
        dest = os.path.join(out_dir, os.path.basename(member))
        with open(dest, "wb") as f:
            f.write(content)
        os.chmod(dest, 0o755)
        return dest
    raise RuntimeError(f"no CLI release asset for DuckDB {version} on {platform}")


def site_packages(env_dir):
    python = os.path.join(env_dir, "Scripts" if sys.platform == "win32" else "bin", "python" + (".exe" if sys.platform == "win32" else ""))
    out = subprocess.run([python, "-c", "import sysconfig; print(sysconfig.get_paths()['platlib'])"], check=True, capture_output=True, text=True)
    return python, out.stdout.strip()


def cli_home(tmp):
    """A home directory for the CLI with the .duckdb directory some extensions expect at load time."""
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".duckdb"), exist_ok=True)
    return home


def verdict(returncode, stdout, name, ext_dir):
    """Whether the CLI's answer shows the extension loaded from ext_dir and not from anywhere else."""
    return returncode == 0 and f"{name},true," in stdout and ext_dir in stdout


def load_in_cli(cli, ext_dir, name, home, unsigned=False):
    """Run LOAD in the CLI with ext_dir as the only extension directory and no autoinstall; returns (ok, detail).

    An output mode flag such as -csv also keeps the 2.0 CLI from printing its agent-mode notice."""
    escaped = ext_dir.replace("'", "''")
    setup = f"SET autoinstall_known_extensions=false; SET extension_directories=['{escaped}'];"
    query = f"LOAD {name}; SELECT extension_name, loaded, install_path FROM duckdb_extensions() WHERE extension_name = '{name}';"
    env = dict(os.environ, HOME=home, USERPROFILE=home)
    args = [cli, "-csv", "-cmd", setup, "-c", query]
    if unsigned:
        args.insert(1, "-unsigned")
    proc = subprocess.run(args, capture_output=True, text=True, timeout=300, env=env)
    detail = (proc.stdout + proc.stderr).strip()
    return verdict(proc.returncode, proc.stdout, name, ext_dir), detail


def read_wheel(path):
    """The extension name, DuckDB version and pip platform from the wheel's single extension binary."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            pattern = r'[^/]+\.data/platlib/\.duckdb_extensions/v([0-9]+\.[0-9]+\.[0-9]+)/([a-z0-9_]+)/([a-z0-9_]+)\.duckdb_extension'
            matches = [re.fullmatch(pattern, n) for n in zf.namelist()]
            matches = [m for m in matches if m is not None]

            if len(matches) != 1:
                raise WheelError(f"wheel must contain exactly one extension binary, found {len(matches)}")

            m = matches[0]
            duckdb_version = m.group(1)
            server_platform = m.group(2)
            name = m.group(3)

            pip_platform = PIP_PLATFORMS.get(server_platform)
            if pip_platform is None:
                raise WheelError(f"unknown server platform: {server_platform}")

            return WheelInfo(name, duckdb_version, pip_platform)
    except (zipfile.BadZipFile, OSError) as e:
        raise WheelError(f"cannot read {path}: {e}") from None


def check_wheel(path, cli=None, pip_args=(), unsigned=False):
    """Install the wheel into a fresh venv and load it in the CLI; returns (ok, detail)."""
    info = read_wheel(path)

    with tempfile.TemporaryDirectory() as tmp:
        try:
            env_dir = os.path.join(tmp, "venv")
            venv.create(env_dir, with_pip=True)
            python, platlib = site_packages(env_dir)
        except (subprocess.CalledProcessError, OSError) as e:
            return False, f"venv: {e}"

        pip_cmd = [python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *pip_args, path]

        try:
            proc = subprocess.run(pip_cmd, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired as e:
            return False, f"pip: timeout after {e.timeout}s"

        if proc.returncode != 0:
            return False, "pip: " + (proc.stdout + proc.stderr).strip()

        ext_dir = os.path.join(platlib, ".duckdb_extensions")
        expected = os.path.join(ext_dir, f"v{info.duckdb_version}", PLATFORMS[info.platform][0], f"{info.name}.duckdb_extension")

        if not os.path.isfile(expected):
            return False, f"missing {expected}"

        try:
            if cli is None:
                cli = download_cli(info.duckdb_version, info.platform, tmp)
        except (RuntimeError, OSError, EOFError, http.client.HTTPException, tarfile.TarError, zipfile.BadZipFile) as e:
            return False, f"cli: {e}"

        home = cli_home(tmp)
        try:
            return load_in_cli(cli, ext_dir, info.name, home, unsigned=unsigned)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"cli: {e}"
