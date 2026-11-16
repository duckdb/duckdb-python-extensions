# duckdb-python-extensions

Publishes every DuckDB core extension that the CLI loads from a file as a PyPI project, `duckdb-core-ext-<name>`, with one wheel per platform and one release per DuckDB version. Nothing is compiled here: each wheel repackages the binary that DuckDB itself downloads from extensions.duckdb.org, placed at

    site-packages/.duckdb_extensions/v<version>/<platform>/<name>.duckdb_extension

That directory has the layout of a DuckDB extension directory and of a DuckDB extension repository, so `duckdb-cli` and the `duckdb` Python module can search it and install from it without a network. An extension is built for one DuckDB version; install the release that matches.

    pip install duckdb-cli[httpfs]==2.0.0

## The wheel tool

The wheels are written by `duckdb-extension-wheel`, a command and Python package in `src/duckdb_extension_wheel/` that any extension author can use for their own extension: it reads the platform and DuckDB version from the binary's metadata, writes one wheel per platform in the layout above, and checks that a wheel loads in the matching DuckDB CLI. [PROTOCOL.md](PROTOCOL.md) describes the wheels and the tool for authors. This repository's own pipeline calls the same code.

## How publishing works

One workflow, `.github/workflows/publish.yml`, runs every night and by hand, with no human step. It plans, stages, tests and promotes:

1. `scripts/plan_wheels.py` compares the stable DuckDB releases at or above the floor in the workflow, the files extensions.duckdb.org serves per extension and platform, and the wheel files already on pypi.org. Every wheel the server has and pypi.org lacks becomes one item per (version, extension).
2. Each item builds its wheels once with `scripts/build_extension_wheels.py`, as a production wheel and a staging copy of the same bytes under `<version>.dev<run id>`. The staging copies go to test.pypi; the production wheels stay with the run.
3. On every platform the plan touches, `scripts/test_installed.py` installs each staging wheel from test.pypi into its own target, together with the released wheels its LOAD needs from pypi.org, downloads the DuckDB CLI of that version from the GitHub release, and loads each extension with the environment's directory as the only extension directory and autoinstall off.
4. Items that passed on every platform they have are uploaded to pypi.org from the run's artifacts. A published wheel is never replaced. An item that failed somewhere is listed as failed on every following night and never restaged by the nightly: a real failure is a signal to look at, and only a run started by hand with a version or extension input stages it again. Within one run, installs from test.pypi and the staging upload are retried a few times, so a slow index or a dropped connection does not count as a failure. An extension whose LOAD needs another one (iceberg needs avro) waits until that one is on pypi.org. A platform that appears more than 14 days after a release's first upload is listed as late, because PyPI refuses new files for such a release.

Both uploads authenticate with trusted publishing: every project on test.pypi has a publisher for this repository, workflow `publish.yml` and environment `pypi-test`, and every project on pypi.org one for environment `pypi`. Neither environment requires reviewers. By hand, the `promote` input decides whether step 4 runs.

The list of extensions, their PyPI project names and their aliases live in `src/duckdb_extensions/registry.py`; the planner in `src/duckdb_extensions/plan.py`; the platform mapping in `src/duckdb_extension_wheel/binary.py` and the requests with their retry policy in `src/duckdb_extension_wheel/net.py`.

## New core extensions

The registry in `src/duckdb_extensions/registry.py` is the list of what gets published. To catch what it misses, the nightly plan and the status page list every extension that DuckDB's own build configuration names for a release (`.github/config/extensions/` in duckdb/duckdb at the release tag) and that extensions.duckdb.org serves, but that the registry lacks. Such an extension gets wheels once its PyPI projects and publishers exist and a registry line maps its project name to its server name.

## Status page

`.github/workflows/status.yml` publishes, on GitHub Pages, which wheels exist per DuckDB version and platform: after every publish run, daily, and by hand. `scripts/status_page.py` gathers the state from extensions.duckdb.org, pypi.org and test.pypi.org and writes `index.html` and `status.json`. A cell is published (on pypi.org), staged (a staging wheel on test.pypi, not yet promoted), waiting (a required extension is not published for that platform), late (pypi.org no longer accepts files for that release), available (served, not yet built) or absent (not served). The page also lists the duckdb-cli wheels per version and platform and the `duckdb` module's wheels per Python tag and platform, since the extension wheels themselves are `py3-none` and independent of the Python version. Pages must be set to deploy from GitHub Actions in the repository settings.

## Releasing the tool

A tag `v<version>` that matches the version in `pyproject.toml` runs `.github/workflows/release.yml`, which tests, builds and publishes `duckdb-extension-wheel` to pypi.org through trusted publishing (publisher: this repository, `release.yml`, environment `pypi`).

## Development

    uv run --no-project --with pytest python -m pytest tests
    DUCKDB_EXTENSION_WHEEL_NETWORK=1 uv run --no-project --with pytest python -m pytest tests/test_extension_wheel_network.py
    uv build
    uv run --no-project scripts/plan_wheels.py --minimum 1.5.0 --version 2.0.0 --extension httpfs
    uv run --no-project scripts/build_extension_wheels.py --version 2.0.0 --extension httpfs --platform linux-arm64 --out-dir dist --stage-version 2.0.0.dev1 --stage-dir stage
    uv run --no-project scripts/test_installed.py --platform linux-arm64 --version 2.0.0 --wheel-version 2.0.0.dev1 --extension httpfs --index stage --results results.json
    uv run --no-project scripts/status_page.py --minimum 1.5.0 --out site
