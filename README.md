# duckdb-python-extensions

Publishes every DuckDB core extension that the CLI loads from a file as a PyPI project, `duckdb-core-ext-<name>`, with one wheel per platform and one release per DuckDB version. Nothing is compiled here: each wheel repackages the binary that DuckDB itself downloads from extensions.duckdb.org, placed at

    site-packages/.duckdb_extensions/v<version>/<platform>/<name>.duckdb_extension

That directory has the layout of a DuckDB extension directory and of a DuckDB extension repository, so `duckdb-cli` and the `duckdb` Python module can search it and install from it without a network. An extension is built for one DuckDB version; install the release that matches.

    pip install duckdb-cli[httpfs]==2.0.0

## How publishing works

One workflow, `.github/workflows/publish.yml`, runs every night and by hand, with no human step. It plans, stages, tests and promotes:

1. `scripts/plan_wheels.py` compares the stable DuckDB releases at or above the floor in the workflow, the files extensions.duckdb.org serves per extension and platform, and the wheel files already on pypi.org. Every wheel the server has and pypi.org lacks becomes one item per (version, extension).
2. Each item builds its wheels once with `scripts/build_extension_wheels.py`, as a production wheel and a staging copy of the same bytes under `<version>.dev<run id>`. The staging copies go to test.pypi; the production wheels stay with the run.
3. On every platform the plan touches, `scripts/test_installed.py` installs each staging wheel from test.pypi into its own target, together with the released wheels its LOAD needs from pypi.org, downloads the DuckDB CLI of that version from the GitHub release, and loads each extension with the environment's directory as the only extension directory and autoinstall off.
4. Items that passed on every platform they have are uploaded to pypi.org from the run's artifacts. A published wheel is never replaced. An item that failed somewhere is reported and planned again the next night, up to five times; after that it is listed as stalled until a run started by hand with a version or extension input. An extension whose LOAD needs another one (iceberg needs avro) waits until that one is on pypi.org. A platform that appears more than 14 days after a release's first upload is listed as late, because PyPI refuses new files for such a release.

Both uploads authenticate with trusted publishing: every project on test.pypi has a publisher for this repository, workflow `publish.yml` and environment `pypi-test`, and every project on pypi.org one for environment `pypi`. Neither environment requires reviewers. By hand, the `promote` input decides whether step 4 runs.

The list of extensions, their PyPI project names, their aliases and the platform mapping live in `src/duckdb_extensions/registry.py`; the planner in `src/duckdb_extensions/plan.py`; the requests with their retry policy in `src/duckdb_extensions/net.py`.

## Development

    uv run --no-project --with pytest python -m pytest tests
    uv run --no-project scripts/plan_wheels.py --minimum 1.5.0 --version 2.0.0 --extension httpfs
    uv run --no-project scripts/build_extension_wheels.py --version 2.0.0 --extension httpfs --platform linux-arm64 --out-dir dist --stage-version 2.0.0.dev1 --stage-dir stage
    uv run --no-project scripts/test_installed.py --platform linux-arm64 --version 2.0.0 --wheel-version 2.0.0.dev1 --extension httpfs --index stage --results results.json
