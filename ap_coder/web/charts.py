"""Charts drawn on the server as inline SVG: a line, bars (one series, stacked or grouped), with axes and labels.

No chart library and nothing from the internet: each function returns the ``<svg>`` as ``Markup`` for a template
(``{{ chart }}``). Every text in it (title, labels, series names) is escaped here, so data from invoices is safe.

Each chart is an image to a screen reader (``role="img"``) named by its ``<title>`` and described by its ``<desc>``;
every point and bar has its own ``<title>``, which the browser shows as a tooltip on hover. Pages also offer the
numbers as a table (the chart is never the only way to read them).

Marks follow the dashboard's chart rules: 2px lines with 8px markers ringed in the surface colour, a 10% area wash,
bars at most 24px thick with a 4px rounded end and a square base, a 2px gap between stacked segments, hairline
solid gridlines, and series colours in a fixed order (never by rank). Text is in text colours, never a series colour.
"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from markupsafe import Markup, escape

# Categorical colours, in this fixed order (the dataviz reference palette; adjacent pairs stay apart for
# colour-blind readers). A chart takes them in order; it never cycles past the last one.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
OK_GREEN, WARN_AMBER = "#1baf7a", "#eda100"  # "approved as coded" / "corrected" (aqua and yellow slots)
TARGET_GRAY = "#8a8985"
GRID = "#eef0f3"
AXIS = "#d5d9e0"
TEXT = "#5b6474"  # axis labels (--lg-muted)
INK = "#0f172a"  # values and legend (--lg-text)
SURFACE = "#ffffff"

WIDTH, HEIGHT = 720, 260
BAR_MAX = 24  # px: a bar never fills its slot
_ids = itertools.count(1)


@dataclass(frozen=True)
class Series:
    """One series: its name (legend, tooltips), one value per label (None: no value) and its colour."""

    name: str
    values: Sequence[float | None]
    color: str = SERIES[0]


Formatter = Callable[[float], str]


def compact(value: float) -> str:
    """0, 950, 1.2K, 12K, 3.4M: an axis tick."""
    n = abs(value)
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= size:
            text = f"{value / size:.1f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
    return f"{value:,.0f}" if n >= 10 or value == int(value) else f"{value:g}"


def percent(value: float) -> str:
    return f"{value:.0%}"


def nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    """Round tick values covering ``low``..``high`` (0 is always one of them when the range crosses it)."""
    low, high = min(low, 0.0), max(high, 0.0)
    if high == low:
        high = low + 1
    raw = (high - low) / max(count - 1, 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = math.floor(low / step) * step
    ticks, value = [], start
    while value < high + step * 0.5:
        ticks.append(round(value, 10))
        if value >= high:
            break
        value += step
    return ticks


def _id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:24] or "chart"
    return f"apc-chart-{slug}-{next(_ids)}"


def _num(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _open(title: str, desc: str, width: int, height: int) -> tuple[list[str], str]:
    cid = _id(title)
    parts = [
        f'<svg class="apc-chart" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-labelledby="{cid}-t {cid}-d" preserveAspectRatio="xMidYMid meet">',
        f'<title id="{cid}-t">{escape(title)}</title><desc id="{cid}-d">{escape(desc or title)}</desc>',
    ]
    return parts, cid


def _legend(series: Sequence[Series], x: float, y: float) -> str:
    """A key for two or more series (identity never rests on colour alone): swatch, then the name in ink."""
    if len(series) < 2:
        return ""
    out, cursor = [f'<g class="legend" font-size="12" fill="{INK}">'], x
    for s in series:
        out.append(f'<rect x="{_num(cursor)}" y="{_num(y - 9)}" width="10" height="10" rx="2" fill="{s.color}"/>')
        out.append(f'<text x="{_num(cursor + 15)}" y="{_num(y)}">{escape(s.name)}</text>')
        cursor += 15 + 7 * len(s.name) + 20
    out.append("</g>")
    return "".join(out)


def _y_axis(ticks: list[float], y: Callable[[float], float], left: float, right: float, fmt: Formatter) -> str:
    out = ['<g class="grid">']
    for t in ticks:
        yy = _num(y(t))
        color = AXIS if t == 0 else GRID
        out.append(
            f'<line x1="{_num(left)}" x2="{_num(right)}" y1="{yy}" y2="{yy}" stroke="{color}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{_num(left - 8)}" y="{yy}" dy="4" text-anchor="end" font-size="11" fill="{TEXT}">'
            f"{escape(fmt(t))}</text>"
        )
    out.append("</g>")
    return "".join(out)


def _x_labels(labels: Sequence[str], centers: Sequence[float], y: float, slot: float) -> str:
    """The category labels under the plot; when they would collide, every n-th (always the last one)."""
    longest = max((len(str(label)) for label in labels), default=1)
    every = max(1, math.ceil(longest * 6.6 / max(slot, 1)))
    out = ['<g class="x" font-size="11" text-anchor="middle">']
    last = len(labels) - 1
    for i, (label, cx) in enumerate(zip(labels, centers, strict=True)):
        if i % every and i != last:
            continue
        if i != last and last - i < every and last % every:
            continue  # leave room for the last label
        out.append(f'<text x="{_num(cx)}" y="{_num(y)}" fill="{TEXT}">{escape(label)}</text>')
    out.append("</g>")
    return "".join(out)


def _frame(series: Sequence[Series], with_legend: bool, width: int, height: int) -> tuple[float, float, float, float]:
    """left, top, right, bottom of the plot area."""
    return 56.0, 40.0 if with_legend and len(series) > 1 else 16.0, width - 16.0, height - 30.0


def line_chart(labels: Sequence[str], series: Series | Sequence[Series], *, title: str, desc: str = "",
               y_format: Formatter = compact, y_domain: tuple[float, float] | None = None,
               target: tuple[float, str] | None = None, area: bool = True,
               tooltip: Callable[[int, Series], str] | None = None, width: int = WIDTH,
               height: int = HEIGHT) -> Markup:  # fmt: skip
    """A line per series over ``labels`` (e.g. weeks). ``y_domain``: fixed (low, high), e.g. (0, 1) for a rate;
    ``target``: (value, label) drawn as a dashed reference line; ``tooltip(i, series)``: a point's hover text.
    ``width`` / ``height``: the drawing's size in px at 100% (it scales with its box: pick the width of the card it
    sits in, so the 11px labels stay about 11px)."""
    all_series = [series] if isinstance(series, Series) else list(series)
    parts, _ = _open(title, desc, width, height)
    left, top, right, bottom = _frame(all_series, True, width, height)
    values = [v for s in all_series for v in s.values if v is not None]
    if target:
        values.append(target[0])
    low, high = y_domain if y_domain else (min(values, default=0.0), max(values, default=1.0))
    ticks = nice_ticks(low, high) if not y_domain else nice_ticks(y_domain[0], y_domain[1])
    lo, hi = ticks[0], ticks[-1]
    span = (hi - lo) or 1.0

    def y(v: float) -> float:
        return bottom - (v - lo) / span * (bottom - top)

    n = len(labels)
    slot = (right - left) / max(n, 1)
    centers = [left + slot * (i + 0.5) for i in range(n)]
    parts.append(_y_axis(ticks, y, left, right, y_format))
    if target:
        ty = _num(y(target[0]))
        parts.append(
            f'<g class="target"><line x1="{_num(left)}" x2="{_num(right)}" y1="{ty}" y2="{ty}" stroke="{TARGET_GRAY}" '
            f'stroke-width="1" stroke-dasharray="4 4"/><text x="{_num(left + 4)}" y="{ty}" dy="-6" font-size="11" '
            f'fill="{TEXT}">{escape(target[1])}</text></g>'
        )
    for s in all_series:
        runs: list[list[tuple[float, float]]] = [[]]
        for cx, v in zip(centers, s.values, strict=True):
            if v is None:
                runs.append([])
            else:
                runs[-1].append((cx, y(v)))
        for run in (r for r in runs if r):
            path = " ".join(f"{'M' if i == 0 else 'L'}{_num(px)} {_num(py)}" for i, (px, py) in enumerate(run))
            if area and len(all_series) == 1 and len(run) > 1:
                base = _num(y(max(lo, 0.0)))
                parts.append(
                    f'<path d="{path} L{_num(run[-1][0])} {base} L{_num(run[0][0])} {base} Z" fill="{s.color}" '
                    'fill-opacity="0.1" stroke="none"/>'
                )
            parts.append(
                f'<path d="{path}" fill="none" stroke="{s.color}" stroke-width="2" stroke-linejoin="round" '
                'stroke-linecap="round"/>'
            )
        for i, (cx, v) in enumerate(zip(centers, s.values, strict=True)):
            if v is None:
                continue
            text = tooltip(i, s) if tooltip else f"{labels[i]}: {y_format(v)}"
            if len(all_series) > 1 and not tooltip:
                text = f"{s.name}, {text}"
            parts.append(
                f'<g class="pt"><title>{escape(text)}</title><circle cx="{_num(cx)}" cy="{_num(y(v))}" r="9" '
                f'fill="transparent"/><circle cx="{_num(cx)}" cy="{_num(y(v))}" r="4" fill="{s.color}" '
                f'stroke="{SURFACE}" stroke-width="2"/></g>'
            )
    parts.append(_legend(all_series, left, 18))
    parts.append(_x_labels([str(label) for label in labels], centers, bottom + 18, slot))
    parts.append("</svg>")
    return Markup("".join(parts))  # noqa: S704 - every text above went through escape()


def _bar(x: float, width: float, y0: float, y1: float, round_end: bool) -> str:
    """A bar from the baseline ``y0`` to ``y1``: square at the base, 4px rounded at its data end."""
    top, bottom = min(y0, y1), max(y0, y1)
    h = bottom - top
    r = min(4.0, h / 2, width / 2) if round_end else 0.0
    if r <= 0.5:
        return f'<rect x="{_num(x)}" y="{_num(top)}" width="{_num(width)}" height="{_num(max(h, 0.0))}"/>'
    x2 = x + width
    if y1 <= y0:  # grows upward: rounded at the top
        d = (f"M{_num(x)} {_num(bottom)} V{_num(top + r)} Q{_num(x)} {_num(top)} {_num(x + r)} {_num(top)} "
             f"H{_num(x2 - r)} Q{_num(x2)} {_num(top)} {_num(x2)} {_num(top + r)} V{_num(bottom)} Z")  # fmt: skip
    else:  # a negative value grows downward: rounded at the bottom
        d = (f"M{_num(x)} {_num(top)} V{_num(bottom - r)} Q{_num(x)} {_num(bottom)} {_num(x + r)} {_num(bottom)} "
             f"H{_num(x2 - r)} Q{_num(x2)} {_num(bottom)} {_num(x2)} {_num(bottom - r)} V{_num(top)} Z")  # fmt: skip
    return f'<path d="{d}"/>'


def bar_chart(labels: Sequence[str], series: Series | Sequence[Series], *, title: str, desc: str = "",
              mode: str = "stacked", y_format: Formatter = compact, value_format: Formatter | None = None,
              tooltip: Callable[[int, Series, float], str] | None = None, legend: bool = True,
              width: int = WIDTH, height: int = HEIGHT) -> Markup:  # fmt: skip
    """Columns over ``labels``: one series, or several ``mode="stacked"`` (segments on one column, a 2px gap
    between them) or ``mode="grouped"`` (side by side). ``value_format``: the numbers in tooltips (defaults to
    ``y_format``); ``tooltip(i, series, value)``: a bar's hover text. ``legend=False``: the page draws the key
    itself (``legend_items``), e.g. for many or long series names. ``width`` / ``height``: as ``line_chart``."""
    if mode not in ("stacked", "grouped"):
        raise ValueError(f"unknown bar chart mode {mode!r}")
    all_series = [series] if isinstance(series, Series) else list(series)
    value_format = value_format or y_format
    parts, _ = _open(title, desc, width, height)
    left, top, right, bottom = _frame(all_series, legend, width, height)
    n = len(labels)
    stacked = mode == "stacked"
    if stacked:
        ups = [sum(max(s.values[i] or 0, 0) for s in all_series) for i in range(n)]
        downs = [sum(min(s.values[i] or 0, 0) for s in all_series) for i in range(n)]
        low, high = min(downs, default=0.0), max(ups, default=0.0)
    else:
        values = [v or 0.0 for s in all_series for v in s.values]
        low, high = min(values, default=0.0), max(values, default=0.0)
    ticks = nice_ticks(low, high)
    lo, hi = ticks[0], ticks[-1]
    span = (hi - lo) or 1.0

    def y(v: float) -> float:
        return bottom - (v - lo) / span * (bottom - top)

    slot = (right - left) / max(n, 1)
    centers = [left + slot * (i + 0.5) for i in range(n)]
    parts.append(_y_axis(ticks, y, left, right, y_format))
    gap = 2.0
    if stacked:
        width = min(BAR_MAX, slot * 0.6)
    else:
        width = min(BAR_MAX, (slot * 0.75 - gap * (len(all_series) - 1)) / max(len(all_series), 1))
    for i, cx in enumerate(centers):
        up = down = 0.0
        # The outermost segment of each direction gets the rounded end.
        last_up = max((k for k, s in enumerate(all_series) if (s.values[i] or 0) > 0), default=-1)
        last_down = max((k for k, s in enumerate(all_series) if (s.values[i] or 0) < 0), default=-1)
        for k, s in enumerate(all_series):
            v = s.values[i]
            if not v:
                continue
            if stacked:
                x = cx - width / 2
                start = up if v > 0 else down
                end = start + v
                if v > 0:
                    up = end
                else:
                    down = end
                y0, y1 = y(start), y(end)
                if start != 0:  # the 2px surface gap between touching segments
                    y0 = y0 - gap if v > 0 else y0 + gap
                    if (v > 0 and y0 <= y1) or (v < 0 and y0 >= y1):
                        y0 = y1 + (0.5 if v > 0 else -0.5)
                rounded = k == (last_up if v > 0 else last_down)
            else:
                total = width * len(all_series) + gap * (len(all_series) - 1)
                x = cx - total / 2 + k * (width + gap)
                y0, y1 = y(0), y(v)
                rounded = True
            text = tooltip(i, s, v) if tooltip else f"{labels[i]}: {value_format(v)}"
            if len(all_series) > 1 and not tooltip:
                text = f"{labels[i]}, {s.name}: {value_format(v)}"
            mark = _bar(x, width, y0, y1, rounded)
            parts.append(f'<g class="bar" fill="{s.color}"><title>{escape(text)}</title>{mark}</g>')
    if legend:
        parts.append(_legend(all_series, left, 18))
    parts.append(_x_labels([str(label) for label in labels], centers, bottom + 18, slot))
    parts.append("</svg>")
    return Markup("".join(parts))  # noqa: S704 - every text above went through escape()


def legend_items(series: Sequence[Series]) -> list[tuple[str, str]]:
    """(name, colour) for a key drawn in HTML next to a chart made with ``legend=False``."""
    return [(s.name, s.color) for s in series]
