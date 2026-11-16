"""A newer tested AP Coder from GitHub's releases (D-247): what the launcher asks about before it sets anything up.

Only for AP Coder from GitHub's ZIP or a git checkout (never the offline bundle), only from scripts/launch.py, and
only GitHub: the check is one GET of the repository's releases on api.github.com, through this computer's proxy as the
first setup's downloads go, with nothing but that request sent (no identifier of this computer or its person). A
release is made by .github/workflows/builds.yml only once CI passed on main for its commit: its tag
``v<version>-build.<commit>``, its source ZIP laid out as GitHub's own (one folder, ``AP_Processer-<...>/``, its
ap_coder/BUILD stamped) and ``SHA256SUMS``; its notes end with ``Build <commit>, committed <UTC time>``.

Taking one, once the person said yes: the ZIP is downloaded (from GitHub's hosts only, every redirect checked) beside
this folder, checked against SHA256SUMS, and unzipped into a new folder beside this one (never over it, never in the
data folder; a name in it that would land outside that folder refuses the ZIP). The launcher then starts the new
folder's own launcher, which takes over as when a person unzips a newer AP Coder and double-clicks it. Anything that
fails is said, what was downloaded is deleted, and this AP Coder starts. Standard library only: it runs before any
package is installed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ap_coder.core import paths

REPO = "MNabikhel/AP_Processer"  # where the releases are (a constant: never this checkout's git remote)
API = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
# GitHub's hosts a release and its files come from; a redirect anywhere else is refused.
HOSTS = ("api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
SCHEME = "https"
CHECK_SECONDS = 5.0  # the whole check: a slow or blocked network delays a start by no more than this
DOWNLOAD_TIMEOUT = 60.0  # each read of the download
AGENT = "AP-Coder-launcher"  # GitHub asks every request for one; it names the program, not the computer
TAG = re.compile(r"^v(\d+(?:\.\d+)*)-build\.([0-9a-f]{7,40})$")
BUILT = re.compile(r"Build ([0-9a-f]{7,40}), committed (\S+)")
SUMS = "SHA256SUMS"
NOTES_LINES = 12  # what's new, as said in the window: the first lines of the notes
UPDATE_FROM_ENV = "AP_UPDATE_FROM"  # the folder that downloaded the new one: its launcher's takeover is not asked again


class UpdateError(Exception):
    """Why a release could not be taken, in plain words."""


@dataclass
class Release:
    tag: str
    version: str
    commit: str
    committed: str  # the commit's time, UTC ("" when the notes don't say)
    published: str  # the day it was released, YYYY-MM-DD
    notes: str
    zip_name: str
    zip_url: str
    sums_url: str

    def shown(self) -> str:
        """``0.3.1 (build 4abc123, 2026-11-20)``, as the launcher says a version."""
        return paths.describe({"version": self.version, "commit": self.commit, "build": self.committed})

    def whats_new(self, most: int = NOTES_LINES) -> list[str]:
        """The notes' lines worth saying (the build line left out), at most ``most``, each cut to one line's width."""
        lines = [ln.strip() for ln in self.notes.splitlines() if ln.strip() and not BUILT.search(ln)]
        shown = [ln if len(ln) <= 110 else ln[:107] + "..." for ln in lines[:most]]
        if len(lines) > most:
            shown.append(f"... and {len(lines) - most} more")
        return shown


def _allowed(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == SCHEME and (parts.hostname or "") in HOSTS


class _GitHubOnly(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to GitHub's own hosts, over HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib's own signature
        if not _allowed(newurl):
            raise UpdateError(f"the download was sent to {urllib.parse.urlsplit(newurl).hostname or newurl}, which "
                              "is not GitHub's")  # fmt: skip
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, timeout: float, accept: str = "application/octet-stream"):
    """``url`` opened through this computer's proxy (as urllib finds it), redirects to GitHub's hosts only."""
    if not _allowed(url):
        raise UpdateError(f"{urllib.parse.urlsplit(url).hostname or url} is not GitHub's")
    opener = urllib.request.build_opener(_GitHubOnly())
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": accept})
    return opener.open(request, timeout=timeout)


def _within(seconds: float, work: Callable[[], object]) -> object:
    """``work()``'s answer, or TimeoutError once ``seconds`` passed (the request is left to end on its own)."""
    done: dict = {}

    def run() -> None:
        try:
            done["answer"] = work()
        except BaseException as exc:  # noqa: BLE001 - raised again in the caller's thread
            done["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError(f"no answer from GitHub within {seconds:.0f} seconds")
    if "error" in done:
        raise done["error"]
    return done["answer"]


def _read_releases() -> list:
    with _open(API, CHECK_SECONDS, accept="application/vnd.github+json") as response:
        return json.loads(response.read(2_000_000).decode("utf-8"))


def newest() -> Release | None:
    """The newest tested release (a build tag with its ZIP and SHA256SUMS; drafts, pre-releases and the pilot
    releases' offline bundles left out), or None. Raises when GitHub could not be asked (any error)."""
    found = _within(CHECK_SECONDS, _read_releases)
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, dict) or item.get("draft") or item.get("prerelease"):
            continue
        tag = TAG.match(str(item.get("tag_name") or ""))
        assets = {str(a.get("name")): str(a.get("browser_download_url") or "") for a in item.get("assets") or []
                  if isinstance(a, dict)}  # fmt: skip
        zips = [name for name in assets if name.startswith("AP_Processer-") and name.endswith(".zip")]
        if not tag or len(zips) != 1 or SUMS not in assets:
            continue
        notes = str(item.get("body") or "")
        built = BUILT.search(notes)
        return Release(tag=tag.group(0), version=tag.group(1), commit=tag.group(2),
                       committed=paths._utc(built.group(2)) if built else "",
                       published=str(item.get("published_at") or "")[:10], notes=notes, zip_name=zips[0],
                       zip_url=assets[zips[0]], sums_url=assets[SUMS])  # fmt: skip
    return None


def is_newer(release: Release, mine: dict) -> bool:
    """Whether ``release`` is newer than the AP Coder ``mine`` (an install identity): a higher version, or the same
    version committed later. The same build, or one whose time can't be told (a folder copied by hand), is not."""
    ours, theirs = paths.version_key(mine.get("version", "")), paths.version_key(release.version)
    if ours != theirs:
        return theirs > ours
    commit = str(mine.get("commit") or "")
    if commit and (commit.startswith(release.commit) or release.commit.startswith(commit)):
        return False
    when = str(mine.get("commit_time") or "")
    return bool(when and release.committed and release.committed > when)


def _sum_for(text: str, name: str) -> str:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    raise UpdateError(f"{SUMS} does not list {name}")


def _download(url: str, target: Path) -> str:
    """``url`` saved as ``target``; its SHA-256."""
    digest = hashlib.sha256()
    with _open(url, DOWNLOAD_TIMEOUT) as response, open(target, "wb") as out:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def _top_folder(names: list[str]) -> str:
    """The one folder every name in the ZIP is in; UpdateError for a name that would land elsewhere (an absolute
    path, "..", a drive, a backslash) or a ZIP that is not one folder."""
    tops = set()
    for name in names:
        path = PurePosixPath(name)
        if (not name or name.startswith("/") or "\\" in name or ":" in name or ".." in path.parts
                or path.is_absolute()):  # fmt: skip
            raise UpdateError(f"the ZIP holds a file that would be written outside its folder ({name})")
        tops.add(path.parts[0])
    if len(tops) != 1:
        raise UpdateError("the ZIP is not one AP Coder folder")
    top = tops.pop()
    if top in (".", "") or top.startswith("."):
        raise UpdateError("the ZIP is not one AP Coder folder")
    return top


def take(release: Release, beside: Path, data: Path, say: Callable[[str], None] = print) -> Path:
    """``release`` downloaded, checked and unzipped into a new folder beside ``beside`` (this install); the folder.
    UpdateError when it can't be (what was downloaded or unzipped is deleted again)."""
    parent = beside.resolve().parent
    folder = parent / release.zip_name.removesuffix(".zip")  # (the workflow names the ZIP after its one folder)
    if _inside(folder, data):
        raise UpdateError(f"the new folder would be inside the data folder {data}")
    if folder.exists():
        if str(paths.install_identity(folder).get("commit") or "").startswith(release.commit):
            return folder  # unzipped by an earlier yes whose new start did not finish: started again
        raise UpdateError(f"{folder} is there already: move it away, then start AP Coder again")
    download = parent / f".{release.zip_name}.download"
    unpacking = parent / f".{release.tag}.unzipping"
    try:
        say(f"Downloading AP Coder {release.shown()} from GitHub ({release.zip_name})...")
        try:
            expected = _sum_for(_read_text(release.sums_url), release.zip_name)
            got = _download(release.zip_url, download)
        except (OSError, ValueError) as exc:  # (urllib's errors are OSErrors)
            raise UpdateError(f"the download didn't finish ({exc})") from exc
        if got != expected:
            raise UpdateError("the file that came is not the one GitHub lists (its SHA-256 differs)")
        with zipfile.ZipFile(download) as archive:
            top = _top_folder(archive.namelist())
            if top != folder.name:
                raise UpdateError(f"the ZIP's folder is {top}, not {folder.name}")
            for info in archive.infolist():
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise UpdateError(f"the ZIP holds a link ({info.filename})")
            archive.extractall(unpacking)
        found = unpacking / top
        if not (found / "ap_coder").is_dir() or not (found / "scripts" / "launch.py").is_file():
            raise UpdateError("the ZIP holds no AP Coder")
        if paths.project_version(found) != release.version:
            raise UpdateError(f"the ZIP holds AP Coder {paths.project_version(found) or '?'}, not {release.version}")
        found.rename(folder)
        return folder
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError(f"it could not be unzipped beside this AP Coder ({exc})") from exc
    finally:
        with contextlib.suppress(OSError):
            download.unlink()
        if unpacking.exists():
            shutil.rmtree(unpacking, ignore_errors=True)


def _read_text(url: str) -> str:
    with _open(url, DOWNLOAD_TIMEOUT) as response:
        return response.read(100_000).decode("utf-8", errors="replace")


def _inside(folder: Path, data: Path) -> bool:
    try:
        folder.resolve().relative_to(data.resolve())
    except ValueError:
        return False
    return True


def handover_command(folder: Path, argv: list[str], windows: bool = os.name == "nt") -> list[str]:
    """The new folder's launcher, as a double-click on its APProcessor runs it (from that folder)."""
    if windows:
        return ["cmd.exe", "/c", "APProcessor.bat", *argv]
    return ["/bin/bash", "APProcessor.command", *argv]
