"""Audit trail: what changed between the AI's suggestion and what was approved, and how to show events.

The store records an event for everything that changes data (processing, approvals with the exact
field changes, rejections, deletions, GL and tax setup changes, forgotten lessons, exports,
restores). This module computes the approval differences and turns events into readable lines.
"""

from __future__ import annotations

from typing import Any

from .memory import pair_lines

HEADER_LABELS = {
    "vendor_name": "vendor", "invoice_number": "invoice #", "invoice_date": "date", "currency": "currency",
    "supplier_province": "supplier province", "ship_to_province": "place of supply",
    "gst_hst_registration_number": "GST/HST #", "qst_registration_number": "QST #", "subtotal": "subtotal",
    "tax_total": "tax total", "grand_total": "total",
}  # fmt: skip
LINE_LABELS = {
    "predicted_gl_code": "GL", "predicted_cost_center": "cost center", "amount": "amount",
    "description": "description", "taxes_applied": "taxes",
}  # fmt: skip

# action -> (Material icon, tone, label)
ACTIONS = {
    "processed": ("document_scanner", "info", "Processed"),
    "failed": ("error", "err", "Could not be processed"),
    "approved": ("task_alt", "ok", "Approved"),
    "rejected": ("block", "gray", "Rejected"),
    "deleted": ("delete", "gray", "Deleted"),
    "exported": ("ios_share", "violet", "Exported"),
    "accounts_imported": ("upload", "info", "Accounts imported"),
    "accounts_edited": ("edit", "info", "Accounts edited"),
    "accounts_deleted": ("delete", "warn", "Accounts deleted"),
    "tax_setup_changed": ("percent", "info", "Tax setup changed"),
    "policy_changed": ("rule", "info", "Coding policy changed"),
    "lessons_forgotten": ("delete_sweep", "warn", "Lessons forgotten"),
    "settings_changed": ("settings", "info", "Settings changed"),
    "backup_made": ("backup", "info", "Backup made"),
    "backup_restored": ("settings_backup_restore", "warn", "Backup restored"),
    "vendor_updated": ("storefront", "info", "Vendor updated"),
    "export_undone": ("undo", "warn", "Export undone"),
}


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "(blank)"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "(none)"
    return str(value)


def _tax_signature(doc: dict[str, Any]) -> list[tuple]:
    return sorted(
        (t.get("tax_type"), t.get("province") or "", round(float(t.get("rate") or 0), 6),
         round(float(t.get("taxable_amount") or 0), 2), round(float(t.get("tax_amount") or 0), 2))
        for t in doc.get("tax_lines") or []
    )  # fmt: skip


def diff_coding(ai: dict[str, Any], final: dict[str, Any]) -> list[dict[str, str]]:
    """Every change a reviewer made: header fields, line fields, added/removed lines, tax lines."""
    changes: list[dict[str, str]] = []
    for field, label in HEADER_LABELS.items():
        before, after = ai.get(field), final.get(field)
        if _fmt(before) != _fmt(after):
            changes.append({"what": label, "before": _fmt(before), "after": _fmt(after)})
    ai_lines, final_lines = ai.get("line_items") or [], final.get("line_items") or []
    matched = set()
    for original, line in pair_lines(ai_lines, final_lines):
        n = line.get("line_number")
        if original is None:
            changes.append({"what": f"line {n} added", "before": "", "after": _line_summary(line)})
            continue
        matched.add(id(original))
        for field, label in LINE_LABELS.items():
            before, after = original.get(field), line.get(field)
            if field == "taxes_applied":
                before, after = sorted(before or []), sorted(after or [])
            if _fmt(before) != _fmt(after):
                changes.append({"what": f"line {n} {label}", "before": _fmt(before), "after": _fmt(after)})
    for original in ai_lines:
        if id(original) not in matched:
            changes.append(
                {"what": f"line {original.get('line_number')} removed", "before": _line_summary(original), "after": ""}
            )
    if _tax_signature(ai) != _tax_signature(final):
        changes.append({"what": "sales tax lines", "before": _tax_text(ai), "after": _tax_text(final)})
    return changes


def _line_summary(line: dict[str, Any]) -> str:
    return f"{line.get('description', '')[:40]} · {_fmt(line.get('amount'))} · GL {line.get('predicted_gl_code', '')}"


def _tax_text(doc: dict[str, Any]) -> str:
    return (
        "; ".join(
            f"{t.get('tax_type')} {float(t.get('rate') or 0) * 100:g}% = {float(t.get('tax_amount') or 0):,.2f}"
            for t in doc.get("tax_lines") or []
        )
        or "(none)"
    )


def describe(event: dict[str, Any]) -> str:
    """One readable sentence for an event (plain text; escape before putting it in HTML)."""
    d = event.get("detail") or {}
    action = event["action"]
    if action == "processed":
        flag = "needs attention" if d.get("requires_review") else "ready"
        conf = d.get("confidence")
        conf_text = f", confidence {conf:.0%}" if isinstance(conf, (int, float)) else ""
        return f"{d.get('file', '')} read and coded{conf_text} ({flag})" + (" · demo" if d.get("demo") else "")
    if action == "failed":
        return f"{d.get('file', '')}: {str(d.get('error', ''))[:160]}"
    if action == "approved":
        changes = d.get("changes") or []
        if not changes:
            bulk = " (bulk approval)" if d.get("bulk") else ""
            return f"{d.get('lines', 0)} line(s), all as the AI suggested{bulk}"
        shown = "; ".join(f"{c['what']}: {c['before']} → {c['after']}" for c in changes[:4])
        more = f" (+{len(changes) - 4} more)" if len(changes) > 4 else ""
        return f"{len(changes)} change(s): {shown}{more}"
    if action == "rejected":
        return f"reason: {d.get('reason') or 'none given'}"
    if action == "deleted":
        return f"{d.get('vendor') or d.get('file', '')} {d.get('invoice_number') or ''}".strip()
    if action == "exported":
        return f"in export batch {d.get('batch', '')}"
    if action == "export_undone":
        return f"batch {d.get('batch', '')}: {d.get('invoices', 0)} invoice(s) back in the ready-to-export list"
    if action == "accounts_imported":
        return f"{d.get('table', '')}: {d.get('added', 0)} added, {d.get('updated', 0)} updated" + (
            " (replaced the list)" if d.get("replace_all") else ""
        )
    if action in ("accounts_edited", "accounts_deleted"):
        codes = d.get("codes") or []
        return f"{d.get('table', '')}: {', '.join(codes[:8])}{' …' if len(codes) > 8 else ''}"
    if action == "tax_setup_changed":
        return f"{d.get('tax_type')}: {d.get('treatment')}" + (f" → GL {d['gl_code']}" if d.get("gl_code") else "")
    if action == "lessons_forgotten":
        return f"{d.get('count', 0)} lesson(s)"
    if action == "settings_changed":
        return ", ".join(d.get("keys") or [])
    if action == "vendor_updated":
        parts = []
        if "status" in d:
            parts.append("put on hold" if d["status"] == "on_hold" else "made active")
        if "expected_gst" in d:
            parts.append(f"expected GST/HST # {d['expected_gst'] or '(cleared)'}")
        if "notes" in d:
            parts.append("notes changed")
        return f"{d.get('vendor', '')}: {', '.join(parts)}"
    if action in ("backup_made", "backup_restored"):
        return str(d.get("file", ""))
    return ""
