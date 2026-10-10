"""Reading and updating the ``.env`` settings file in place.

Standard library only: the installer uses it before any package is installed.
"""

from __future__ import annotations

import re
from pathlib import Path

PLACEHOLDER = "<your-resource>"  # the example endpoints in .env.example


_QUOTED = {'"': re.compile(r'"((?:[^"\\]|\\.)*)"'), "'": re.compile(r"'((?:[^'\\]|\\.)*)'")}
_ESCAPES = {'"': re.compile(r'\\([\\"])'), "'": re.compile(r"\\([\\'])")}


def parse_value(value: str) -> str:
    """One value as python-dotenv (what the app reads with) reads it: a quoted value ends at its closing quote
    (an inline ``# comment`` after it is not part of it), an unquoted one at `` #`` (``abc#def`` is kept)."""
    value = value.strip()
    quote = value[:1]
    if quote in _QUOTED:
        match = _QUOTED[quote].match(value)
        if match:
            return _ESCAPES[quote].sub(r"\1", match.group(1))
        return value  # no closing quote: kept as it is (python-dotenv cannot read the line either)
    return re.sub(r"\s+#.*", "", value).strip()


def read_env(path: Path) -> dict[str, str]:
    """``KEY=value`` pairs that are set (commented lines and example placeholders are skipped)."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():  # -sig: Notepad's byte-order mark
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = re.sub(r"^export\s+", "", key.strip())
            value = parse_value(value)
            if PLACEHOLDER not in value:
                values[key] = value
    return values


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Set values in place (uncommenting ``# KEY=`` lines), keeping every other line as it is."""
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    for key, value in updates.items():
        if re.search(r"[\s#'\"]", value):
            value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        # The line in effect if there is one (the last, as it is read), else the commented example.
        live = [i for i, line in enumerate(lines) if re.match(rf"^\s*{re.escape(key)}\s*=", line)]
        examples = [i for i, line in enumerate(lines) if re.match(rf"^\s*#\s*{re.escape(key)}\s*=", line)]
        if live or examples:
            lines[live[-1] if live else examples[0]] = f"{key}={value}"
        else:
            lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def clean_url(value: str) -> str:
    """An endpoint as Azure expects it: https:// in front, one slash at the end."""
    value = value.strip()
    if value and not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    return value.rstrip("/") + "/" if value else value
