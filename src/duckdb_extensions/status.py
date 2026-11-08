"""The availability of extension wheels per DuckDB version and platform, and the page that shows it."""

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from duckdb_extensions.plan import (
    UPLOAD_MARGIN,
    UPLOAD_WINDOW,
    available,
    published,
    published_project,
    stable_versions,
)
from duckdb_extensions.registry import EXTENSIONS, PLATFORMS, REQUIRES, platform_tag, project_name, wheel_filename

PYPI = "https://pypi.org"
TEST_PYPI = "https://test.pypi.org"

# State of one (version, extension, platform) cell, from best to worst.
PUBLISHED = "published"  # the wheel is on pypi.org
STAGED = "staged"  # a staging wheel exists on test.pypi, the production wheel is not on pypi.org
WAITING = "waiting"  # the server has it, but a required extension is not released for this platform
LATE = "late"  # the server has it, pypi.org refuses new files for this release
AVAILABLE = "available"  # the server has it, nothing published or staged
ABSENT = "absent"  # the server does not serve it for this platform

LEGEND = {
    PUBLISHED: "on pypi.org",
    STAGED: "staged on test.pypi, not yet on pypi.org",
    WAITING: "waits for a required extension to be published",
    LATE: "missing, and pypi.org no longer accepts files for this release",
    AVAILABLE: "served by extensions.duckdb.org, not yet built",
    ABSENT: "not served by extensions.duckdb.org for this platform",
}

CLI_PLATFORMS = ["linux-amd64", "linux-arm64", "osx-amd64", "osx-arm64", "windows-amd64", "windows-arm64"]


@dataclass
class Status:
    generated: datetime
    versions: list
    extensions: dict = field(default_factory=dict)  # version -> extension -> platform -> state
    cli: dict = field(default_factory=dict)  # version -> platform -> bool
    module: dict = field(default_factory=dict)  # version -> python tag -> sorted platform tags


def _staged_platforms(state, version):
    """Platform tags with a staging wheel for the DuckDB version, from a project's test.pypi state."""
    tags = set()
    pattern = re.compile(re.escape(version) + r"\.dev\d+$")
    for v, entry in state.items():
        if pattern.match(v):
            for f in entry.files:
                tags.add(f.rsplit("-", 1)[1].removesuffix(".whl"))
    return tags


def classify(version, extension, platform, served, on_pypi, on_test, now):
    """The state of one cell. served is the set of (extension, platform) pairs the server has for the version;
    on_pypi and on_test are the project's states per index as published() returns them."""
    if (extension, platform) not in served:
        return ABSENT
    release = on_pypi.get(version)
    if release and wheel_filename(extension, version, platform) in release.files:
        return PUBLISHED
    if platform_tag(platform) in _staged_platforms(on_test, version):
        return STAGED
    if release and release.first_upload is not None and now + UPLOAD_MARGIN - release.first_upload > UPLOAD_WINDOW:
        return LATE
    return AVAILABLE


def apply_requirements(cells, version, on_pypi_all):
    """Mark cells of a dependent extension as waiting when the required extension is not published there."""
    for extension, deps in REQUIRES.items():
        for platform, state in cells.get(extension, {}).items():
            if state != AVAILABLE:
                continue
            for dep in deps:
                release = on_pypi_all.get(dep, {}).get(version)
                if not release or wheel_filename(dep, version, platform) not in release.files:
                    cells[extension][platform] = WAITING
                    break


def module_wheels(files, version):
    """{python tag: sorted platform tags} of the duckdb module's wheels for a version."""
    out = {}
    prefix = f"duckdb-{version}-"
    for f in files:
        if not f.startswith(prefix) or not f.endswith(".whl"):
            continue
        parts = f[len(prefix) : -4].split("-")
        if len(parts) != 3:
            continue
        python, _, plat = parts
        out.setdefault(python, set()).add(plat)
    return {k: sorted(v) for k, v in sorted(out.items())}


def gather(minimum, now=None, token=None):
    """Collect the state of every extension wheel, the duckdb-cli wheels and the duckdb module wheels."""
    now = now or datetime.now(timezone.utc)
    versions = stable_versions(minimum, token=token)
    status = Status(generated=now, versions=sorted(versions, key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True))
    on_pypi = {e: published(e, PYPI) for e in EXTENSIONS}
    on_test = {e: published(e, TEST_PYPI) for e in EXTENSIONS}
    for version in status.versions:
        have = {(e, p) for e in EXTENSIONS for p in PLATFORMS if version in on_pypi[e] and wheel_filename(e, version, p) in on_pypi[e][version].files}
        served = available(version, list(EXTENSIONS), list(PLATFORMS), skip=have) | have
        cells = {e: {p: classify(version, e, p, served, on_pypi[e], on_test[e], now) for p in PLATFORMS} for e in EXTENSIONS}
        apply_requirements(cells, version, on_pypi)
        status.extensions[version] = cells
    cli = published_project("duckdb-cli", PYPI)
    for version in status.versions:
        files = cli.get(version).files if version in cli else set()
        status.cli[version] = {p: f"duckdb_cli-{version}-py3-none-{platform_tag(p)}.whl" in files for p in CLI_PLATFORMS}
    module = published_project("duckdb", PYPI)
    for version in status.versions:
        status.module[version] = module_wheels(module[version].files, version) if version in module else {}
    return status


def to_json(status):
    return json.dumps(
        {
            "generated": status.generated.isoformat(),
            "versions": status.versions,
            "legend": LEGEND,
            "extensions": status.extensions,
            "duckdb_cli": status.cli,
            "duckdb_module": status.module,
        },
        indent=1,
        sort_keys=True,
    )


_CSS = """
body { font: 15px/1.45 system-ui, sans-serif; margin: 2rem auto; max-width: 1200px; padding: 0 1rem; color: #222; }
h1 { font-size: 1.6rem; } h2 { font-size: 1.25rem; margin-top: 2.5rem; } h3 { font-size: 1.05rem; margin-top: 1.5rem; }
table { border-collapse: collapse; margin: 0.5rem 0 1rem; font-size: 13px; }
th, td { border: 1px solid #ddd; padding: 3px 7px; text-align: center; white-space: nowrap; }
th { background: #f4f4f4; } td.name { text-align: left; font-family: ui-monospace, monospace; }
td.published { background: #cfe8cf; } td.staged { background: #fff1b8; } td.waiting { background: #e3d7f3; }
td.late { background: #f6c9c9; } td.available { background: #e9e9e9; } td.absent { color: #aaa; }
td.yes { background: #cfe8cf; } td.no { background: #f6c9c9; }
.legend span { display: inline-block; padding: 1px 8px; margin: 0 6px 6px 0; border: 1px solid #ccc; border-radius: 3px; }
.legend .published { background: #cfe8cf; } .legend .staged { background: #fff1b8; } .legend .waiting { background: #e3d7f3; }
.legend .late { background: #f6c9c9; } .legend .available { background: #e9e9e9; } .legend .absent { color: #aaa; }
p.note { color: #555; }
"""

_SYMBOL = {PUBLISHED: "●", STAGED: "◐", WAITING: "◇", LATE: "✕", AVAILABLE: "○", ABSENT: "·"}


def _table(headers, rows):
    out = ["<table><thead><tr><th></th>" + "".join(f"<th>{html.escape(h)}</th>" for h in headers) + "</tr></thead><tbody>"]
    for name, cells in rows:
        out.append(f'<tr><td class="name">{html.escape(name)}</td>' + "".join(cells) + "</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def render(status):
    """The status page as one HTML document."""
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>DuckDB extension wheels: status</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>DuckDB extension wheels: status</h1>",
        f"<p class=\"note\">Generated {status.generated.strftime('%Y-%m-%d %H:%M UTC')}. "
        "Each extension is a PyPI project <code>duckdb-core-ext-&lt;name&gt;</code> with one wheel per platform and one release per DuckDB version. "
        "The wheels are tagged <code>py3-none</code>: they work with any Python from 3.8 on, so Python versions are not a dimension here; "
        "the <code>duckdb</code> module's own wheels per Python version are listed at the bottom. "
        "Extensions compiled into the DuckDB CLI (autocomplete, core_functions, icu, json, parquet) have no wheels.</p>",
        '<p class="legend">' + "".join(f'<span class="{k}">{_SYMBOL[k]} {html.escape(v)}</span>' for k, v in LEGEND.items()) + "</p>",
    ]
    platforms = list(PLATFORMS)
    for version in status.versions:
        cells = status.extensions.get(version, {})
        counts = {k: 0 for k in LEGEND}
        rows = []
        for e in EXTENSIONS:
            row = []
            for p in platforms:
                state = cells.get(e, {}).get(p, ABSENT)
                counts[state] += 1
                row.append(f'<td class="{state}" title="{html.escape(LEGEND[state])}">{_SYMBOL[state]}</td>')
            rows.append((project_name(e), row))
        summary = ", ".join(f"{counts[k]} {k}" for k in (PUBLISHED, STAGED, WAITING, LATE, AVAILABLE) if counts[k])
        parts.append(f"<h2>DuckDB {html.escape(version)}</h2><p class=\"note\">{html.escape(summary)}</p>")
        parts.append(_table(platforms, rows))
    parts.append("<h2>duckdb-cli</h2><p class=\"note\">The CLI binary as a PyPI package, one wheel per platform, any Python from 3.8 on.</p>")
    rows = [(f"duckdb-cli {v}", [f'<td class="{"yes" if ok else "no"}">{"●" if ok else "✕"}</td>' for ok in (status.cli.get(v, {}).get(p, False) for p in CLI_PLATFORMS)]) for v in status.versions]
    parts.append(_table(CLI_PLATFORMS, rows))
    parts.append("<h2>duckdb (Python module)</h2><p class=\"note\">Wheels on pypi.org per Python tag and platform tag, as published by duckdb-python. The extension wheels serve this module too when both are the same DuckDB version.</p>")
    for version in status.versions:
        wheels = status.module.get(version, {})
        if not wheels:
            parts.append(f"<h3>duckdb {html.escape(version)}</h3><p class=\"note\">no wheels on pypi.org</p>")
            continue
        tags = sorted({t for ts in wheels.values() for t in ts})
        rows = [(py, [f'<td class="{"yes" if t in ts else "no"}">{"●" if t in ts else "·"}</td>' for t in tags]) for py, ts in wheels.items()]
        parts.append(f"<h3>duckdb {html.escape(version)}</h3>")
        parts.append(_table(tags, rows))
    parts.append("<p class=\"note\">Sources: extensions.duckdb.org (which binaries exist), pypi.org and test.pypi.org (simple API), GitHub releases of duckdb/duckdb (stable versions). "
                 "Built by the <code>status</code> workflow of <a href=\"https://github.com/duckdb/duckdb-python-extensions\">duckdb/duckdb-python-extensions</a>; the data is in <a href=\"status.json\">status.json</a>.</p>")
    parts.append("</body></html>")
    return "\n".join(parts)
