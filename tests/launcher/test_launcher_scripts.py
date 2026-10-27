"""The double-click files themselves: APProcessor.command must run under the bash 3.2 every Mac has as /bin/bash,
and the .bat files (APProcessor.bat, scripts\\terminal.bat) must survive whatever folder they are unzipped into
(spaces, parentheses, ``&``, accents). Neither can be run here on the real system, so their text is checked: every
bash-4-only form is refused, ``bash -n`` must parse each script, and every path a .bat expands is inside double
quotes."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELL_SCRIPTS = ["APProcessor.command"]
BAT_FILES = sorted(p.relative_to(ROOT).as_posix() for p in [*ROOT.glob("*.bat"), *ROOT.glob("scripts/*.bat")])

# Forms bash 3.2 (macOS's /bin/bash) does not have: each one is a syntax error or does something else there.
BASH4_ONLY = {
    r"\b(declare|local|typeset)\s+-[a-zA-Z]*[An]": "associative arrays / namerefs (declare -A, local -n)",
    r"\b(mapfile|readarray)\b": "mapfile / readarray",
    r"\$\{[^}]*(,,|\^\^)[^}]*\}|\$\{[A-Za-z_][A-Za-z0-9_]*[,^]\}": "case conversion ${var,,} / ${var^^}",
    r"\$\{[^}]*@[QEPAaKUuL]\}": "${var@Q} transformations",
    r"&>>": "&>> (append both streams)",
    r"\|&": "|& (pipe both streams)",
    r"\bcoproc\b": "coproc",
    r";;&|;&\s*$": "case fall-through ;;& / ;&",
    r"\bshopt\s+-s\s+(globstar|lastpipe)\b": "globstar / lastpipe",
    r"\$\{?(BASHPID|EPOCHSECONDS|EPOCHREALTIME)\b": "$BASHPID / $EPOCHSECONDS",
    r"\bwait\s+-n\b": "wait -n",
    r"\[\[\s+-v\s": "[[ -v var ]]",
    r"\bdeclare\s+-g\b": "declare -g",
    r"\bread\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[iN]": "read -i / read -N",
    r"\{\d+\.\.\d+\.\.\d+\}|\{0\d+\.\.\d+\}": "brace steps / zero padding {1..9..2} {01..10}",
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\[-\d+\]\}": "negative array index ${a[-1]}",
    r"\bexec\s+\{[A-Za-z_]+\}[<>]": "exec {fd}> (automatic file descriptors)",
    r"printf\s+['\"]?%\(": "printf %(...)T",
}


def _code_lines(text: str) -> list[tuple[int, str]]:
    """The lines of a shell script without their comments (a ``#`` that starts a word)."""
    out = []
    for number, raw in enumerate(text.splitlines(), 1):
        code = re.sub(r"(^|\s)#.*$", "", raw) if not raw.startswith("#!") else ""
        if code.strip():
            out.append((number, code))
    return out


def bash4_forms(text: str) -> list[str]:
    return [f"line {n}: {what}: {code.strip()}" for n, code in _code_lines(text)
            for pattern, what in BASH4_ONLY.items() if re.search(pattern, code)]  # fmt: skip


@pytest.mark.parametrize("name", SHELL_SCRIPTS)
def test_shell_scripts_use_nothing_bash_3_2_lacks(name):
    assert bash4_forms((ROOT / name).read_text(encoding="utf-8")) == []


def test_the_bash_4_check_catches_what_it_should():
    """The check itself: each form is caught, and APProcessor.command's own forms (all bash 3.2) are not."""
    caught = ["declare -A seen", "local -n ref=x", "mapfile -t lines < f", 'echo "${name,,}"', 'echo "${name^^}"',
              "cmd &>> log", "cmd |& tee", "coproc x { :; }", "shopt -s globstar", "echo $BASHPID", "wait -n",
              "[[ -v PY ]]", "read -r -i yes answer", "for i in {1..9..2}; do :; done", 'echo "${a[-1]}"',
              "exec {fd}>log", "printf '%(%F)T'"]  # fmt: skip
    for code in caught:
        assert bash4_forms(code), code
    fine = ['v="${1%%.*}"', "for minor in {15..40}; do", 'read -r -p "Press Enter. " _', "done < <(candidates)",
            'echo $((${1%%.*} * 100 + ${1#*.}))', 'local v i=0 major="${1%%.*}"',
            "# declare -A in a comment"]  # fmt: skip
    for code in fine:
        assert not bash4_forms(code), code


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash here")
@pytest.mark.skipif(os.name == "nt", reason="Windows' bash is WSL's stub; checked on Linux and macOS")
@pytest.mark.parametrize("name", SHELL_SCRIPTS)
def test_shell_scripts_parse(name):
    out = subprocess.run(["bash", "-n", str(ROOT / name)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_the_mac_desktop_launcher_is_bash_3_2_too():
    """The "AP Coder.command" the launcher writes on a Mac's desktop (scripts/launch.py, write_mac_launcher)."""
    text = (ROOT / "scripts" / "launch.py").read_text(encoding="utf-8")
    body = re.search(r"def write_mac_launcher.*?link\.write_text\((.*?)encoding=", text, re.S).group(1)
    assert "#!/bin/bash" in body and bash4_forms(body.replace("\\n", "\n")) == []


@pytest.mark.parametrize("name", SHELL_SCRIPTS)
def test_shell_scripts_have_unix_line_endings_and_a_bash_shebang(name):
    raw = (ROOT / name).read_bytes()
    assert b"\r" not in raw
    assert raw.startswith((b"#!/bin/bash\n", b"#!/usr/bin/env bash\n"))


# --- The .bat files ---------------------------------------------------------------------------------------------

# What expands to a path: the batch file's own folder, an argument, a for-loop variable, and the usual folders.
PATH_EXPANSION = re.compile(r"%~[a-z]*[0-9*]|%%~[a-z]*[A-Za-z]|%(CD|LOCALAPPDATA|ProgramFiles|USERPROFILE|TEMP|TMP|"
                            r"APPDATA|VIRTUAL_ENV|HOMEPATH)%", re.IGNORECASE)  # fmt: skip


def unquoted_paths(text: str) -> list[str]:
    """Lines where a path is expanded outside double quotes: there an ``&`` in the folder name ends the command and
    runs the rest as another one, a ``)`` ends the block it is in, and a space splits it in two."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or re.match(r"(?i)(rem\b|::|@?echo\s+off\b)", stripped):
            continue
        for match in PATH_EXPANSION.finditer(line):
            if line.count('"', 0, match.start()) % 2 == 0:
                found.append(f"line {number}: {stripped}")
    return found


@pytest.mark.parametrize("name", BAT_FILES)
def test_every_path_a_bat_file_expands_is_quoted(name):
    """Unzipped into ``C:\\Users\\José\\AP Coder (2) & co``, every line must still mean what it says."""
    assert unquoted_paths((ROOT / name).read_text(encoding="ascii")) == []


def test_the_quoting_check_catches_what_it_should():
    assert unquoted_paths("echo AP Coder could not open its own folder: %~dp0")
    assert unquoted_paths("cd %~dp0")
    assert unquoted_paths('for /d %%D in ("x") do call :try %%~D\\python.exe')
    assert not unquoted_paths('pushd "%~dp0" || goto :nofolder')
    assert not unquoted_paths('set "PATH=%~dp0.venv\\Scripts;%PATH%"')
    assert not unquoted_paths("rem pushd, not cd: %~dp0")


@pytest.mark.parametrize("name", BAT_FILES)
def test_bat_files_are_ascii_with_windows_line_endings(name):
    """cmd reads a .bat in the console's code page (an accent would be another letter) and needs CRLF: with LF only
    it misreads labels (``goto :fail`` lands in the wrong place). .gitattributes keeps them so in a ZIP download."""
    raw = (ROOT / name).read_bytes()
    raw.decode("ascii")
    assert raw.count(b"\n") == raw.count(b"\r\n") > 0


@pytest.mark.parametrize("name", BAT_FILES)
def test_bat_files_never_cd_without_d(name):
    """``cd`` alone does not change the drive (the bundle on D:, the prompt on C:): pushd, or cd /d."""
    text = (ROOT / name).read_text(encoding="ascii")
    lines = [line for line in text.splitlines() if not re.match(r"(?i)\s*rem\b", line)]
    assert not [line for line in lines if re.search(r"(?i)(^|[\s&|(])cd\s+(?!/d)", line)]


@pytest.mark.parametrize("name", BAT_FILES)
def test_bat_files_keep_their_settings_to_themselves(name):
    """A .bat run from an open command prompt must not leave its variables (PATH, AP_YES...) or folder behind:
    ``setlocal`` before the first ``set`` or ``pushd``; run twice, terminal.bat would otherwise put .venv on PATH twice
    in the prompt that ran it."""
    lines = [line.strip().lower() for line in (ROOT / name).read_text(encoding="ascii").splitlines()]
    code = [line for line in lines if line and not line.startswith(("rem ", "rem\t", "::")) and line != "rem"]
    changes = [i for i, line in enumerate(code) if re.match(r"(set|pushd)\b", line)]
    if changes:
        assert "setlocal" in code[: changes[0]], f"{name}: setlocal must come before {code[changes[0]]!r}"
