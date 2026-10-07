"""HTML building blocks for the dashboard (styled by assets/style.css).

Pure functions returning HTML strings, so they can be unit-tested. Every piece of data is
escaped; icons use the Material Symbols font that Streamlit already loads.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
import re
from collections.abc import Iterable, Sequence
from typing import Any

# Dark enough for white initials (>= 4.5:1 contrast).
AVATAR_COLORS = ("#1f63b5", "#b54a1f", "#127a56", "#8a5a00", "#b23a6b", "#2f6b2f", "#4a3aa7", "#9b2c2c")
# Categorical palette (dataviz reference palette, fixed order).
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948")
OK, WARN, ERR, BRAND = "#1a7f4b", "#e59a00", "#c53030", "#2a78d6"


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def icon(name: str, size: str = "1.1em", color: str = "") -> str:
    style = f"font-size:{size};vertical-align:-0.18em;line-height:1;" + (f"color:{color};" if color else "")
    return (
        f"<span aria-hidden='true' style=\"font-family:'Material Symbols Rounded';font-weight:normal;"
        f'font-style:normal;{style}">{esc(name)}</span>'
    )


def initials(name: str) -> str:
    words = [w for w in "".join(c if c.isalnum() else " " for c in (name or "")).split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def avatar(name: str, size: str = "") -> str:
    digest = int(hashlib.sha1((name or "").lower().encode()).hexdigest(), 16)
    color = AVATAR_COLORS[digest % len(AVATAR_COLORS)]
    return f"<div class='apc-avatar {size}' style='background:{color}' aria-hidden='true'>{esc(initials(name))}</div>"


def pill(text: str, tone: str = "gray", icon_name: str = "") -> str:
    ico = icon(icon_name, ".95em") if icon_name else ""
    return f"<span class='apc-pill {tone}'>{ico}{esc(text)}</span>"


def tax_chip(tax_type: str) -> str:
    return f"<span class='apc-tax {esc(tax_type)}'>{esc(tax_type)}</span>"


def kbd(key: str) -> str:
    return f"<kbd class='apc-kbd'>{esc(key)}</kbd>"


def confidence_color(value: float, threshold: float = 0.85) -> str:
    return OK if value >= threshold else WARN


def ring(value: float, size: int = 64, stroke: int = 7, color: str = BRAND, track: str = "#e8edf4",
         text_color: str = "#142033", label: str | None = None) -> str:  # fmt: skip
    """Donut gauge for a single 0..1 value (pure CSS: inline SVG is stripped by Streamlit's sanitiser)."""
    value = max(0.0, min(1.0, value or 0.0))
    text = label if label is not None else f"{value:.0%}"
    style = (
        f"--p:{value:.4f};--c:{color};--t:{track};--w:{stroke}px;width:{size}px;height:{size}px;"
        f"color:{text_color};font-size:{size * 0.24:.1f}px"
    )
    return f"<div class='apc-ring' style='{style}' role='img' aria-label='{esc(text)}'><span>{esc(text)}</span></div>"


def svg_img(svg: str, width: int, height: int, alt: str = "") -> str:
    """Embed SVG as an image (Streamlit strips inline <svg>)."""
    data = base64.b64encode(svg.strip().encode("utf-8")).decode("ascii")
    hidden = "" if alt else " aria-hidden='true'"
    return (
        f"<img src='data:image/svg+xml;base64,{data}' width='{width}' height='{height}' "
        f"alt='{esc(alt)}'{hidden} style='display:inline-block'>"
    )


def sparkline(values: Sequence[float], width: int = 96, height: int = 28, color: str = BRAND) -> str:
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = width / (len(values) - 1)
    points = " ".join(f"{i * step:.1f},{height - 3 - (v - lo) / span * (height - 6):.1f}" for i, v in enumerate(values))
    last_x, last_y = points.split(" ")[-1].split(",")
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
        f"<polyline points='{points}' fill='none' stroke='{color}' stroke-width='2' stroke-linejoin='round' "
        f"stroke-linecap='round'/><circle cx='{last_x}' cy='{last_y}' r='3' fill='{color}'/></svg>"
    )
    return svg_img(svg, width, height)


def tile(label: str, value: Any, icon_name: str, tone: str = "blue", hint: str = "", trend: str = "",
         spark: Sequence[float] | None = None) -> str:  # fmt: skip
    """``trend`` is 'up', 'down' or ''."""
    spark_svg = sparkline(spark or [], color={"green": OK, "amber": WARN}.get(tone, BRAND)) if spark else ""
    hint_html = f"<span class='hint {trend}'>{esc(hint)}</span>" if hint else "<span></span>"
    return (
        f"<div class='apc-tile tone-{tone} apc-anim'><div class='top'><span class='label'>{esc(label)}</span>"
        f"<span class='icon'>{icon(icon_name)}</span></div><div class='value'>{esc(value)}</div>"
        f"<div class='foot'>{hint_html}{spark_svg}</div></div>"
    )


def tiles(items: Iterable[str]) -> str:
    return f"<div class='apc-tiles'>{''.join(items)}</div>"


def page_header(eyebrow: str, title: str, subtitle: str = "") -> str:
    sub = f"<div class='apc-sub'>{esc(subtitle)}</div>" if subtitle else ""
    return (
        f"<div class='apc-head apc-anim'><div><div class='apc-eyebrow'>{esc(eyebrow)}</div>"
        f"<div class='apc-title'>{esc(title)}</div>{sub}</div></div>"
    )


def greeting(now: dt.datetime | None = None) -> str:
    hour = (now or dt.datetime.now()).hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


def hero(eyebrow: str, title: str, lead: str, chips: Iterable[str], ring_value: float, ring_label: str,
         ring_caption: str) -> str:  # fmt: skip
    chips_html = "".join(f"<span class='chip'>{c}</span>" for c in chips)
    gauge = ring(ring_value, size=104, stroke=10, color="#6aa9f2", track="rgba(255,255,255,.14)",
                 text_color="#ffffff", label=ring_label)  # fmt: skip
    return (
        f"<div class='apc-hero apc-anim'><div style='position:relative;z-index:1'>"
        f"<div class='eyebrow'>{esc(eyebrow)}</div><h1>{esc(title)}</h1><p class='lead'>{esc(lead)}</p>"
        f"<div class='chips'>{chips_html}</div></div>"
        f"<div class='ring-wrap'>{gauge}<div class='ring-label'>{esc(ring_caption)}</div></div></div>"
    )


def meter(value: float, threshold: float = 0.85) -> str:
    value = max(0.0, min(1.0, value or 0.0))
    return (
        f"<div class='apc-meter'><div class='txt'><span>Confidence</span><b>{value:.0%}</b></div>"
        f"<div class='bar'><div class='fill' style='width:{value * 100:.0f}%;"
        f"background:{confidence_color(value, threshold)}'></div></div></div>"
    )


def queue_card(inv: dict[str, Any], taxes: Sequence[str] = (), province: str = "") -> str:
    flagged = bool(inv.get("requires_review"))
    status = pill("Needs attention", "warn", "flag") if flagged else pill("Ready", "ok", "check_circle")
    meta = [f"<span>{icon('receipt_long', '1em')} {esc(inv.get('invoice_number') or '—')}</span>",
            f"<span>{icon('event', '1em')} {esc(inv.get('invoice_date') or '—')}</span>"]  # fmt: skip
    if province:
        meta.append(f"<span>{icon('location_on', '1em')} {esc(province)}</span>")
    meta += [tax_chip(t) for t in taxes]
    return (
        f"<div class='apc-qcard {'attn' if flagged else 'ready'} apc-anim'>"
        f"{avatar(inv.get('vendor_name') or inv.get('file_name') or '')}"
        f"<div class='who'><div class='vendor'>{esc(inv.get('vendor_name') or inv.get('file_name'))}</div>"
        f"<div class='meta'>{''.join(meta)}</div></div>"
        f"<div class='state'>{status}{meter(inv.get('adjusted_confidence') or 0.0)}</div>"
        f"<div class='amount'>{money(inv.get('grand_total'))}<small>{esc(inv.get('currency') or '')}</small></div>"
        f"<div class='chev'>{icon('chevron_right')}</div></div>"
    )


def progress(position: int, total: int) -> str:
    pct = 0 if not total else (position / total) * 100
    return f"<div class='apc-progress' aria-hidden='true'><div style='width:{pct:.0f}%'></div></div>"


def invoice_hero(vendor: str, meta: Iterable[tuple[str, str]], pills: Iterable[str], confidence: float,
                 total: float, currency: str, threshold: float = 0.85) -> str:  # fmt: skip
    meta_html = "".join(f"<span>{icon(i, '1.05em')} {esc(t)}</span>" for i, t in meta if t)
    gauge = ring(confidence, size=70, stroke=7, color=confidence_color(confidence, threshold))
    return (
        f"<div class='apc-inv apc-anim'>{avatar(vendor, 'lg')}<div class='main'>"
        f"<div class='vendor'>{esc(vendor or 'Unknown vendor')}</div><div class='meta'>{meta_html}</div>"
        f"<div class='pills'>{''.join(pills)}</div></div>"
        f"<div class='side'><div style='text-align:center'>{gauge}<div class='apc-muted' "
        f"style='font-size:.72rem'>AI confidence</div></div>"
        f"<div class='total'><div class='label'>Total due</div><div class='value'>{money(total)}</div>"
        f"<div class='cur'>{esc(currency)}</div></div></div></div>"
    )


def split_bar(segments: Sequence[tuple[str, float]], max_segments: int = 6) -> str:
    """Stacked bar + legend showing where an invoice's money goes (fixed palette order, 'Other' fold)."""
    segments = [(label, amount) for label, amount in segments if amount > 0]
    if len(segments) > max_segments:
        head = segments[: max_segments - 1]
        segments = [*head, ("Other", sum(a for _, a in segments[max_segments - 1 :]))]
    total = sum(a for _, a in segments) or 1.0
    bars, legend = [], []
    for i, (label, amount) in enumerate(segments):
        color = SERIES[i % len(SERIES)]
        share = amount / total
        title = f"{label}: {money(amount)} ({share:.0%})"
        bars.append(f"<div style='flex:{share:.4f};background:{color}' title='{esc(title)}'></div>")
        legend.append(
            f"<span><i style='background:{color}'></i>{esc(label)} <b>{money(amount)}</b> <em>{share:.0%}</em></span>"
        )
    return f"<div class='apc-split' role='img' aria-label='Spend by account'>{''.join(bars)}</div><div class='apc-legend'>{''.join(legend)}</div>"  # noqa: E501


def reason_row(number: int, description: str, gl_code: str, gl_label: str, reasoning: str,
               badges: Iterable[str] = ()) -> str:  # fmt: skip
    """One line of the 'why the AI chose these codes' panel (badges are pre-built HTML)."""
    return (
        f"<div class='apc-reason'><span class='apc-pill gray'>{number}</span><div class='body'>"
        f"<div class='head'><span class='desc'>{esc(description)}</span><span class='apc-arrow'>→</span>"
        f"<b class='apc-mono'>{esc(gl_code)}</b> <span class='apc-muted'>{esc(gl_label)}</span></div>"
        f"<div class='why'>{esc(reasoning)}</div><div class='badges'>{''.join(badges)}</div></div></div>"
    )


_CHECK_ICONS = {"error": "!", "warning": "!", "ok": "✓", "info": "★"}


def check(kind: str, title: str, message: str, code: str = "") -> str:
    code_html = f"<span class='code'>{esc(code)}</span>" if code else ""
    mark = _CHECK_ICONS.get(kind, "•")
    return (
        f"<div class='apc-check {kind} apc-anim'><span class='ico' aria-hidden='true'>{mark}</span>"
        f"<div><div class='ttl'>{esc(title)}{code_html}</div><div>{esc(message)}</div></div></div>"
    )


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], right: Iterable[int] = (),
          foot: Sequence[str] | None = None, wrap: Iterable[int] = ()) -> str:  # fmt: skip
    """``rows`` cells are HTML (escape data before passing); ``right`` = right-aligned column indexes,
    ``wrap`` = columns allowed to wrap (others stay on one line)."""
    right, wrap = set(right), set(wrap)

    def cells(values: Sequence[str], tag: str) -> str:
        out = []
        for i, v in enumerate(values):
            classes = " ".join(c for c, on in (("r", i in right), ("w", i in wrap)) if on)
            cls = f" class='{classes}'" if classes else ""
            out.append(f"<{tag}{cls}>{v}</{tag}>")
        return "".join(out)

    body = "".join(f"<tr>{cells(r, 'td')}</tr>" for r in rows)
    foot_html = f"<tfoot><tr>{cells(foot, 'td')}</tr></tfoot>" if foot else ""
    return (
        f"<table class='apc-table'><thead><tr>{cells([esc(h) for h in headers], 'th')}</tr></thead>"
        f"<tbody>{body}</tbody>{foot_html}</table>"
    )


EMPTY_INBOX_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="150" height="110" viewBox="0 0 150 110">
  <ellipse cx="75" cy="100" rx="52" ry="6" fill="#e3e9f2"/>
  <rect x="28" y="38" width="94" height="56" rx="12" fill="#ffffff" stroke="#cfdcee" stroke-width="2"/>
  <path d="M28 66h26l6 10h30l6-10h26" fill="none" stroke="#cfdcee" stroke-width="2"/>
  <rect x="44" y="14" width="62" height="40" rx="8" fill="#eaf2fc" stroke="#9cc0ec" stroke-width="2"/>
  <circle cx="75" cy="34" r="11" fill="#1a7f4b"/>
  <path d="M70 34l4 4 7-8" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="122" cy="22" r="3" fill="#6aa9f2"/><circle cx="24" cy="30" r="2.5" fill="#eda100"/>
  <circle cx="132" cy="44" r="2" fill="#1baf7a"/>
</svg>"""

LEARNING_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="150" height="110" viewBox="0 0 150 110">
  <ellipse cx="75" cy="100" rx="52" ry="6" fill="#e3e9f2"/>
  <rect x="34" y="30" width="82" height="62" rx="12" fill="#ffffff" stroke="#cfdcee" stroke-width="2"/>
  <polyline points="46,78 62,66 76,72 92,52 104,46" fill="none" stroke="#2a78d6" stroke-width="3"
    stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="104" cy="46" r="4" fill="#2a78d6"/>
  <path d="M75 8l4 9 9 1-7 6 2 9-8-5-8 5 2-9-7-6 9-1z" fill="#eda100"/>
</svg>"""


def empty_state(title: str, text: str, art: str = EMPTY_INBOX_SVG) -> str:
    return f"<div class='apc-empty apc-anim'>{svg_img(art, 150, 110)}<h3>{esc(title)}</h3><p>{esc(text)}</p></div>"


def time_ago(iso: str | None, now: dt.datetime | None = None) -> str:
    if not iso:
        return ""
    try:
        then = dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    seconds = int(((now or dt.datetime.now()) - then).total_seconds())
    if seconds < 60:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n > 1 else ''} ago"
    return iso


def feed_item(reviewer: str, html_text: str, when: str) -> str:
    """``html_text`` must already be escaped."""
    return (
        f"<div class='item apc-anim'>{avatar(reviewer, 'sm')}<div><div class='txt'>{html_text}</div>"
        f"<div class='when'>{esc(when)}</div></div></div>"
    )


STATUS_PILLS = {
    "attention": ("Needs attention", "warn", "flag"),
    "review": ("In queue", "info", "inbox"),
    "approved": ("Approved", "ok", "check_circle"),
    "rejected": ("Rejected", "gray", "block"),
    "failed": ("Failed", "err", "error"),
}


def recent_row(inv: dict[str, Any], now: dt.datetime | None = None) -> str:
    """One line in the "recently processed" list: who, what, when, and where it is now."""
    state = inv.get("status") or "review"
    if state == "review" and inv.get("requires_review"):
        state = "attention"
    label, tone, icon_name = STATUS_PILLS.get(state, (state.title(), "gray", ""))
    name = inv.get("vendor_name") or inv.get("file_name") or ""
    detail = inv.get("invoice_number") or inv.get("file_name") or ""
    when = time_ago(inv.get("created_at"), now)
    sub = " · ".join(x for x in (detail, when) if x)
    return (
        f"<div class='apc-rrow'>{avatar(name, 'sm')}<div style='min-width:0'><div class='name'>{esc(name)}</div>"
        f"<div class='sub'>{esc(sub)}</div></div>{pill(label, tone, icon_name)}</div>"
    )


def vendor_row(name: str, lines: int, accuracy: float, corrections: int) -> str:
    color = confidence_color(accuracy, 0.9)
    return (
        f"<div class='apc-vrow'>{avatar(name, 'sm')}<div style='min-width:0'><div class='name'>{esc(name)}</div>"
        f"<div class='sub'>{lines} lines · {corrections} correction{'s' if corrections != 1 else ''}</div>"
        f"<div class='apc-meter' style='margin-top:.3rem'><div class='bar'><div class='fill' "
        f"style='width:{accuracy * 100:.0f}%;background:{color}'></div></div></div></div>"
        f"<div class='pct'>{accuracy:.0%}</div></div>"
    )


def step(state: str, label: str, detail: str) -> str:
    mark = {"ok": "✓", "todo": "!", "bad": "✕"}.get(state, "•")
    return (
        f"<div class='apc-step'><span class='dot {state}' aria-hidden='true'>{mark}</span>{esc(label)}"
        f"<span class='state'>{esc(detail)}</span></div>"
    )


def sidebar_profile(name: str, approved_today: int, waiting: int) -> str:
    return (
        f"<div class='apc-me'>{avatar(name)}<div><div class='name'>{esc(name)}</div>"
        f"<div class='role'>AP reviewer</div></div></div>"
        f"<div class='apc-today'><div><b>{approved_today}</b><span>done today</span></div>"
        f"<div><b>{waiting}</b><span>waiting</span></div></div>"
    )


# Document Intelligence Markdown uses plain HTML tables. Only these bare tags are kept; everything
# else (cell text included) is escaped, so text printed on an invoice can never become markup.
_TABLE_TAG = re.compile(r'</?(?:table|thead|tbody|tr|th|td|caption)(?:\s+(?:rowspan|colspan)="\d+")*\s*>', re.I)


def document_text(md: str) -> str:
    """Extracted invoice text as safe HTML: tables kept, line breaks preserved, everything else escaped."""
    out = []
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("<!--"):
            if "PageBreak" in stripped:
                out.append("<hr>")
            continue
        if _TABLE_TAG.match(stripped):
            parts, pos = [], 0
            for m in _TABLE_TAG.finditer(stripped):
                parts += [esc(stripped[pos : m.start()]), m.group(0)]
                pos = m.end()
            out.append("".join(parts) + esc(stripped[pos:]))
        else:
            out.append(esc(line) + "<br>")
    return "\n".join(out)


def timeline(items: Sequence[dict[str, str]]) -> str:
    """A vertical history: each item has icon, tone, title, text (all plain text), who and when."""
    rows = []
    for it in items:
        who = f" · {esc(it['who'])}" if it.get("who") else ""
        rows.append(
            f"<div class='ev'><span class='dot {esc(it.get('tone', 'gray'))}'>{icon(it.get('icon', 'circle'), '1em')}"
            f"</span><div class='body'><div class='t'><b>{esc(it.get('title', ''))}</b>{who}</div>"
            f"<div class='d'>{esc(it.get('text', ''))}</div><div class='w'>{esc(it.get('when', ''))}</div></div></div>"
        )
    return f"<div class='apc-timeline'>{''.join(rows)}</div>" if rows else ""
