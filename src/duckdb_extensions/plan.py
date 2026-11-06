"""Decide which extension wheels to build: what the server has that the index lacks."""

import re
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from duckdb_extensions.net import get_json, head as _head
from duckdb_extensions.registry import REQUIRES, extension_url, project_name, wheel_filename

# PyPI refuses new files for a release whose first file is older than this, checked at upload time;
# the margin covers the time between planning and the last upload of a run, which stages, tests on
# every platform and promotes up to 200 items six at a time.
UPLOAD_WINDOW = timedelta(days=14)
UPLOAD_MARGIN = timedelta(hours=12)

_RELEASES_API = "https://api.github.com/repos/duckdb/duckdb/releases?per_page=100"
_STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


@dataclass
class PublishedVersion:
    files: set
    first_upload: datetime


@dataclass
class WorkItem:
    version: str
    extension: str
    platforms: list


@dataclass(frozen=True)
class Late:
    version: str
    extension: str
    platform: str
    first_upload: datetime


@dataclass(frozen=True)
class Failed:
    version: str
    extension: str
    attempts: int


@dataclass(frozen=True)
class Waiting:
    version: str
    extension: str
    requires: str


@dataclass
class Plan:
    items: list
    late: list
    failed: list
    waiting: list


# An item staged once without being promoted failed its test; the nightly never retries it, a run started by
# hand with a version or extension input does.
MAX_STAGE_ATTEMPTS = 1


def parse_version(version):
    return tuple(int(part) for part in version.split("."))


def stable_versions(minimum, fetch=get_json, token=None):
    """Released DuckDB versions at or above minimum that are neither drafts nor pre-releases."""
    floor = parse_version(minimum)
    versions = []
    for release in fetch(_RELEASES_API, "application/vnd.github+json", token=token):
        if release.get("draft") or release.get("prerelease"):
            continue
        match = _STABLE_TAG.match(release["tag_name"])
        if match:
            version = ".".join(match.groups())
            if parse_version(version) >= floor:
                versions.append(version)
    return sorted(versions, key=parse_version)


def available(version, extensions, platforms, head=_head, skip=frozenset()):
    """The (extension, platform) pairs the server serves for the version; pairs in skip are not asked for."""
    found = set()
    for extension in extensions:
        for platform in platforms:
            if (extension, platform) in skip:
                continue
            url = extension_url(extension, version, platform)
            status = head(url)
            if status == 200:
                found.add((extension, platform))
            elif status != 404:
                raise RuntimeError(f"HEAD {url} returned {status}")
    return found


def _parse_upload_time(value):
    if value is None:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def published(extension, index, fetch=get_json):
    """Per version, the wheel files of the extension's project on the index and the earliest upload time."""
    url = f"{index}/simple/{project_name(extension)}/"
    try:
        page = fetch(url, "application/vnd.pypi.simple.v1+json")
    except urllib.error.HTTPError as e:
        e.close()
        if e.code != 404:
            raise
        return {}
    versions = {}
    for file in page.get("files", []):
        filename = file["filename"]
        if not filename.endswith(".whl"):
            continue
        version = filename.split("-")[1]
        uploaded = _parse_upload_time(file.get("upload-time"))
        entry = versions.setdefault(version, PublishedVersion(files=set(), first_upload=None))
        entry.files.add(filename)
        if uploaded is not None and (entry.first_upload is None or uploaded < entry.first_upload):
            entry.first_upload = uploaded
    return versions


def stage_attempts(staged, extension, version, since=None):
    """How many staging versions of the extension exist on the test index for the DuckDB version, counting
    only those uploaded after `since` (the item's first production upload) when given."""
    pattern = re.compile(re.escape(version) + r"\.dev\d+$")
    count = 0
    for v, state in staged.get(extension, {}).items():
        if not pattern.match(v):
            continue
        if since is not None and state.first_upload is not None and state.first_upload <= since:
            continue
        count += 1
    return count


def _dependency_missing(extension, version, platforms, published):
    """The first required extension whose released wheel is absent for any of the platforms, or None."""
    for dep in REQUIRES.get(extension, []):
        files = published.get(dep, {}).get(version)
        files = files.files if files else set()
        if any(wheel_filename(dep, version, p) not in files for p in platforms):
            return dep
    return None


def plan(versions, available, published, now, staged=None, max_attempts=MAX_STAGE_ATTEMPTS):
    """What to build (newest version first, then extension), what PyPI would refuse as late, what was
    staged before without being promoted (it failed; no automatic retry), and what waits for a required
    extension.

    A pair is planned when the server has it and its wheel file is not on the index.
    A wheel that exists is never planned again, even if the server file changed.
    An extension whose LOAD requires another one is planned only once that one's released wheels exist
    for the same platforms, so the dependency is always on the index before the dependent.
    staged is the test index's state per extension (as published() returns it); None disables the cap.
    """
    result = Plan([], [], [], [])
    for version in sorted(versions, key=parse_version, reverse=True):
        pairs = available.get(version, set())
        by_extension = {}
        for extension, platform in pairs:
            by_extension.setdefault(extension, []).append(platform)
        for extension in sorted(by_extension):
            state = published.get(extension, {}).get(version)
            files = state.files if state else set()
            first_upload = state.first_upload if state else None
            window_closed = first_upload is not None and now + UPLOAD_MARGIN - first_upload > UPLOAD_WINDOW
            missing = sorted(p for p in by_extension[extension] if wheel_filename(extension, version, p) not in files)
            if not missing:
                continue
            if window_closed:
                result.late.extend(Late(version, extension, p, first_upload) for p in missing)
                continue
            dep = _dependency_missing(extension, version, missing, published)
            if dep is not None:
                result.waiting.append(Waiting(version, extension, dep))
                continue
            attempts = stage_attempts(staged, extension, version, since=first_upload) if staged is not None else 0
            if attempts >= max_attempts:
                result.failed.append(Failed(version, extension, attempts))
            else:
                result.items.append(WorkItem(version, extension, missing))
    return result
