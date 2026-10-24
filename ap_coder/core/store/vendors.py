"""The vendor master."""

from __future__ import annotations

import json
import re
from typing import Any

from ..memory import vendor_key
from ..paths import PROJECT_DIR, default_db_path, find_moved_file, private_dir  # noqa: F401  (re-exported)
from ._base import (
    _ACTIVE_IN,
    ACTIVE_STATUSES,
    FAILED,
    _clean_code,
    _in_list,
    _now,
)


class VendorsMixin:
    # --- Vendors ----------------------------------------------------------------------------------------

    def get_vendor(self, key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM vendors WHERE vendor_key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def save_vendor(
        self, key: str, display_name: str, status: str = "active", expected_gst: str = "", notes: str = "",
        actor: str | None = None, qst_number: str | None = None, pst_number: str | None = None,
        province: str | None = None,
    ) -> None:  # fmt: skip
        """Add or change a vendor. QST, PST and province left None keep what the vendor has."""
        if status not in ("active", "on_hold"):
            raise ValueError(f"unknown vendor status {status!r}")
        before = self.get_vendor(key) or {}
        values = {"status": status, "expected_gst": expected_gst.strip(), "notes": notes.strip()}
        extra = {"qst_number": qst_number, "pst_number": pst_number, "province": province}
        values.update({k: str(v).strip().upper() if k == "province" else str(v).strip()
                       for k, v in extra.items() if v is not None})  # fmt: skip
        keep = {k: before.get(k) or "" for k in extra if extra[k] is None}
        row = {**keep, **values}
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO vendors (vendor_key, display_name, status, expected_gst, notes, updated_at, qst_number,
                   pst_number, province)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(vendor_key) DO UPDATE SET
                   display_name = excluded.display_name, status = excluded.status,
                   expected_gst = excluded.expected_gst, notes = excluded.notes, updated_at = excluded.updated_at,
                   qst_number = excluded.qst_number, pst_number = excluded.pst_number, province = excluded.province""",
                (key, display_name, status, values["expected_gst"], values["notes"], _now(), row["qst_number"],
                 row["pst_number"], row["province"]),
            )  # fmt: skip
            changed = {k: v for k, v in values.items() if before.get(k, "active" if k == "status" else "") != v}
            if changed:
                self._log(conn, "vendor_updated", actor=actor, detail={"vendor": display_name, **changed})

    def import_vendor_master(
        self, rows: list[dict[str, Any]], actor: str | None = None, only_new: bool = False
    ) -> dict[str, Any]:
        """Import the ERP's vendor list (``vendors.master_rows`` output). Each vendor is matched by name;
        what the file gives (ERP ID, GST/HST #, terms, default GL, status) replaces what was there; what
        it has no column for is kept.
        Rows whose names normalise to the same vendor are merged (on hold if any of them is) and reported
        in ``duplicates``: often the same supplier set up twice in the ERP. With ``only_new``, vendors
        already in the list are left alone (``inserted`` lists the new ones)."""
        added = updated = 0
        now = _now()
        merged: dict[str, dict[str, Any]] = {}
        names: dict[str, list[str]] = {}
        for row in rows:
            key = vendor_key(row["vendor_name"])
            if not key:
                continue
            names.setdefault(key, []).append(row["vendor_name"])
            if key in merged:
                first = merged[key]
                if row.get("status") == "on_hold":
                    first["status"], first["status_given"] = "on_hold", True
                for field in ("erp_id", "gst", "terms", "default_gl", "qst", "pst", "province"):
                    first[field] = first.get(field) or row.get(field) or first.get(field)
            else:
                merged[key] = dict(row)
        duplicates = [n for n in names.values() if len(n) > 1]
        inserted: list[str] = []
        with self._conn() as conn:
            existing = {r["vendor_key"] for r in conn.execute("SELECT vendor_key FROM vendors")}
            for key, row in merged.items():
                if only_new and key in existing:
                    continue
                conn.execute(
                    """INSERT INTO vendors (vendor_key, display_name, status, expected_gst, notes, updated_at, erp_id,
                       terms, default_gl, in_master, qst_number, pst_number, province)
                       VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, 1, ?, ?, ?)
                       ON CONFLICT(vendor_key) DO UPDATE SET
                       qst_number = CASE WHEN excluded.qst_number != '' THEN excluded.qst_number
                                    ELSE vendors.qst_number END,
                       pst_number = CASE WHEN excluded.pst_number != '' THEN excluded.pst_number
                                    ELSE vendors.pst_number END,
                       province = CASE WHEN excluded.province != '' THEN excluded.province ELSE vendors.province END,
                       erp_id = CASE WHEN ? THEN excluded.erp_id ELSE vendors.erp_id END,
                       terms = CASE WHEN ? THEN excluded.terms ELSE vendors.terms END,
                       default_gl = CASE WHEN ? THEN excluded.default_gl ELSE vendors.default_gl END,
                       in_master = 1, updated_at = excluded.updated_at,
                       expected_gst = CASE WHEN excluded.expected_gst != '' THEN excluded.expected_gst
                                      ELSE vendors.expected_gst END,
                       status = CASE WHEN ? THEN excluded.status ELSE vendors.status END""",
                    (key, row["vendor_name"], row.get("status") or "active", row.get("gst") or "", now,
                     row.get("erp_id") or "", row.get("terms") or "", _clean_code(row.get("default_gl")),
                     row.get("qst") or "", row.get("pst") or "", row.get("province") or "",
                     *(row.get(f) is not None for f in ("erp_id", "terms", "default_gl")),
                     bool(row.get("status_given"))),
                )  # fmt: skip
                if key in existing:
                    updated += 1
                else:
                    added += 1
                    inserted.append(key)
                    existing.add(key)
            self._log(conn, "vendors_imported", actor=actor, detail={
                "added": added, "updated": updated, "duplicates": len(duplicates),
            })  # fmt: skip
        return {"added": added, "updated": updated, "duplicates": duplicates, "inserted": inserted}

    def our_province(self) -> str:
        """Where invoices are delivered to us when they print no ship-to (Settings → Review), "" if not set."""
        return self.get_setting("our_province").strip().upper()

    def site_provinces(self) -> dict[str, str]:
        """{cost center: province} for sites in other provinces than ours (Settings → Review: "CC400=BC" per line)."""
        out: dict[str, str] = {}
        for line in re.split(r"[\n,;]+", self.get_setting("site_provinces")):
            code, sep, province = line.partition("=")
            if sep and code.strip() and province.strip():
                out[code.strip()] = province.strip().upper()
        return out

    def vendor_choices(self) -> list[dict[str, Any]]:
        """The vendors a reviewer picks from on the review screen: the vendor master's and the ones added there; with
        no vendor master, every vendor with an approved invoice too. Sorted by name."""
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM vendors WHERE in_master IN (1, 2)")]
            if not any(r["in_master"] == 1 for r in rows):
                known = {r["vendor_key"] for r in rows}
                # the tax numbers AP gave such a vendor on the Vendors page come along (the review screen fills them)
                own = {r["vendor_key"]: dict(r) for r in conn.execute("SELECT * FROM vendors WHERE in_master = 0")}
                for r in conn.execute(
                    "SELECT vendor_key, MAX(vendor_name) name FROM invoices WHERE status = 'approved' AND vendor_key "
                    "!= '' GROUP BY vendor_key"
                ):
                    if r["vendor_key"] not in known:
                        numbers = {c: own.get(r["vendor_key"], {}).get(c) or ""
                                   for c in ("expected_gst", "qst_number", "pst_number")}  # fmt: skip
                        rows.append({"vendor_key": r["vendor_key"], "display_name": r["name"], "in_master": 0,
                                     **numbers})  # fmt: skip
        return sorted(rows, key=lambda r: str(r.get("display_name") or "").lower())

    def add_vendor_here(
        self, name: str, *, gst: str = "", qst: str = "", pst: str = "", province: str = "", actor: str | None = None,
        invoice_id: int | None = None,
    ) -> str:  # fmt: skip
        """A new vendor added to the vendor list from the review screen (``vendors.ADDED_HERE``): every invoice from
        it needs a person until it is in the vendor master imported from the ERP. Returns its key. A vendor already
        in the list is refused (ValueError)."""
        key = vendor_key(name)
        if not key:
            raise ValueError("Type the vendor's name.")
        before = self.get_vendor(key) or {}
        if before.get("in_master"):
            raise ValueError(f"{before.get('display_name') or name} is already in the vendor list: pick it.")
        note = f"Added by {actor or 'a reviewer'} on {_now()[:10]}"
        note += f" from invoice #{invoice_id}." if invoice_id else "."
        notes = "\n".join(n for n in (note, str(before.get("notes") or "").strip()) if n)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO vendors (vendor_key, display_name, status, expected_gst, notes, updated_at, qst_number,
                   pst_number, province, in_master) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 2)
                   ON CONFLICT(vendor_key) DO UPDATE SET display_name = excluded.display_name,
                   expected_gst = excluded.expected_gst, notes = excluded.notes, updated_at = excluded.updated_at,
                   qst_number = excluded.qst_number, pst_number = excluded.pst_number, province = excluded.province,
                   in_master = 2""",
                (key, name.strip(), before.get("status") or "active", gst.strip(), notes, _now(), qst.strip(),
                 pst.strip(), province.strip().upper()),
            )  # fmt: skip
            self._log(conn, "vendor_added", invoice_id=invoice_id, actor=actor, detail={
                "vendor": name.strip(), "gst": gst.strip(), "qst": qst.strip(), "pst": pst.strip(),
                "province": province.strip().upper()})  # fmt: skip
        return key

    def delete_vendors(self, keys: list[str], actor: str | None = None) -> int:
        """Remove vendors from the vendor list (e.g. the demo's sample vendor master), logged: an on-hold vendor
        removed lifts its hold."""
        where, params = _in_list("vendor_key"), (json.dumps(list(keys)),)
        with self._conn() as conn:
            names = [r[0] or r[1] for r in conn.execute(f"SELECT display_name, vendor_key FROM vendors WHERE {where}",
                                                         params)]  # fmt: skip
            count = conn.execute(f"DELETE FROM vendors WHERE {where}", params).rowcount
            if count:
                self._log(conn, "vendors_deleted", actor=actor, detail={"vendors": names})
            return count

    def has_vendor_master(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM vendors WHERE in_master = 1 LIMIT 1").fetchone() is not None

    def master_vendors(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM vendors WHERE in_master = 1")]

    def vendor_ids(self) -> dict[str, str]:
        """{vendor_key: ERP vendor ID} from the vendor master, plus {"gst:<number>": ERP vendor ID} so an
        invoice whose vendor name differs but whose GST/HST number matches still gets its ID."""
        from ...coding.vendors import norm_tax_number

        with self._conn() as conn:
            rows = conn.execute("SELECT vendor_key, erp_id, expected_gst FROM vendors WHERE erp_id != ''").fetchall()
        ids = {r[0]: r[1] for r in rows}
        for r in rows:
            number = norm_tax_number(r[2])
            if sum(c.isdigit() for c in number) >= 9:
                ids.setdefault(f"gst:{number}", r[1])
                if number[:9].isdigit():  # bn: the master keeps only the Business Number; bnx: the full account
                    ids.setdefault(f"{'bn' if len(number) == 9 else 'bnx'}:{number[:9]}", r[1])
        return ids

    def all_vendor_terms(self) -> dict[str, str]:
        """{vendor_key: payment terms} for every vendor that has terms in the vendor master."""
        with self._conn() as conn:
            return {r[0]: r[1] for r in conn.execute("SELECT vendor_key, terms FROM vendors WHERE terms != ''")}

    def vendor_terms(self, key: str) -> str:
        """The vendor master's payment terms for this vendor ('' if none)."""
        if not key:
            return ""
        with self._conn() as conn:
            row = conn.execute("SELECT terms FROM vendors WHERE vendor_key = ?", (key,)).fetchone()
        return row[0] if row else ""

    def vendor_invoices(self, key: str) -> list[dict[str, Any]]:
        """This vendor's invoices (newest first) with the GST/HST number and bank account each one carried. Read once
        per page while nothing changes (``_session_memo``): the vendor checks and the tax-number notes both ask."""
        return self._session_memo(("vendor_invoices", key), lambda: self._vendor_invoices(key))

    def _vendor_invoices(self, key: str) -> list[dict[str, Any]]:
        # The coding as approved, else as read (a blank one counts as none), by json_extract: no invoice's whole AI
        # output is parsed for five fields.
        doc = "COALESCE(NULLIF(NULLIF(final_output, 'null'), '{}'), NULLIF(ai_output, 'null'), '{}')"
        fields = ", ".join(
            f"json_extract({doc}, '$.{field}') AS {name}"
            for field, name in (
                ("gst_hst_registration_number", "gst_hst_number"), ("qst_registration_number", "qst_number"),
                ("pst_registration_number", "pst_number"), ("remit_bank_account", "bank_account"),
                ("original_invoice_number", "original_invoice_number"),
            )
        )  # fmt: skip
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    f"""SELECT id, status, export_batch, vendor_name, invoice_number, invoice_date, currency,
                              grand_total, adjusted_confidence, requires_review, created_at, reviewed_at, {fields}
                       FROM invoices WHERE vendor_key = ? ORDER BY id DESC""",
                    (key,),
                )
            ]
        for r in rows:
            for name in ("gst_hst_number", "qst_number", "pst_number", "bank_account", "original_invoice_number"):
                r[name] = r[name] or ""
        return rows

    def vendor_invoice_dates(self) -> list[dict[str, Any]]:
        """Vendor, date and total of every invoice in review or approved (for ``recurring.detect``)."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT vendor_key, vendor_name, invoice_date, grand_total, currency FROM invoices "
                    "WHERE vendor_key != '' AND status IN " + _ACTIVE_IN,
                    ACTIVE_STATUSES,
                )
            ]

    def has_other_vendors(self, key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM invoices WHERE vendor_key != ? AND vendor_key != '' AND status != ? LIMIT 1",
                (key, FAILED),
            ).fetchone()
        return row is not None

    def vendor_summaries(self) -> list[dict[str, Any]]:
        """One row per vendor seen on an invoice: counts, spend, dates, AI accuracy and AP's settings."""
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT vendor_key, MAX(vendor_name) vendor_name, COUNT(*) invoices,
                              SUM(status = 'approved') approved, SUM(status = 'review') to_review,
                              SUM(CASE WHEN status = 'approved' AND COALESCE(NULLIF(currency, ''), 'CAD') = 'CAD'
                                       THEN grand_total ELSE 0 END) spend_cad,
                              MIN(invoice_date) first_invoice, MAX(invoice_date) last_invoice,
                              MIN(created_at) first_seen
                       FROM invoices WHERE vendor_key != '' AND status != 'failed' GROUP BY vendor_key"""
                )
            ]
            lessons = {
                r["vendor_key"]: dict(r)
                for r in conn.execute(
                    """SELECT vendor_key, COUNT(*) lessons, SUM(outcome NOT IN ('history', 'coded', 'bulk')) lines,
                              SUM(outcome = 'accepted') accepted FROM feedback GROUP BY vendor_key"""
                )
            }
            masters = {r["vendor_key"]: dict(r) for r in conn.execute("SELECT * FROM vendors")}
            spend: dict[str, dict[str, float]] = {}
            for r in conn.execute(
                """SELECT vendor_key, COALESCE(NULLIF(UPPER(TRIM(currency)), ''), 'CAD') currency,
                          SUM(grand_total) total
                   FROM invoices WHERE vendor_key != '' AND status = 'approved' GROUP BY vendor_key, 2"""
            ):
                spend.setdefault(r["vendor_key"], {})[r["currency"]] = round(r["total"] or 0, 2)
        rates = self.fx_rates()
        for r in rows:
            # Per currency: dollars and euros do not add up, and a USD-only vendor did not spend 0.00 CAD.
            r["spend"] = dict(sorted(spend.get(r["vendor_key"], {}).items()))
            # In CAD at the Settings rates (for sorting; a currency without a rate counts at face value).
            r["spend_in_cad"] = round(sum(t * rates.get(c, 1.0) for c, t in r["spend"].items()), 2)
            fb = lessons.get(r["vendor_key"]) or {}
            r["lessons"] = fb.get("lessons", 0)
            r["accuracy"] = (fb["accepted"] / fb["lines"]) if fb.get("lines") else None
            master = masters.get(r["vendor_key"]) or {}
            r["status"] = master.get("status", "active")
            r["expected_gst"] = master.get("expected_gst", "")
            for field in ("qst_number", "pst_number", "province"):
                r[field] = master.get(field) or ""
            r["notes"] = master.get("notes", "")
            for field in ("erp_id", "terms", "default_gl"):
                r[field] = master.get(field) or ""
            r["in_master"] = master.get("in_master") == 1
            r["added_here"] = master.get("in_master") == 2
            r["has_record"] = bool(master)  # in the vendor list, added here or set up by AP (on hold, notes)
        return sorted(rows, key=lambda r: -r["spend_in_cad"])

    def vendor_name_checks(self, ids: list[int]) -> dict[int, str]:
        """{invoice id: how sure the capture is of its vendor name} ("verified", "likely", "check", "missing"), in
        one query; an invoice with no capture is left out (the review queue's "check" mark, B-471)."""
        if not ids:
            return {}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT invoice_id, json_extract(capture_json, '$.fields.vendor_name.status') FROM invoice_capture "
                f"WHERE {_in_list('invoice_id')}",
                (json.dumps([int(i) for i in ids]),),
            ).fetchall()
        return {int(r[0]): str(r[1] or "") for r in rows}

    def vendor_gl_usage(self, key: str) -> list[dict[str, Any]]:
        """GL accounts reviewers used for this vendor's lines, most used first."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = 'corrected') corrected FROM feedback
                       WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC""",
                    (key,),
                )
            ]
