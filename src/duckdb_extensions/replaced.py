"""Notice when extensions.duckdb.org serves a different binary than a published wheel carries.

A wheel records which binary it holds (see duckdb_extension_wheel.read_provenance). For every published wheel
the server file is compared with that record: the ETag first, since the same ETag means the file is the one
the wheel was built from, and when it differs the file itself, by the extension_version in its footer and its
sha256. A changed binary becomes a post release of the wheel's project, never a replaced
file, because pypi.org keeps every file it has accepted.
"""

import gzip
import hashlib
import re
import urllib.error
from dataclasses import asdict, dataclass

from duckdb_extension_wheel.binary import read_footer
from duckdb_extension_wheel.net import get_bytes_headers, get_json, get_text, head_headers
from duckdb_extension_wheel.wheel import read_provenance

from .plan import parse_version, published_project
from .registry import PLATFORMS, REQUIRES, extension_url, project_name, wheel_filename

UNCHANGED = "unchanged"  # the server's headers are the recorded ones; nothing downloaded
HEADERS_ONLY = "headers_only"  # the ETag changed, the binary did not
REBUILT = "rebuilt"  # the same extension_version with different bytes
REPLACED = "replaced"  # a different extension_version
WITHDRAWN = "withdrawn"  # the server no longer has the file
UNKNOWN = "unknown"  # the wheel records no provenance and the baseline has none
ERROR = "error"  # a request failed; the other wheels are still compared

_POST = re.compile(r"\.post(\d+)$")

NEEDS_POST_RELEASE = (REBUILT, REPLACED)


@dataclass(frozen=True)
class Provenance:
    extension_version: str
    sha256: str
    etag: str = ""
    last_modified: str = ""
    source: str = ""

    @classmethod
    def from_lines(cls, lines):
        """From the dict read_provenance returns, or a baseline entry; None when the binary fields are missing."""
        if not lines or not lines.get("extension_version") or not lines.get("sha256"):
            return None
        return cls(
            extension_version=lines["extension_version"],
            sha256=lines["sha256"],
            etag=lines.get("etag") or "",
            last_modified=lines.get("last_modified") or "",
            source=lines.get("source") or "",
        )

    @classmethod
    def of_binary(cls, data, etag="", last_modified="", source=""):
        """What a binary says about itself, for a file just downloaded or taken from a wheel."""
        footer = read_footer(data)
        return cls(footer.extension_version, hashlib.sha256(data).hexdigest(), etag or "", last_modified or "", source)

    def same_file(self, other):
        """Whether the server identifies the same file: by ETag alone, because Last-Modified differs between
        a HEAD answered by the origin and a GET served from the cache for one and the same file."""
        return bool(self.etag) and self.etag == other.etag


@dataclass(frozen=True)
class Finding:
    version: str
    extension: str
    platform: str
    outcome: str
    recorded: Provenance = None
    current: Provenance = None
    # The release the record was read from: the DuckDB version, or its newest post release with this platform.
    wheel_version: str = None
    detail: str = ""

    def to_json(self):
        return {
            "version": self.version,
            "extension": self.extension,
            "platform": self.platform,
            "outcome": self.outcome,
            "recorded": asdict(self.recorded) if self.recorded else None,
            "current": asdict(self.current) if self.current else None,
            "wheel_version": self.wheel_version or self.version,
            "detail": self.detail,
        }


def baseline_key(extension, version, platform):
    return f"{extension} {version} {platform}"


def metadata_url(page, filename):
    """The URL of a wheel's METADATA on the index, from the project's simple API page; None when the index does
    not serve the metadata separately for that file."""
    for file in page.get("files", []):
        if file["filename"] == filename:
            return file["url"] + ".metadata" if file.get("core-metadata") else None
    return None


def project_page(extension, index, fetch_json=get_json):
    """The simple API page of the extension's project, or an empty page when the project does not exist."""
    try:
        return fetch_json(f"{index}/simple/{project_name(extension)}/", "application/vnd.pypi.simple.v1+json")
    except urllib.error.HTTPError as e:
        e.close()
        if e.code != 404:
            raise
        return {"files": []}


def published_state(page):
    """Per version, the wheel files on the page, as published() returns them."""
    return published_project("", "", fetch=lambda url, accept: page)


def post_number(release, version):
    """N for <version>.postN, 0 for <version> itself, None for any other release."""
    if release == version:
        return 0
    match = _POST.match(release[len(version):]) if release.startswith(version) else None
    return int(match.group(1)) if match else None


def latest_release(state, version, platform, extension):
    """The release whose wheel for the platform is the current one for the DuckDB version: the newest
    <version>.postN that has the platform's wheel, else <version> itself; None when neither has it."""
    candidates = sorted(((post_number(v, version), v) for v in state if post_number(v, version) is not None), reverse=True)
    for _, v in candidates:
        if wheel_filename(extension, v, platform) in state[v].files:
            return v
    return None


def newest_release(state, version):
    """The newest release of the DuckDB version in the state (<version>.postN with the highest N, else
    <version>), or None when the state has none."""
    releases = sorted(((post_number(v, version), v) for v in state if post_number(v, version) is not None), reverse=True)
    return releases[0][1] if releases else None


def _name_pattern(project):
    """A regex for the project name in any spelling PEP 503 treats as the same name."""
    return r"[-_.]+".join(re.escape(part) for part in re.split(r"[-_.]+", project)) if project else ""


def pins_exactly(page, state, version, dependency, fetch_text=get_text):
    """Whether the newest release of the DuckDB version on the page requires the dependency at an exact version
    (`==2.0.0`, as the wheels published before the `.*` pin did), so that only a new wheel can follow a post
    release of the dependency. A release whose metadata cannot be read counts as pinning exactly."""
    newest = newest_release(state, version)
    if newest is None:
        return False
    filename = next(iter(sorted(state[newest].files)), None)
    url = metadata_url(page, filename) if filename else None
    text = fetch_text(url) if url else None
    if not text:
        return True
    head = text.split("\n\n", 1)[0]
    exact = re.compile(r"^Requires-Dist:\s*" + _name_pattern(project_name(dependency)) + r"\s*\(?\s*==\s*[0-9][^\s;)*]*\s*\)?\s*(;.*)?$", re.IGNORECASE)
    return any(exact.match(line) for line in head.splitlines())


def recorded(extension, version, platform, page, baseline=None, fetch_text=get_text, wheel_version=None):
    """The provenance a published wheel records, read from its METADATA on the index, else from the baseline
    file for a wheel built before wheels carried one; None when neither has it. wheel_version names the
    release the wheel belongs to when it is a post release of the DuckDB version."""
    url = metadata_url(page, wheel_filename(extension, wheel_version or version, platform))
    if url is not None:
        text = fetch_text(url)
        provenance = Provenance.from_lines(read_provenance(text)) if text else None
        if provenance is not None:
            return provenance
    if baseline and (wheel_version or version) == version:
        return Provenance.from_lines(baseline.get(baseline_key(extension, version, platform)))
    return None


def compare(extension, version, platform, record, head=head_headers, get=get_bytes_headers, wheel_version=None):
    """One finding for a published wheel: the server's ETag against the record, and when it differs the binary
    itself. The download happens only when the ETag differs or is missing."""
    url = extension_url(extension, version, platform)
    if record is None:
        return Finding(version, extension, platform, UNKNOWN, wheel_version=wheel_version)
    status, headers = head(url)
    if status == 404:
        return Finding(version, extension, platform, WITHDRAWN, record, wheel_version=wheel_version)
    if status != 200:
        raise RuntimeError(f"HEAD {url} returned {status}")
    probe = Provenance(record.extension_version, record.sha256, headers.get("etag") or "", headers.get("last_modified") or "", url)
    if record.same_file(probe):
        return Finding(version, extension, platform, UNCHANGED, record, wheel_version=wheel_version)
    compressed, headers = get(url)
    if compressed is None:
        return Finding(version, extension, platform, WITHDRAWN, record, wheel_version=wheel_version)
    current = Provenance.of_binary(gzip.decompress(compressed), headers.get("etag"), headers.get("last_modified"), url)
    if current.extension_version != record.extension_version:
        outcome = REPLACED
    elif current.sha256 != record.sha256:
        outcome = REBUILT
    else:
        outcome = HEADERS_ONLY
    return Finding(version, extension, platform, outcome, record, current, wheel_version=wheel_version)


def post_version(state, version):
    """The next free post release of the DuckDB version for a project, from its versions on the index."""
    pattern = re.compile(re.escape(version) + r"\.post(\d+)$")
    taken = [int(m.group(1)) for v in state for m in [pattern.match(v)] if m]
    return f"{version}.post{max(taken, default=0) + 1}"


def dependents(extension):
    """The extensions whose LOAD requires this one."""
    return sorted(d for d, deps in REQUIRES.items() if extension in deps)


def post_items(findings, published_state, follow=None):
    """The post release items the findings call for: one per extension and version, covering every platform
    whose current wheel is published and whose file the server still serves, so a post release is a complete
    set. An extension that requires another one gets a post release when its newest wheel pins the requirement
    exactly and that requirement is about to be, or already is, a post release (follow, keyed by (version,
    dependent, requirement), says so as detect() computed it), since only a new wheel can point at the new
    release; this holds night after night until the dependent's wheel follows, not only on the night the
    requirement changed."""
    follow = follow or {}
    needed = {}
    withdrawn = set()
    for f in findings:
        if f.outcome in NEEDS_POST_RELEASE:
            needed.setdefault((f.version, f.extension), []).append(f)
        elif f.outcome == WITHDRAWN:
            withdrawn.add((f.version, f.extension, f.platform))
    for (version, d, _), must_follow in sorted(follow.items()):
        if must_follow and d in published_state and version in published_state[d]:
            needed.setdefault((version, d), [])
    items = []
    # Newest DuckDB version first, extensions in name order within a version.
    for version, extension in sorted(sorted(needed, key=lambda k: k[1]), key=lambda k: parse_version(k[0]), reverse=True):
        state = published_state.get(extension, {})
        platforms = [p for p in PLATFORMS if latest_release(state, version, p, extension) is not None and (version, extension, p) not in withdrawn]
        if not platforms:
            continue
        items.append({
            "version": version,
            "extension": extension,
            "platforms": platforms,
            "wheel_version": post_version(state, version),
            "changed": sorted(f.platform for f in needed[(version, extension)]),
        })
    return items


def detect(versions, extensions, index, baseline=None, fetch_json=get_json, fetch_text=get_text, head=head_headers, get=get_bytes_headers, log=None):
    """Findings for every published wheel of the versions and extensions, the published state used, and per
    (version, dependent) whether the dependent's newest wheel still pins a compared extension exactly. The
    state covers the dependents of the compared extensions too, so their post releases can be planned. Each
    project's index page is fetched once. A request that fails for one wheel, or for one project page, is a
    finding of its own, so the other wheels are still compared."""
    with_dependents = sorted(set(extensions) | {d for e in extensions for d in dependents(e)})
    pages, state, findings = {}, {}, []
    for e in with_dependents:
        try:
            pages[e] = project_page(e, index, fetch_json)
        except Exception as exc:  # noqa: BLE001
            findings.append(Finding("", e, "", ERROR, detail=f"{type(exc).__name__}: {exc}"))
            if log is not None:
                print(f"error {e}: {findings[-1].detail}", file=log)
            continue
        state[e] = published_state(pages[e])
    follow = {}
    for version in sorted(versions, key=parse_version, reverse=True):
        for extension in sorted(extensions):
            if extension not in state:
                continue
            for platform in PLATFORMS:
                wheel_version = latest_release(state[extension], version, platform, extension)
                if wheel_version is None:
                    continue
                try:
                    record = recorded(extension, version, platform, pages[extension], baseline, fetch_text, wheel_version)
                    finding = compare(extension, version, platform, record, head, get, wheel_version)
                except Exception as e:  # noqa: BLE001
                    finding = Finding(version, extension, platform, ERROR, wheel_version=wheel_version, detail=f"{type(e).__name__}: {e}")
                findings.append(finding)
                if log is not None and finding.outcome != UNCHANGED:
                    print(f"{finding.outcome} {extension} {version} {platform} {finding.detail}".rstrip(), file=log)
    # A dependent must follow its requirement while its newest wheel pins it exactly and the requirement is a
    # post release already or becomes one tonight; decided from the index, so a night missed is not lost.
    changed_tonight = {(f.version, f.extension) for f in findings if f.outcome in NEEDS_POST_RELEASE}
    for version in sorted(versions, key=parse_version, reverse=True):
        for extension in sorted(extensions):
            if extension not in state:
                continue
            requirement_moved = (version, extension) in changed_tonight or post_number(newest_release(state[extension], version) or version, version)
            for d in dependents(extension):
                if d not in state or version not in state[d] or not requirement_moved:
                    continue
                try:
                    follow[(version, d, extension)] = pins_exactly(pages[d], state[d], version, extension, fetch_text)
                except Exception as exc:  # noqa: BLE001
                    follow[(version, d, extension)] = True
                    findings.append(Finding(version, d, "", ERROR, detail=f"{type(exc).__name__}: {exc}"))
                    if log is not None:
                        print(f"error {d} {version}: {findings[-1].detail}", file=log)
    return findings, state, follow
