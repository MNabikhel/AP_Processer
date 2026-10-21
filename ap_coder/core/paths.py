"""Where AP Coder keeps your data and settings.

Enterprise data (database, invoices, outputs) and the ``.env`` with Azure keys live in one
*data folder* that is separate from the code, so updating or re-downloading the code never
loses or duplicates them. The installer records that folder in a small per-user settings file
(``~/.ap_coder/settings.json``); everything else reads it from here.

Data folder, first match wins:

1. ``AP_PRIVATE_DIR`` environment variable
2. the folder recorded by the installer
3. ``private/`` inside the project, when running from a checkout
4. ``./private``
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR_NAME = "APCoder"  # ~/APCoder: the data folder unless another one is recorded


def default_data_dir() -> Path:
    return Path.home() / DEFAULT_DATA_DIR_NAME


def user_settings_path() -> Path:
    return Path(os.environ.get("AP_USER_SETTINGS") or Path.home() / ".ap_coder" / "settings.json")


def read_user_settings() -> dict:
    """The settings file's values ({} when it is missing, unreadable, or not a JSON object: a damaged file is never
    a reason for the launcher or a page to stop)."""
    try:  # -sig: a settings file saved by Notepad starts with a byte-order mark (it is not an empty file)
        found = json.loads(user_settings_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def write_user_settings(**values: object) -> Path:
    """Set these values, keeping every other one. The one writer of the settings file (the launcher, the installer
    and the dashboard all use it): written whole to a temporary file first, so it is never left half-written."""
    path = user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = {**read_user_settings(), **values}
    part = path.with_name(f"{path.name}.{os.getpid()}.part")
    part.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    os.replace(part, path)
    return path


# --- The installs of AP Coder on this computer ----------------------------------------------------------------------
#
# The settings file also records every AP Coder folder started on this computer and which one is current, so a newer
# version unzipped anywhere takes over (its shortcut, its data folder) and cleans up the older ones, and an older one
# started by mistake hands over to the newest instead of opening the data with older code:
#
#     "installs": [{"folder": "C:\\AP Coder 0.2", "version": "0.2.0", "build": "2026-10-11T09:30:00",
#                   "last_run": "2026-10-11 09:41"}, ...],
#     "current": "C:\\AP Coder 0.2"


def same_folder(a: str | Path | None, b: str | Path | None) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except (OSError, TypeError):  # (TypeError: a damaged settings file names a folder as a number)
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def project_version(folder: Path = PROJECT_DIR) -> str:
    """The version in ``folder``'s pyproject.toml ("" when it has none). Read as text: no package needed."""
    try:
        text = (folder / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return ""
    found = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return found.group(1) if found else ""


def bundle_manifest(folder: Path = PROJECT_DIR) -> dict:
    """``folder``'s bundle_manifest.json (written by scripts/build_offline_bundle.py), or {}."""
    try:
        found = json.loads((folder / "bundle_manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


# The build stamp: ``ap_coder/BUILD`` holds ``$Format:%h %cI$``, which ``git archive`` fills in with the commit and its
# time (``export-subst`` in .gitattributes; GitHub's Download ZIP is a git archive). Every version used to be 0.2.0
# and a ZIP carried nothing else, so a newer ZIP unzipped over an older one looked like the same AP Coder (B-542).
BUILD_FILE = Path("ap_coder") / "BUILD"
_STAMP = re.compile(r"^\s*([0-9a-f]{7,40})\s+(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:Z|[+-]\d\d:?\d\d)?)\s*$")
# What the code fingerprint reads when there is neither a stamp nor git (a folder copied by hand).
_CODE_FOLDERS = ("ap_coder", "scripts")


def _utc(stamp: str) -> str:
    """``2026-10-21T16:14:00-04:00`` -> ``2026-10-21T20:14:00`` (UTC, no offset: builds compare as text)."""
    import datetime as dt

    try:
        moment = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is not None:
        moment = moment.astimezone(dt.UTC).replace(tzinfo=None)
    return moment.isoformat(timespec="seconds")


def _git_build(folder: Path) -> tuple[str, str]:
    """(short commit, its time in UTC) of a git checkout's HEAD, ("", "") when git can't tell."""
    import subprocess

    try:
        out = subprocess.run(["git", "-C", str(folder), "log", "-1", "--format=%h %cI"], capture_output=True,  # noqa: S607
                             text=True, timeout=10, check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return "", ""
    found = _STAMP.match(out.stdout) if out.returncode == 0 else None
    return (found.group(1), _utc(found.group(2))) if found else ("", "")


def _code_hash(folder: Path) -> str:
    """A short hash of the code itself (pyproject.toml and every file under ap_coder/ and scripts/, caches left out):
    a folder with neither a build stamp nor git still changes identity whenever its code does."""
    digest = hashlib.sha256()
    files = [folder / "pyproject.toml"]
    for top in _CODE_FOLDERS:
        base = folder / top
        if base.is_dir():
            files += [p for p in base.rglob("*") if "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")]
    found = False
    for path in sorted(files):
        try:
            if path.is_file() and path.relative_to(folder) != BUILD_FILE:
                digest.update(path.relative_to(folder).as_posix().encode() + b"\0" + path.read_bytes())
                found = True
        except OSError:
            continue
    return digest.hexdigest()[:7] if found else ""


def build_stamp(folder: Path = PROJECT_DIR) -> dict:
    """Which build of AP Coder ``folder`` holds: {"commit", "time" (UTC, "" when unknown), "source"}. The source is
    ``stamp`` (ap_coder/BUILD, filled in by GitHub's Download ZIP or git archive, or by the offline bundle builder),
    ``git`` (a checkout: its HEAD), ``code`` (neither: a hash of the code files, no time) or "" (no code at all)."""
    try:
        text = (folder / BUILD_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    found = _STAMP.match(text)
    if found:
        return {"commit": found.group(1), "time": _utc(found.group(2)), "source": "stamp"}
    if (folder / ".git").exists():
        commit, when = _git_build(folder)
        if commit:
            return {"commit": commit, "time": when, "source": "git"}
    code = _code_hash(folder)
    return {"commit": code, "time": "", "source": "code" if code else ""}


def install_identity(folder: Path = PROJECT_DIR) -> dict:
    """What AP Coder ``folder`` holds: its version (pyproject), its build (when it was built: the offline bundle's build
    time, else the time of the commit GitHub's ZIP was made from; "" for a checkout, which is never older), its commit
    (said to people: from the build stamp or git only), ``code`` (:func:`build_stamp`'s commit, or the hash of the code
    when there is no commit: what a running dashboard is compared with) and a fingerprint of its pyproject and code
    (changes whenever its code or packages do)."""
    stamp = build_stamp(folder)
    try:
        fingerprint = hashlib.sha256((folder / "pyproject.toml").read_bytes() + stamp["commit"].encode()).hexdigest()
    except OSError:
        fingerprint = ""
    built = str(bundle_manifest(folder).get("built") or "") or (stamp["time"] if stamp["source"] == "stamp" else "")
    commit = stamp["commit"] if stamp["source"] in ("stamp", "git") else ""
    return {"folder": str(folder), "version": project_version(folder), "build": built, "commit": commit,
            "commit_time": stamp["time"], "code": stamp["commit"], "fingerprint": fingerprint[:16]}  # fmt: skip


def build_label(identity: dict) -> str:
    """``build 3dacb6f, 2026-10-21 20:14``: the build as Settings → About, the doctor and the launcher say it ("" when
    nothing is known). A checkout's time is its last commit's."""
    commit = str(identity.get("commit") or "")
    built = str(identity.get("build") or identity.get("commit_time") or "")[:16].replace("T", " ")
    if commit:
        return f"build {commit}, {built}" if built else f"build {commit}"
    return f"built {built}" if built else ""


def version_key(version: str) -> tuple[int, ...]:
    """``"0.2.0"`` -> (0, 2, 0); the digits of each part only ("0.2.0rc1" -> (0, 2, 0)), "" -> ()."""
    return tuple(int(re.match(r"\d*", part).group() or 0) for part in version.split(".")) if version else ()


def is_older(this: dict, other: dict) -> bool:
    """``this`` install (an :func:`install_identity`) is older than ``other``: a lower version, or the same version
    built earlier (both built as offline bundles). The same version and build, or a checkout, is not older."""
    mine, theirs = version_key(this.get("version", "")), version_key(other.get("version", ""))
    if mine != theirs:
        return mine < theirs
    built, other_built = str(this.get("build") or ""), str(other.get("build") or "")
    return bool(built and other_built and built < other_built)


def describe(identity: dict, exact: bool = False) -> str:
    """``0.3.0 (build 3dacb6f, 2026-10-21)``: the version, its build and the day it was built (each when known);
    ``exact``: and the time."""
    version = identity.get("version") or "an earlier version"
    built = str(identity.get("build") or "")[: 16 if exact else 10].replace("T", " ")
    commit = str(identity.get("commit") or "")
    inside = ", ".join(part for part in (f"build {commit}" if commit else "", built) if part)
    return f"{version} ({inside})" if inside else version


def installs() -> list[dict]:
    found = read_user_settings().get("installs")
    if not isinstance(found, list):
        return []
    return [e for e in found if isinstance(e, dict) and e.get("folder") and isinstance(e["folder"], str)]


def current_install() -> dict | None:
    """The install recorded as current (the newest started), or None."""
    current = read_user_settings().get("current")
    return next((entry for entry in installs() if same_folder(entry["folder"], current)), None) if current else None


def record_install(identity: dict, current: bool = True) -> dict | None:
    """Record ``identity``'s folder as an install (started now), and as the current one. Returns the install that was
    current before, when it was another folder (None otherwise)."""
    before = current_install()
    entry = {key: identity.get(key, "") for key in ("folder", "version", "build", "commit")}
    entry["last_run"] = time.strftime("%Y-%m-%d %H:%M")
    entries = [e for e in installs() if not same_folder(e["folder"], entry["folder"])] + [entry]
    values: dict = {"installs": entries}
    if current:
        values["current"] = entry["folder"]
    write_user_settings(**values)
    return before if before and not same_folder(before["folder"], entry["folder"]) else None


def forget_install(folder: str | Path) -> None:
    """Remove ``folder`` from the record (it was cleaned up, or is gone)."""
    settings = read_user_settings()
    values: dict = {"installs": [e for e in installs() if not same_folder(e["folder"], folder)]}
    if same_folder(settings.get("current"), folder):
        values["current"] = None
    write_user_settings(**values)


def user_path(value: str) -> Path | None:
    """A folder or file as a person writes it: Windows cmd's ``set NAME="C:\\My Folder"`` keeps the quotes in the
    value (they are not part of the path), and ``~`` is the home folder."""
    value = (value or "").strip().strip('"').strip()
    return Path(value).expanduser() if value else None


def env_path(name: str) -> Path | None:
    """A folder or file from an environment variable (see ``user_path``)."""
    return user_path(os.environ.get(name) or "")


def private_dir() -> Path:
    """The folder for enterprise data (see the module docstring for the lookup order)."""
    if folder := env_path("AP_PRIVATE_DIR"):
        return folder
    recorded = user_path(str(read_user_settings().get("data_dir") or ""))
    if recorded:
        return recorded
    if (PROJECT_DIR / "pyproject.toml").exists() and (PROJECT_DIR / "ap_coder").is_dir():
        return PROJECT_DIR / "private"
    return Path("private")


def default_db_path() -> Path:
    return private_dir() / "ap_coder.db"


def file_sha256(path: str | Path) -> str | None:
    """The SHA-256 of the file at ``path`` (None when it can't be read)."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def after_private(stored: str | Path) -> Path | None:
    """``C:\\AP Coder 0.1\\private\\invoices\\a.pdf`` -> ``invoices/a.pdf``: where a file kept in an AP Coder 0.1
    data folder (``<folder>/private``) is, relative to the data folder (None for a path with no ``private`` in it)."""
    parts = Path(stored).parts
    names = [p.lower() for p in parts]
    if "private" not in names[:-1]:
        return None
    last = len(names) - 1 - names[::-1].index("private")
    return Path(*parts[last + 1 :]) if last + 1 < len(parts) else None


def _same_name(candidate: str, name: str) -> bool:
    """``candidate`` is the file ``name``, or that file renamed beside one of the same name when it moved
    (``a.from-old-install.pdf``, ``a.from-old-install-2.pdf``: scripts/upgrade.py)."""
    if os.path.normcase(candidate) == os.path.normcase(name):
        return True
    stem, suffix = os.path.splitext(name)
    lower = os.path.normcase(candidate)
    return lower.startswith(os.path.normcase(f"{stem}.from-old-install")) and lower.endswith(os.path.normcase(suffix))


def find_moved_file(
    stored: str | Path, sha: str | None, places: list[Path], name: str = "", data: Path | None = None
) -> Path | None:
    """Where the file recorded at ``stored`` (no longer there: an update moved it, or deleted the folder it was in)
    is now: the same place in the data folder ``data`` for a file of an AP Coder 0.1 data folder (``private/...``),
    else a file of the same name (``name``, else the stored one's) anywhere in ``places`` (searched in order, folders
    within them too). It must have the bytes recorded (``sha``); with no SHA-256 recorded, only a name found once."""
    name = name or Path(stored).name
    found: list[Path] = []
    rest = after_private(stored)
    if data is not None and rest is not None:
        found.append(data / rest)
    for place in places:
        if not place.is_dir():
            continue
        for folder, dirs, files in os.walk(place):
            dirs[:] = sorted(d for d in dirs if d not in (".cache", "__pycache__") and ".removing-" not in d)
            found += [Path(folder) / f for f in sorted(files) if _same_name(f, name)]
    seen: set[str] = set()
    unique = []
    for candidate in found:
        key = os.path.normcase(str(candidate))
        if key not in seen and candidate.is_file():
            seen.add(key)
            unique.append(candidate)
    if not sha:
        return unique[0] if len(unique) == 1 else None
    return next((c for c in unique if file_sha256(c) == sha), None)


def env_file() -> Path | None:
    """The ``.env`` with Azure settings: ``AP_ENV_FILE``, else the data folder's, else the folder
    the command runs from, else the project folder's."""
    if chosen := env_path("AP_ENV_FILE"):
        return chosen
    for candidate in (private_dir() / ".env", Path.cwd() / ".env", PROJECT_DIR / ".env"):
        if candidate.is_file():
            return candidate
    return None
