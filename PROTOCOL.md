# DuckDB extensions as Python wheels

This document is for authors of DuckDB extensions who want users to get their extension with `pip install`. It says what such a wheel contains, how to make one with the `duckdb-extension-wheel` command, what you decide yourself, what DuckDB does with the installed file, and what does not work yet.

The DuckDB core extensions are published this way by this repository, as the PyPI projects `duckdb-core-ext-<name>`. Their wheels follow the same rules as yours.

## What the wheel installs

A wheel holds one extension binary for one platform and one DuckDB version. Installing it puts the binary at

    site-packages/.duckdb_extensions/v<DuckDB version>/<DuckDB platform>/<name>.duckdb_extension

for example `site-packages/.duckdb_extensions/v2.0.0/linux_arm64/spatial.duckdb_extension`. This is the layout DuckDB uses for its own extension directory (by default `~/.duckdb/extensions`), so DuckDB finds the file when `site-packages/.duckdb_extensions` is one of its extension directories. Every extension wheel installs into the same `.duckdb_extensions` directory, so one directory serves all of them.

The wheel also installs a small Python module named after the project (`duckdb_core_ext_spatial` for the project `duckdb-core-ext-spatial`) with two functions: `get_extensions_dir()` returns the `.duckdb_extensions` directory, and `get_extension_load_path()` returns the path of the binary, for `LOAD '<path>'`.

The wheel is tagged `py3-none-<platform>`: it holds no Python code that depends on the interpreter version, so one wheel serves every Python from 3.8 on, and pip installs it only on the platform its binary was built for. The tag says what pip needs to know; what the binary itself needs from the system, such as the glibc version of a Linux build, is the extension author's to state.

| DuckDB platform    | Wheel platform tag                              |
|--------------------|-------------------------------------------------|
| `linux_amd64`      | `manylinux_2_17_x86_64.manylinux2014_x86_64`    |
| `linux_arm64`      | `manylinux_2_17_aarch64.manylinux2014_aarch64`  |
| `linux_amd64_musl` | `musllinux_1_2_x86_64`                          |
| `linux_arm64_musl` | `musllinux_1_2_aarch64`                         |
| `osx_amd64`        | `macosx_12_0_x86_64`                            |
| `osx_arm64`        | `macosx_12_0_arm64`                             |
| `windows_amd64`    | `win_amd64`                                     |
| `windows_arm64`    | `win_arm64`                                     |

There is no wheel for the WebAssembly platforms or for any other DuckDB platform: pip does not install into a browser, and the others have no matching wheel tag.

## Making the wheel

Install the tool and give it your binaries, one per platform:

    pip install duckdb-extension-wheel
    duckdb-extension-wheel build --name acme-duckdb-geo --version 0.3.1 \
        --requires "duckdb-cli==2.0.0" --summary "Geometry functions for DuckDB" \
        --description-file README.md --license MIT \
        --project-url "Source, https://github.com/acme/duckdb-geo" \
        --out-dir dist build/*/geo.duckdb_extension

It writes one wheel per binary into `dist` and prints their paths, ready for upload with `twine` or `uv publish`.

The tool reads from each binary what the binary says about itself. DuckDB writes a short block of metadata at the end of every extension binary it builds: the platform, the DuckDB version, and the kind of interface the extension uses. The platform picks the wheel tag from the table above. The DuckDB version picks the `v<DuckDB version>` directory. The extension name is the file name without `.duckdb_extension`; it must be lowercase letters, digits and underscores, because `LOAD` lowercases the name and calls a function named after it, and the tool checks that the binary contains that function.

You give what the binary cannot say:

- `--name`: the PyPI project name.
- `--version`: the version of the wheel. Any valid version in its standard form ([PEP 440](https://peps.python.org/pep-0440/)), see below.
- `--requires`: a dependency, as a [PEP 508](https://peps.python.org/pep-0508/) string, copied into the wheel as given. Repeat it for several.
- Optional: `--summary`, `--description-file` (shown as Markdown for `.md`, as reStructuredText for `.rst`, as plain text otherwise), `--license` (an SPDX license expression such as `MIT` or `Apache-2.0 OR MIT`), `--project-url "Label, URL"` (repeatable), `--keyword` (repeatable), and `--requires-python` (default `>=3.8`).

All binaries in one call must be the same extension for the same DuckDB version, one per platform. The tool refuses, and writes no wheel at all, when a binary has no DuckDB metadata, is for a platform without a wheel tag, is for a development build of DuckDB, or does not match the others.

### Extensions built against the C API

An extension built against DuckDB's stable C API does not record the DuckDB version it is meant for, only the oldest C API version it needs, because it loads in every DuckDB release that offers that API. A wheel still has to put it under one `v<DuckDB version>` directory, so for such a binary you name the DuckDB version yourself with `--duckdb-version 2.0.0`. For any other binary, `--duckdb-version` is optional and must agree with what the binary says. The tool cannot tell whether the DuckDB version you name offers the C API version the binary needs; `duckdb-extension-wheel check` finds out, because that DuckDB refuses to load it otherwise.

### Install information

DuckDB writes a small file beside every extension it installs, `<name>.duckdb_extension.info`, recording where the extension came from, and DuckDB 2.0 reads it when loading to decide which signing keys to trust. A community extension therefore needs that file in the wheel. Get it by installing your extension once on each platform, `INSTALL <name> FROM community`, and copying `~/.duckdb/extensions/v<version>/<platform>/<name>.duckdb_extension.info` next to the binary you give the tool, which picks it up by name; or name it with `--info <path>` when you build one platform at a time. The tool refuses a binary that has neither, unless you say `--core-extension`, which is right only for a binary signed with a DuckDB core key, such as the ones on extensions.duckdb.org; the flag waives the requirement, and a file found beside the binary is shipped either way. The file lands in the wheel next to the binary.

## Your choices: versions and dependencies

The wheel version is yours. The tool sets no rule linking it to the DuckDB version. The core extension projects use the DuckDB version as their wheel version, so that `pip install duckdb-core-ext-spatial==2.0.0` gets the binary for DuckDB 2.0.0; you may do the same, or version your extension on its own and say in your description which DuckDB version each release is for.

A C++ extension loads only in the exact DuckDB version it was built for, and the wheel places it only where that version looks. Each new DuckDB release, patch releases included, needs a new build of your extension and a new wheel.

Dependencies are yours too. You may require `duckdb-cli` at the matching version, so that installing your extension also installs a DuckDB command line that finds it, or leave it out so that users choose their own DuckDB. The tool treats `duckdb-cli` like any other dependency. If your extension needs another extension present to load, require that extension's wheel at the matching version, as the core `iceberg` project does with `duckdb-core-ext-avro`.

## What DuckDB does with the directory

DuckDB looks for extensions in the directories listed in its `extension_directories` setting. `LOAD geo` searches every listed directory, finds `v<its version>/<its platform>/geo.duckdb_extension` in one of them, and loads it without a download. DuckDB also checks the file: it refuses a binary built for another DuckDB version or platform, and one whose signature it does not trust.

The `duckdb` command from the `duckdb-cli` package adds `site-packages/.duckdb_extensions` to `extension_directories` when that directory exists. The releases differ in where: 1.5.0 puts it first; 1.5.1 and later put it last, after the directories DuckDB already searches. The 1.4 releases of `duckdb-cli` do not add it at all, and DuckDB before 1.5.0 has no `extension_directories` setting, so the tool refuses binaries built for DuckDB older than 1.5.0.

Its place in the list matters for automatic loading. When a query needs a known extension that is not loaded, DuckDB installs and loads it on its own, and `INSTALL` looks only at the first listed directory to decide whether the extension is already there. With the pip directory last, an explicit `LOAD` finds a pip-installed extension and loads it without a download, but automatic loading may download a second copy into the first directory, and without a network it fails. This is a limit until DuckDB treats an extension found in any configured directory as installed, a change under way for DuckDB 2.0.1; a `duckdb-cli` that bundles such an engine autoloads pip-installed extensions offline too.

## What is not promised

- The `duckdb` Python module does not read `site-packages/.duckdb_extensions` yet. In Python, set the directory yourself, for example `duckdb.connect(config={"extension_directories": [<module>.get_extensions_dir()]})`, or load the file by path with `<module>.get_extension_load_path()`.
- A C API extension that works across DuckDB versions still needs one copy per DuckDB version directory. A wheel holds one, so serving several DuckDB versions means a wheel, or a release, per DuckDB version.
- DuckDB checks the signature of every extension it loads, and decides which signing keys to trust from the install information file, `<name>.duckdb_extension.info`, that `INSTALL` writes beside an extension. Without that file DuckDB 2.0.0 trusts only the core keys, so a community extension placed by pip loads there only when its `.info` file is placed with it; this is why the tool ships the file (see Making the wheel) until DuckDB also trusts the community keys for a file without install information when `allow_community_extensions` is on, as 1.5.6 does. A binary you build and sign yourself, or leave unsigned, loads only when the user starts DuckDB with `-unsigned` or sets `allow_unsigned_extensions`. The wheel changes nothing about this.
- Nothing in the wheel tells pip which DuckDB the user has. If a user installs a wheel for another DuckDB version than the one they run, `LOAD` does not find it, or refuses it.

## Checking a wheel

`duckdb-extension-wheel check dist/<wheel>.whl` installs the wheel into a fresh virtual environment, downloads the DuckDB command line of the binary's version from DuckDB's GitHub release (every platform in the table has one from 1.5.0 on), and loads the extension with the environment's `.duckdb_extensions` as the only extension directory and automatic installation off. It passes only when DuckDB reports the extension loaded from that directory. It runs on the platform of the wheel, so run it in CI on each platform you build for. `--cli <path>` uses a DuckDB you already have, `--unsigned` allows an unsigned binary, `--no-deps` skips the wheel's dependencies, and `--index-url` or `--find-links` say where pip finds them.
