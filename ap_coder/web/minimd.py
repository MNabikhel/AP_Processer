"""A little Markdown for the dashboard's own help texts (docs/WHATS_NEW.md, the JD Edwards checklist): headings,
paragraphs, bullet lists, **bold**, *italic* and `code`. Links keep their text only (the docs they point to are not
pages of the dashboard). Everything is escaped first, so the result is safe whatever the text holds; it is meant for
AP Coder's own texts, never for text from an invoice (that is shown as plain text)."""

from __future__ import annotations

import re

from markupsafe import Markup, escape

_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")


def inline(text: str) -> str:
    """One line's inline marks as HTML (escaped first)."""
    out = str(escape(_LINK.sub(r"\1", text)))
    codes: list[str] = []

    def keep(match: re.Match[str]) -> str:  # code is never formatted further
        codes.append(match.group(1))
        return f"\x00{len(codes) - 1}\x00"

    out = _CODE.sub(keep, out)
    out = _BOLD.sub(r"<b>\1</b>", out)
    out = _ITALIC.sub(r"<i>\1</i>", out)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", out)


def render(text: str, *, top: int = 4) -> Markup:
    """``text`` as HTML: ``#`` headings become ``<h{top}>`` (``##`` one level down, and so on, to h6)."""
    html: list[str] = []
    para: list[str] = []
    items: list[str] = []
    kind = ""
    start = 1  # the first number of a numbered list (the Quick start's steps start at 0)

    def flush() -> None:
        nonlocal kind, start
        if para:
            html.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()
        if items:
            tag = "ol" if kind == "ol" else "ul"
            opening = f'<ol start="{start}">' if kind == "ol" and start != 1 else f"<{tag}>"
            html.append(opening + "".join(f"<li>{inline(i)}</li>" for i in items) + f"</{tag}>")
            items.clear()
        kind, start = "", 1

    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if heading := _HEADING.match(line):
            flush()
            level = min(6, top + len(heading.group(1)) - 1)
            html.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
        elif item := _BULLET.match(line) or _NUMBERED.match(line):
            new_kind = "ul" if item.re is _BULLET else "ol"
            if para or (kind and kind != new_kind):
                flush()
            if not items and new_kind == "ol":
                start = int(re.match(r"\s*(\d+)", line).group(1))
            kind = new_kind
            items.append(item.group(1))
        elif items and raw[:1].isspace():
            items[-1] += " " + line.strip()  # a bullet that goes on, indented, on the next line
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return Markup("".join(html))  # noqa: S704 - every piece above was escaped
