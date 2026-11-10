"""The vendor master as the ERP gives it: its import (``import_vendor_master``, a file's rows or a page of the JD
Edwards sync), what its rows say (JD Edwards' hold payment codes, ``hold_code``; a name marked as retired,
``name_marker``; a person listed "LAST, FIRST M.", ``person_name``), the vendor a name is when none is kept under it
as written (``_vendor_named_otherwise``, for ``find_vendor``), and what the rest reads of the master: its vendors,
their ERP IDs (``vendor_ids``), the one a GST/HST number belongs to. The vendor list itself (a vendor's own row, its
invoices, the Vendors page) is ``vendors``, which keeps every name of this part importable from there too."""

from __future__ import annotations

import bisect
import json
import re
from typing import Any

from ..chart import markers as _markers  # (how a skip word is found in a name: the chart's, for both, D-209)
from ..keys import BUSINESS_NUMBER, norm_tax_number, same_tax_number
from ..memory import _LEGAL_SUFFIXES, _normalise, vendor_key
from ._base import _clean_code, _in_list, _now
from .dialect import ROWID, like
from .summary import fetch_rows

# JD Edwards' hold payment codes that put a supplier on hold (a setting; the Vendors page's import shows it). "N", its
# usual value, and blank never do (B-2081). "Y" and "1" hold payments and stop new vouchers, "2" holds payments only;
# another code a company uses ("0") holds once it is listed.
HOLD_CODES_SETTING = "jde_hold_codes"
HOLD_CODES = "Y, 1, 2"
NEVER_HOLD = ("", "N")
# JD Edwards' "2": the supplier's payments are held, its vouchers still entered. AP Coder codes and keys vouchers and
# pays nothing, so the invoice goes on with a warning (VENDOR_PAYMENTS_HELD), not held as one of ``Y`` or ``1`` is
# (B-2157).
PAYMENTS_ONLY = ("2",)


def stops_vouchers(code: Any) -> bool:
    """Whether a hold payment code a vendor keeps (``vendors.erp_hold``) stops the vendor's invoices and vouchers:
    any but blank and the codes that hold payments only."""
    return bool(code) and str(code).strip().upper() not in PAYMENTS_ONLY


def other_holds(rows: list[dict[str, Any]], holding: list[str]) -> dict[str, int]:
    """{a hold payment code of an import's rows that is neither "N", blank nor one that holds: how many suppliers
    have it}: what the import says it did not treat as a hold (B-2159)."""
    counted: dict[str, int] = {}
    for row in rows:
        code = str(row.get("hold") or "").strip().upper()
        if code not in NEVER_HOLD and not hold_code(code, holding):
            counted[code] = counted.get(code, 0) + 1
    return counted


def holds_as_listed(conn: Any, holding: list[str]) -> None:
    """A hold payment code a vendor keeps that the list does not name (any more) is no hold: blanked on every vendor,
    whichever of them an import or a sync lists (B-2158)."""
    codes = sorted({code.upper() for code in holding} - set(NEVER_HOLD))
    conn.execute(f"UPDATE vendors SET erp_hold = '' WHERE erp_hold != '' AND NOT {_in_list('UPPER(TRIM(erp_hold))')}",
                 (json.dumps(codes),))  # fmt: skip


def listed(text: Any) -> list[str]:
    """A setting's list as its items, in the order written: "Y, 1, 2" is ["Y", "1", "2"]."""
    return [item.strip() for item in str(text or "").split(",") if item.strip()]


def hold_code(code: Any, holding: list[str]) -> str:
    """JD Edwards' hold payment code as a vendor keeps it (``vendors.erp_hold``): the code when it is one of
    ``holding`` (``Store.hold_codes``), "" when it is not a hold ("N", blank, a code not listed). The one place that
    says what a code means: whatever reads ``erp_hold`` reads a hold (B-2081)."""
    code = str(code or "").strip().upper()
    return code if code not in NEVER_HOLD and code in {c.upper() for c in holding} else ""


# A supplier whose name the ERP marks as retired (the words: the setting ``skip_words``, ``Store.skip_words``, whose
# default is ``vendors.SKIP_WORDS``; STAR in the list: a star before or after the name marks one too).
STAR = "*"
MARKED = "marked {} in the supplier master"  # the note of a vendor on hold for its marker (B-2083)
MARKED_LIKE = MARKED.format("%")
_WAS_MARKED = like("vendors.notes")  # (with MARKED_LIKE) a vendor whose note is a marker's: on hold for it


def name_marker(name: Any, words: list[str]) -> tuple[str, str]:
    """(the name without it, the marker) of a name the ERP marks as retired: one of ``words`` (``Store.skip_words``)
    in any case and spacing ("NORTHERN PAPER CO  DO NOT USE", "DNU - Old Print Shop"), or, with STAR among them, a
    star before or after the name ("*LAKESIDE CATERING"); the name as it is and "" for any other. The marker is not
    part of the vendor's key: the supplier is found by its real name, and stopped (B-2083)."""
    text = str(name or "")
    for said, pattern in _markers(words):
        found = pattern.search(text)
        if found:
            return f"{text[: found.start()]} {text[found.end() :]}".strip(), said
    plain = text.strip()
    if STAR in words and (plain.startswith(STAR) or plain.endswith(STAR)):
        return plain.strip("* "), STAR
    return text, ""


# Words no person's name has: a name with one of them is a company's, however it is written.
_COMPANY_WORDS = _LEGAL_SUFFIXES | {
    "of", "the", "for", "group", "groupe", "service", "services", "associates", "associes", "partners", "holdings",
    "enterprises", "entreprises", "city", "ville", "town", "university", "club", "association", "foundation",
}  # fmt: skip
_NOT_IN_A_NAME = re.compile(r"[\d&/@+#]")
Person = tuple[tuple[str, ...], str, tuple[str, ...]]  # (the surname's words, the first name, middle names or initials)


def person_name(name: Any) -> Person | None:
    """A person's name written as an ERP lists people, "LAST, FIRST M." ("DOE, JANE Q." is (("doe",), "jane",
    ("q",))); None for any other name. Only a name with exactly one comma, no digit and no company word is read so:
    "Smith, Brown & Co" and "Toronto, City of" are companies."""
    text = str(name or "")
    if text.count(",") != 1 or _NOT_IN_A_NAME.search(text):
        return None
    last, given = (tuple(_normalise(part).split()) for part in text.split(","))
    if not (1 <= len(last) <= 3 and 1 <= len(given) <= 3) or _COMPANY_WORDS & {*last, *given}:
        return None
    return last, given[0], given[1:]


def _plain_words(name: Any) -> tuple[str, ...]:
    """The words of a name written in the usual order ("Jane Q. Doe") when it could be a person's: 2 to 6 words, no
    comma, digit or company word; () otherwise."""
    text = str(name or "")
    words = tuple(_normalise(text).split())
    if "," in text or _NOT_IN_A_NAME.search(text) or not 2 <= len(words) <= 6 or _COMPANY_WORDS & set(words):
        return ()
    return words


def _same_middles(a: tuple[str, ...], b: tuple[str, ...], exactly: bool = False) -> bool:
    """Whether two people's middle names can be one person's: either gives none ("Jane Doe" for "DOE, JANE Q."), or
    each is the other's or its initial ("q" for "quinn"). "Q." and "R." are two people. ``exactly``: as many, too."""
    if not a or not b:
        return not exactly or a == b
    return len(a) == len(b) and all(x.startswith(y) or y.startswith(x) for x, y in zip(a, b, strict=True))


def same_person(person: Person, other: Any, exactly: bool = False) -> bool:
    """Whether ``other`` (a name as printed or as a vendor's, in either order) names ``person``: the same surname and
    first name, and middle names that can be the same (``_same_middles``). A different first name is another person,
    whatever the surname."""
    last, first, middles = person
    theirs = person_name(other)
    if theirs:
        return theirs[:2] == (last, first) and _same_middles(theirs[2], middles, exactly)
    words = _plain_words(other)
    cut = len(words) - len(last)
    return cut >= 1 and words[0] == first and words[cut:] == last and _same_middles(words[1:cut], middles, exactly)


def _distinct(first: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether two vendor master rows whose names read the same are two suppliers: each has an ERP ID of its own. The
    supplier's number is the identity whenever the list gives numbers (B-2161: before, each also needed its own
    GST/HST number, B-930, which JD Edwards' list does not give). A list with no numbers cannot tell them apart: the
    same supplier set up twice, merged, as before."""
    a, b = str(first.get("erp_id") or "").strip(), str(row.get("erp_id") or "").strip()
    return bool(a and b) and a != b


def same_number(a: Any, b: Any) -> bool:
    """Whether two supplier numbers are the same, leading zeros and spaces aside ("0410002" and 410002)."""
    a, b = str(a or "").strip().lstrip("0"), str(b or "").strip().lstrip("0")
    return bool(a) and a == b


def erp_stopped(vendor: dict[str, Any]) -> bool:
    """Whether the ERP itself stops a vendor of the list: its name was marked as retired (its note is the marker's),
    or its hold payment code stops vouchers. AP's own hold is not the ERP's: such a vendor is still one in use."""
    return bool(re.fullmatch(MARKED.format(".+"), vendor.get("notes") or "")) or stops_vouchers(vendor.get("erp_hold"))


def sharing(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The vendors in use that share one name, each under a supplier number of its own, among the vendors of that
    name (the one under the name's key and its "#<ERP ID>" ones), by number; [] when the name is one supplier's. The
    number is the identity: the name alone does not say which of them an invoice is from (B-2161)."""
    live = {str(r["erp_id"]).strip(): r for r in rows if str(r.get("erp_id") or "").strip() and not erp_stopped(r)}
    return [live[number] for number in sorted(live)] if len(live) > 1 else []


def with_number(vendor: dict[str, Any]) -> str:
    """A vendor's name with its supplier number, as the review screen offers one of several by a name and as an
    invoice keeps the pick: "LAKESIDE CATERING LTD #2004"."""
    return f"{vendor['display_name']} #{str(vendor['erp_id']).strip()}"


_NUMBERED = re.compile(r"\s*(.*?\S)\s*#\s*(\d{1,12})\s*")


def named_otherwise(name: str, words: list[str], like: Any) -> dict[str, Any] | None:
    """The vendor of a name no vendor is kept under as it is written: the ONE rule the checks (``find_vendor``) and
    the exports' supplier number (``VendorIds.number_of``) both follow (B-2162). ``like(key, more)``: the vendors
    under a key and its "#<ERP ID>" ones (``more``: every key starting with it as a word), each with its
    ``vendor_key``, ``display_name`` and ``erp_id``.

    * A name with a supplier number ("LAKESIDE CATERING LTD #2004", a pick among the suppliers of one name) is the
      vendor of that name holding that number (B-2161).
    * A name with a retired supplier's marker (picked as the master shows it: "NORTHERN PAPER CO  DO NOT USE") is the
      supplier kept under the name without it, the one shown under that very name first (B-2083).
    * A person's name is the one vendor that person is, written the other way round or without a middle initial:
      "Jane Q. Doe" and "Jane Doe" for "DOE, JANE Q.", "Doe, Jane Q." for "Jane Q. Doe" (``same_person``); none when
      two vendors could be, and never a company's name for a person's or the reverse."""
    numbered = _NUMBERED.fullmatch(name)
    if numbered and (key := vendor_key(name_marker(numbered.group(1), words)[0])):
        return next((r for r in like(key, False) if same_number(r["erp_id"], numbered.group(2))), None)
    plain, marker = name_marker(name, words)
    if marker and (key := vendor_key(plain)):
        rows = like(key, False)
        shown = [r for r in rows if vendor_key(r["display_name"]) == vendor_key(name)]
        if found := (shown or [r for r in rows if r["vendor_key"] == key]):
            return found[0]
    asked = person_name(plain)
    if asked:  # "DOE, JANE Q.": the vendors "LAST, FIRST …" and the ones starting with the first name
        starts = [" ".join((*asked[0], asked[1])), asked[1]]
    else:  # "Jane Q. Doe": the vendors "LAST, FIRST …", for each way its last words can be the surname
        given = _plain_words(plain)
        starts = [" ".join((*given[cut:], given[0])) for cut in range(max(1, len(given) - 3), len(given))]

    def fits(row: dict[str, Any], exactly: bool = False) -> bool:
        theirs = name_marker(row["display_name"], words)[0]
        if asked:
            return same_person(asked, theirs, exactly)
        person = person_name(theirs)
        return person is not None and same_person(person, plain, exactly)

    rows = [row for start in starts for row in like(start, True) if fits(row)]
    found = list({row["vendor_key"]: row for row in rows}.values())
    if len(found) > 1:  # "DOE, JANE" and "DOE, JANE Q." for "Jane Doe": the one written with as much
        found = [row for row in found if fits(row, exactly=True)]
    return found[0] if len(found) == 1 else None


class VendorIds(dict):  # type: ignore[type-arg]
    """``Store.vendor_ids()``: {vendor key: ERP ID}, and the "gst:" keys, as a plain dict gave them; and the supplier
    number of a name with no key of its own (``number_of``), by the rule the checks find its vendor by
    (``named_otherwise``), from every vendor of the list as it was read."""

    def __init__(self, ids: dict[str, str], vendors: list[dict[str, Any]], words: list[str]) -> None:
        super().__init__(ids)
        self._vendors = {v["vendor_key"]: v for v in vendors}
        self._keys = sorted(self._vendors)
        self._words = words

    def _like(self, key: str, more: bool) -> list[dict[str, Any]]:
        first, last = (f"{key} ", f"{key}!") if more else (f"{key} #", f"{key} $")
        found = [self._vendors[key]] if key in self._vendors else []
        at = bisect.bisect_left(self._keys, first)
        while at < len(self._keys) and self._keys[at] < last:
            found.append(self._vendors[self._keys[at]])
            at += 1
        return found

    def number_of(self, name: Any) -> str:
        """The supplier number of the vendor a name is by ``named_otherwise``; "" when it is no vendor's."""
        found = named_otherwise(str(name or ""), self._words, self._like)
        return str((found or {}).get("erp_id") or "").strip()


def _master_rows_as_kept(rows: list[dict[str, Any]], holding: list[str], words: list[str]) -> list[dict[str, Any]]:
    """Each row of a vendor master import (a copy) as it is kept: ``key``, its name's key without a retired supplier's
    marker; ``marked``, the note of a marked one, which is on hold (B-2083); ``hold``, JD Edwards' hold payment code
    when it is one that holds (B-2082)."""
    kept = []
    for row in rows:
        plain, marker = name_marker(row["vendor_name"], words)
        row = {**row, "key": vendor_key(plain) or vendor_key(row["vendor_name"]),
               "marked": MARKED.format(marker) if marker else ""}  # fmt: skip
        if marker:
            row["status"], row["status_given"] = "on_hold", True
        if row.get("hold") is not None:
            row["hold"] = hold_code(row["hold"], holding)
        kept.append(row)
    return kept


def _as_written(key: str, row: dict[str, Any], existing: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """(the note, the status) a vendor of an import is written with: its marker's note and its own status; and for
    a number split off a vendor AP has on hold (a new "<key> #<ERP ID>" whose name's vendor is held, by AP's own
    hold: not the ERP's), that vendor's note and hold, which AP put on the name (B-2161)."""
    marked, status = row.get("marked") or "", row.get("status") or "active"
    first = existing.get(key.partition(" #")[0]) if " #" in key and key not in existing else None
    if first and first["status"] == "on_hold" and not marked and not erp_stopped(first):
        return first["notes"] or "", "on_hold"
    return marked, status


def _stopped(row: dict[str, Any]) -> bool:
    """Whether the ERP itself stops this supplier of an import: marked as retired, or its hold payment code holds."""
    return bool(row.get("marked") or row.get("hold"))


def _retired_twin(first: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether ``row`` is a supplier the ERP stops (``_stopped``) by the name of one in use (``first``), under an ERP
    ID of its own: two vendors, the stopped one the second, never merged into the one in use, which would be held for
    it (B-2083)."""
    ident = str(row.get("erp_id") or "").strip()
    return bool(_stopped(row) and not _stopped(first) and ident and ident != str(first.get("erp_id") or "").strip())


class VendorMasterMixin:
    # --- The vendor master's import -----------------------------------------------------------------------

    def import_vendor_master(
        self, rows: list[dict[str, Any]], actor: str | None = None, only_new: bool = False, by_erp_id: bool = False,
        log: bool = True,
    ) -> dict[str, Any]:  # fmt: skip
        """Import the ERP's vendor list (``vendors.master_rows`` output). Each vendor is matched by name;
        what the file gives (ERP ID, GST/HST #, terms, default GL, status) replaces what was there; what
        it has no column for is kept.
        Rows whose names normalise to the same vendor are merged (on hold if any of them is) and reported
        in ``duplicates``: often the same supplier set up twice in the ERP. With ``only_new``, vendors
        already in the list are left alone (``inserted`` lists the new ones). ``by_erp_id`` (the JD Edwards sync,
        which brings the list a page or a night's changes at a time, D-181): the vendors already there count as rows
        before these, so a row goes to the vendor of its name holding its ERP ID ("Acme", or "Acme #4002" the second
        of two, B-930) and another ERP ID's row of that name is merged into that vendor or is the second vendor, as
        in one file. ``log``: the import's event (the sync logs one for all its pages).
        A supplier whose name the ERP marks as retired ("… DO NOT USE", ``name_marker``) is kept under its name
        without the marker, on hold, with a note that says so; when the list has a supplier of that name in use too,
        that one is the vendor of the name and the retired one is kept under its ERP ID (B-2083).
        The supplier's number is the identity when the list gives numbers (B-2161): two rows of one name with two
        ERP IDs are two vendors, the first under the name's key, the others under "<key> #<ERP ID>"; ``shared``
        lists the names several numbers in use go by, each "<ERP ID> <name>". A vendor already there under the
        name keeps its key and everything AP set on it; a number split off a vendor AP holds is held with it."""
        added = updated = 0
        now = _now()
        holding = self.hold_codes()
        merged, names = self._master_rows_merged(rows, by_erp_id)
        duplicates = [n for n in names.values() if len(n) > 1]
        inserted: list[str] = []
        with self._conn() as conn:
            existing = {r["vendor_key"]: dict(r) for r in conn.execute("SELECT vendor_key, status, notes, erp_id "
                                                                      "FROM vendors")}  # fmt: skip
            before = dict(existing)  # the vendors there before this import
            for key, row in merged.items():
                if only_new and key in existing:
                    continue
                marked, status = _as_written(key, row, before)
                # A marked supplier shows the master's name, on hold with its note (AP's own note is kept); one no
                # longer marked there (its note is still the marker's) is active again under the master's name.
                conn.execute(
                    f"""INSERT INTO vendors (vendor_key, display_name, status, expected_gst, notes, updated_at, erp_id,
                       terms, default_gl, in_master, qst_number, pst_number, province, erp_hold)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                       ON CONFLICT(vendor_key) DO UPDATE SET
                       display_name = CASE WHEN excluded.notes != '' OR {_WAS_MARKED}
                                      THEN excluded.display_name ELSE vendors.display_name END,
                       notes = CASE WHEN excluded.notes != '' AND vendors.notes = '' THEN excluded.notes
                               WHEN excluded.notes = '' AND {_WAS_MARKED} THEN '' ELSE vendors.notes END,
                       erp_hold = CASE WHEN ? THEN excluded.erp_hold ELSE vendors.erp_hold END,
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
                       status = CASE WHEN ? THEN excluded.status WHEN {_WAS_MARKED} THEN 'active'
                                ELSE vendors.status END""",
                    (key, row["vendor_name"], status, row.get("gst") or "", marked, now,
                     row.get("erp_id") or "", row.get("terms") or "", _clean_code(row.get("default_gl")),
                     row.get("qst") or "", row.get("pst") or "", row.get("province") or "", row.get("hold") or "",
                     MARKED_LIKE, MARKED_LIKE,
                     *(row.get(f) is not None for f in ("hold", "erp_id", "terms", "default_gl")),
                     bool(row.get("status_given")), MARKED_LIKE),
                )  # fmt: skip
                if key in existing:
                    updated += 1
                else:
                    added += 1
                    inserted.append(key)
                    existing[key] = {"vendor_key": key, "status": status, "notes": marked, "erp_id": row.get("erp_id")}
                # the same supplier as an earlier version kept it, active, under a key with the marker in it: folded
                # into this one, its note kept, so the name as the master shows it is stopped too (B-2163)
                old = existing.get(vendor_key(row["vendor_name"])) if row.get("marked") else None
                if old and old["vendor_key"] != key and str(old["erp_id"] or "") in ("", str(row.get("erp_id") or "")):
                    self._merge_vendor_rows(conn, existing.pop(old["vendor_key"])["vendor_key"], key)
            holds_as_listed(conn, holding)
            if log:
                self._log(conn, "vendors_imported", actor=actor, detail={
                    "added": added, "updated": updated, "duplicates": len(duplicates),
                })  # fmt: skip
        bases = sorted({key.partition(" #")[0] for key in merged if " #" in key})
        shared = [[f"{v['erp_id']} {v['display_name']}" for v in found] for base in bases
                  if (found := sharing(self._vendors_like(base)))]  # fmt: skip
        return {"added": added, "updated": updated, "duplicates": duplicates, "inserted": inserted,
                "other_holds": other_holds(rows, holding), "shared": shared}  # fmt: skip

    def _master_rows_merged(
        self, rows: list[dict[str, Any]], by_erp_id: bool
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
        """({the key each vendor of an import is kept under: its row, the rows of one vendor merged}, {key: the names
        merged under it}): ``import_vendor_master``'s rules, before anything is written."""
        merged: dict[str, dict[str, Any]] = {}
        names: dict[str, list[str]] = {}
        words = self.supplier_markers()
        # the suppliers in use first: of two by one name, the one the ERP does not stop is the vendor of the name
        # (B-2083)
        rows = sorted(_master_rows_as_kept(rows, self.hold_codes(), words), key=_stopped)
        known = self._master_rows_named(rows, words) if by_erp_id else {}
        for row in rows:
            key = row["key"]
            if not key:
                continue
            if known and key not in merged:
                key = self._earlier_row(key, row, known, merged, names)
            first = merged.get(key)
            if first is not None and (_distinct(first, row) or _retired_twin(first, row)):
                # Two suppliers whose names read the same, each with its own ERP ID: two vendors, the second kept
                # under its ERP ID, not merged into the first (B-930, B-2161). So is a retired supplier by the name
                # of one in use (B-2083).
                key = f"{key} #{row['erp_id'].strip()}"
                if key not in merged:
                    merged[key] = dict(row)
                    continue
            names.setdefault(key, []).append(row["vendor_name"])
            if key in merged:
                first = merged[key]
                if row.get("status") == "on_hold":
                    first["status"], first["status_given"] = "on_hold", True
                for field in ("erp_id", "gst", "terms", "default_gl", "qst", "pst", "province", "hold", "marked"):
                    first[field] = first.get(field) or row.get(field) or first.get(field)
            else:
                merged[key] = dict(row)
        return merged, names

    def _master_rows_named(self, rows: list[dict[str, Any]], words: list[str]) -> dict[str, dict[str, Any]]:
        """The vendors already there under these rows' names, or a second one's ("<name> #<ERP ID>"), as rows of the
        import (``_master_rows_as_kept``'s), by key."""
        keys = set()
        for row in rows:
            if key := row["key"]:
                keys |= {key, f"{key} #{str(row.get('erp_id') or '').strip()}"}
        with self._conn() as conn:
            found = fetch_rows(conn, "SELECT vendor_key, display_name, erp_id, expected_gst, terms, default_gl, "
                               "qst_number, pst_number, province FROM vendors WHERE " + _in_list("vendor_key"),
                               (json.dumps(sorted(keys)),))  # fmt: skip
        there = [{"vendor_name": r["display_name"], "erp_id": r["erp_id"], "gst": r["expected_gst"],
                  "terms": r["terms"], "default_gl": r["default_gl"], "qst": r["qst_number"],
                  "pst": r["pst_number"], "province": r["province"]} for r in found]  # fmt: skip
        return {r["vendor_key"]: row for r, row in zip(found, _master_rows_as_kept(there, [], words), strict=True)}

    @staticmethod
    def _earlier_row(key: str, row: dict[str, Any], known: dict[str, dict[str, Any]],
                     merged: dict[str, dict[str, Any]], names: dict[str, list[str]]) -> str:  # fmt: skip
        """``by_erp_id``: the key of the vendor already there that ``row`` is (its name's, holding its ERP ID, or the
        second one's of its name), else ``key``; a vendor there of that name with another ERP ID is put in ``merged``
        as an earlier row (its own values kept when the row is merged into it). A retired one there (marked) gives
        the name to a row in use: it is put under its own ERP ID, the second of the two (B-2083)."""
        erp_id = str(row.get("erp_id") or "").strip()
        for own in (key, f"{key} #{erp_id}"):
            if erp_id and str((known.get(own) or {}).get("erp_id") or "").strip() == erp_id:
                return own
        there = known.get(key)
        if there and str(there.get("erp_id") or "").strip() and erp_id:
            if _retired_twin(row, there):
                merged[f"{key} #{there['erp_id'].strip()}"] = dict(there)
                return key
            merged[key] = dict(there)
            names[key] = [there["vendor_name"]]
        return key

    # --- What the rest reads of the master ----------------------------------------------------------------

    def has_vendor_master(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM vendors WHERE in_master = 1 LIMIT 1").fetchone() is not None

    def master_vendors(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM vendors WHERE in_master = 1")]

    def master_vendor_ids(self) -> list[tuple[str, str]]:
        """(vendor key, ERP ID) of each vendor of the vendor master: what Import invoices' setup step counts, at every
        view (``master_vendors``' every column of 20,000 vendors took 0.23 s, D-166)."""
        with self._conn() as conn:
            return [tuple(r) for r in conn.execute("SELECT vendor_key, erp_id FROM vendors WHERE in_master = 1")]

    def master_vendors_of(self, keys: set[str], ids: set[str]) -> dict[str, dict[str, Any]]:
        """The vendor master's vendors of these keys or ERP IDs, by key: what the JD Edwards sync reports changed."""
        sql = (
            "SELECT vendor_key, display_name, erp_id, expected_gst, terms, status, qst_number, pst_number, province, "
            "erp_bank, erp_hold FROM vendors WHERE in_master = 1 AND {} IN (SELECT value FROM json_each(?))"
        )
        found: dict[str, dict[str, Any]] = {}
        with self._conn() as conn:
            for column, values in (("vendor_key", keys), ("erp_id", ids)):
                for r in conn.execute(sql.format(column), (json.dumps(sorted(values)),)):
                    found[r["vendor_key"]] = dict(r)
        return found

    def hold_codes(self) -> list[str]:
        """JD Edwards' hold payment codes that put a supplier on hold (HOLD_CODES unless the setting lists others)."""
        return listed(self.get_setting(HOLD_CODES_SETTING)) or listed(HOLD_CODES)

    def set_erp_bank_and_hold(self, values: dict[str, tuple[str, str]]) -> None:
        """{ERP ID: (the bank account JDE pays on, JDE's hold code)} on the vendor master's vendors of those IDs (kept
        apart from AP's own hold, D-105): only where either changed. The hold code is kept when it is one that holds
        (``hold_code``), blank otherwise; and a code no vendor should keep any more goes (``holds_as_listed``)."""
        holding = self.hold_codes()
        kept = [(bank, hold_code(hold, holding), erp_id) for erp_id, (bank, hold) in values.items()]
        with self._conn() as conn:
            conn.executemany("UPDATE vendors SET erp_bank = ?1, erp_hold = ?2 WHERE erp_id = ?3 AND in_master = 1 AND "
                             "(erp_bank != ?1 OR erp_hold != ?2)", kept)  # fmt: skip
            holds_as_listed(conn, holding)

    def vendor_ids(self) -> dict[str, str]:
        """{vendor_key: ERP vendor ID} from the vendor master, plus {"gst:<number>": ERP vendor ID} so an
        invoice whose vendor name differs but whose GST/HST number matches still gets its ID. Kept in each process
        until the vendor list changes (``changes/vendors``, D-159): each check asks it twice. Shared: read it, don't
        change it."""
        return self.summary_memo("vendor_ids", self._vendor_ids, topic="", changes="vendors")

    def _vendor_ids(self) -> dict[str, str]:
        with self._conn() as conn:
            vendors = fetch_rows(conn, "SELECT vendor_key, display_name, erp_id, expected_gst, notes, erp_hold "
                                       "FROM vendors")  # fmt: skip
        rows = [(v["vendor_key"], v["erp_id"], v["expected_gst"]) for v in vendors if v["erp_id"]]
        ids = {r[0]: r[1] for r in rows}
        for r in rows:
            number = norm_tax_number(r[2])
            if sum(c.isdigit() for c in number) >= 9:
                ids.setdefault(f"gst:{number}", r[1])
                if number[:9].isdigit():  # bn: the master keeps only the Business Number; bnx: the full account
                    ids.setdefault(f"{'bn' if len(number) == 9 else 'bnx'}:{number[:9]}", r[1])
        # A name several supplier numbers in use go by gives none of them: only the name with its number does
        # (``VendorIds.number_of``, B-2161).
        named: dict[str, list[dict[str, Any]]] = {}
        for v in vendors:
            named.setdefault(v["vendor_key"].partition(" #")[0], []).append(v)
        for key, group in named.items():
            if len(group) > 1 and sharing(group):
                ids.pop(key, None)
        return VendorIds(ids, vendors, self.supplier_markers())

    def vendors_sharing(self, name: str | None) -> list[dict[str, Any]]:
        """The vendors in use that share the name an invoice prints, each under a supplier number of its own, by
        number; [] when the name is one vendor's or none's (``sharing``, B-2161)."""
        key = vendor_key(name or "")
        return sharing(self._vendors_like(key)) if key else []

    def master_by_tax_number(self, number: Any) -> list[dict[str, Any]]:
        """The vendor master's vendors whose GST/HST number is the registration ``number`` is (``same_tax_number``),
        in the list's order: for an invoice not found by name. Only those vendors are read, found by the master's
        numbers kept in each process until the vendor list changes (``changes/vendors``, D-159)."""
        text = norm_tax_number(number)
        if not text:
            return []
        exact, business = self.summary_memo("master_numbers", self._master_numbers, topic="", changes="vendors")
        found = {*exact.get(text, ()), *(business.get(text[:9], ()) if BUSINESS_NUMBER.fullmatch(text) else ())}
        if not found:
            return []
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM vendors WHERE rowid IN (SELECT value FROM json_each(?)) AND in_master = 1 "
                "ORDER BY rowid", (json.dumps(sorted(found)),),
            )]  # fmt: skip
        return [r for r in rows if same_tax_number(r["expected_gst"], number)]

    def _master_numbers(self) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
        """({a GST/HST number as compared: the rowids of the master's vendors with it}, {a 9-digit Business Number:
        the rowids of those whose number is it, with or without its RT suffix}): every vendor ``same_tax_number``
        could match is in one of them."""
        with self._conn() as conn:
            rows = conn.execute("SELECT rowid, expected_gst FROM vendors WHERE in_master = 1 AND expected_gst != ''")
            exact: dict[str, list[int]] = {}
            business: dict[str, list[int]] = {}
            for rowid, gst in rows:
                text = norm_tax_number(gst)
                if text:
                    exact.setdefault(text, []).append(rowid)
                    if BUSINESS_NUMBER.fullmatch(text):
                        business.setdefault(text[:9], []).append(rowid)
        return exact, business

    # --- The vendor a name is when none is kept under it as written -----------------------------------------

    def _vendors_like(self, key: str, more: bool = False) -> list[dict[str, Any]]:
        """The vendors under ``key`` and its second ones' ("<key> #<ERP ID>"); ``more``: every key starting with it
        as a word. A seek in the vendor key's index (B-1582), in the order they were kept."""
        with self._conn() as conn:
            return fetch_rows(conn, "SELECT * FROM vendors WHERE vendor_key = ? OR (vendor_key >= ? AND vendor_key "
                              f"< ?) ORDER BY {ROWID}", (key, f"{key} " if more else f"{key} #",
                                                         f"{key}!" if more else f"{key} $"))  # fmt: skip

    def _vendor_named_otherwise(self, name: str) -> dict[str, Any] | None:
        """The vendor of a name no vendor is kept under as it is written (``named_otherwise``: a name with its
        supplier number, a retired supplier's marker, a person written the other way round), read from the list."""
        return named_otherwise(name, self.supplier_markers(), self._vendors_like)
