"""The capture benchmark: generator truth is right, and the harness scores correctly."""

from __future__ import annotations

import json
import math
import random
import re
from decimal import Decimal

import pytest

pymupdf = pytest.importorskip("pymupdf")

from ap_coder.bench import content as C  # noqa: E402
from ap_coder.bench.generator import ARCHETYPES, generate, plan, rotate_box  # noqa: E402
from ap_coder.bench.harness import evaluate, render_markdown, run  # noqa: E402
from ap_coder.bench.scoring import calibration, field_metrics, score_case, values_match  # noqa: E402
from ap_coder.capture.types import AMOUNT_FIELDS, FIELDS, Box, CaptureResult, FieldResult  # noqa: E402


@pytest.fixture(scope="module")
def digital_cases(tmp_path_factory):
    """Two of every archetype, no scans."""
    return generate(tmp_path_factory.mktemp("bench_digital"), 2 * len(ARCHETYPES), seed=11, scanned_fraction=0.0)


def _words_in(page, box: list[float]) -> str:
    w, h = page.rect.width, page.rect.height
    x0, y0, x1, y1 = box[1] * w, box[2] * h, box[3] * w, box[4] * h
    inside = [
        wd for wd in page.get_text("words") if x0 <= (wd[0] + wd[2]) / 2 <= x1 and y0 <= (wd[1] + wd[3]) / 2 <= y1
    ]
    inside.sort(key=lambda wd: wd[0])  # every value is printed on one line
    return " ".join(wd[4] for wd in inside)


def _parse_amount(raw: str) -> float:
    s = raw.replace("CAD", "").replace("USD", "").replace("$", "").strip()
    neg = s.startswith("-") or s.startswith("(") or s.endswith("CR")
    s = s.strip("()-").replace("CR", "").strip()
    if re.search(r",\d{2}$", s):
        s = s.replace(" ", "").replace(",", ".")
    else:
        s = s.replace(",", "").replace(" ", "")
    return -float(s) if neg else float(s)


# --- generator ----------------------------------------------------------------------------------


def test_generator_is_deterministic(tmp_path):
    a = generate(tmp_path / "a", 8, seed=5, scanned_fraction=0.25)
    b = generate(tmp_path / "b", 8, seed=5, scanned_fraction=0.25)
    assert [c.truth for c in a] == [c.truth for c in b]
    assert [c.path.read_bytes() for c in a] == [c.path.read_bytes() for c in b]
    assert sum(c.scanned for c in a) == 2
    c = generate(tmp_path / "c", 8, seed=6, scanned_fraction=0.25)
    assert [x.truth["fields"] for x in a] != [x.truth["fields"] for x in c]


def test_plan_covers_every_archetype():
    p = plan(len(ARCHETYPES), seed=3, scanned_fraction=0.3)
    assert sorted(a for _i, a, _s in p) == sorted(ARCHETYPES)
    assert len(ARCHETYPES) >= 12
    assert sum(s for _i, _a, s in p) == round(len(ARCHETYPES) * 0.3)


def test_truth_files_are_complete(digital_cases):
    for case in digital_cases:
        t = json.loads(case.truth_path.read_text(encoding="utf-8"))
        assert t == case.truth
        assert t["layout"] in ARCHETYPES and t["language"] in ("en", "fr", "bi") and t["scanned"] is False
        assert set(t["fields"]) <= set(FIELDS)
        for f in ("vendor_name", "invoice_number", "invoice_date", "grand_total"):
            assert f in t["fields"], (case.id, f)
        assert t["line_items"], case.id
        for e in t["fields"].values():
            for box in [e["box"]] + [a["box"] for a in e["also"]]:
                assert 1 <= box[0] <= t["page_count"] and 0 <= box[1] < box[3] <= 1 and 0 <= box[2] < box[4] <= 1


def test_truth_boxes_land_on_the_printed_text(digital_cases):
    checked = 0
    for case in digital_cases:
        doc = pymupdf.open(case.path)
        for name, e in case.truth["fields"].items():
            for raw, box in [(e["raw"], e["box"])] + [(a["raw"], a["box"]) for a in e["also"]]:
                got = _words_in(doc[box[0] - 1], box)
                assert " ".join(got.split()) == " ".join(raw.split()), (case.id, name, raw, got)
                checked += 1
        for li in case.truth["line_items"]:
            row = _words_in(doc[li["box"][0] - 1], li["box"])
            assert li["description"].split()[0] in row, (case.id, li, row)
    assert checked > 200


def test_truth_values_match_what_is_printed(digital_cases):
    for case in digital_cases:
        for name, e in case.truth["fields"].items():
            if name in AMOUNT_FIELDS:
                for raw in [e["raw"]] + [a["raw"] for a in e["also"]]:
                    assert _parse_amount(raw) == pytest.approx(e["value"], abs=0.001), (case.id, name, raw)
            elif name in ("invoice_number", "po_number", "payment_terms", "vendor_name"):
                assert e["raw"].lstrip("#") == e["value"], (case.id, name)


def test_multipage_and_stub_repeat_values(digital_cases):
    multi = [c for c in digital_cases if c.layout == "multipage"]
    assert multi and all(c.truth["page_count"] >= 2 for c in multi)
    for c in multi:
        assert c.truth["fields"]["grand_total"]["box"][0] == c.truth["page_count"]
        assert {li["box"][0] for li in c.truth["line_items"]} >= {1, 2}
    stubs = [c for c in digital_cases if c.layout == "remittance_stub"]
    for c in stubs:
        assert c.truth["fields"]["grand_total"]["also"], c.id
        assert c.truth["fields"]["invoice_number"]["also"], c.id


def test_credit_notes_are_negative(digital_cases):
    credits = [c for c in digital_cases if c.layout == "credit_note"]
    assert credits
    for c in credits:
        f = c.truth["fields"]
        assert f["grand_total"]["value"] < 0 and f["subtotal"]["value"] < 0
        assert "original_invoice" in c.truth["extras"]


def test_business_numbers_pass_luhn(digital_cases):
    assert C.luhn_valid("123456782") and not C.luhn_valid("123456789")
    rng = random.Random(1)
    for _ in range(200):
        assert C.luhn_valid(C.make_bn(rng))
    seen = 0
    for case in digital_cases:
        e = case.truth["fields"].get("gst_hst_registration_number")
        if e:
            v = e["value"]
            assert re.fullmatch(r"\d{9}RT\d{4}", v), v
            assert C.luhn_valid(v[:9])
            seen += 1
        q = case.truth["fields"].get("qst_registration_number")
        if q:
            assert re.fullmatch(r"\d{10}TQ\d{4}", q["value"])
    assert seen >= len(digital_cases) // 2


def test_taxes_add_up(digital_cases):
    rates = {"gst_amount": None, "hst_amount": None, "pst_amount": None, "qst_amount": None}
    for case in digital_cases:
        t = case.truth
        f = {k: Decimal(str(v["value"])) for k, v in t["fields"].items() if k in AMOUNT_FIELDS}
        taxes = sum((f[k] for k in rates if k in f), Decimal("0"))
        if t["country"] == "US":
            taxes = f.get("tax_total", Decimal("0"))
        freight = Decimal(str(t["extras"].get("freight", 0)))
        assert f["subtotal"] + freight + taxes == f["grand_total"], case.id
        lines = sum((Decimal(str(li["amount"])) for li in t["line_items"]), Decimal("0"))
        assert lines == f["subtotal"], case.id
        if "tax_total" in f and t["country"] == "CA":
            assert f["tax_total"] == taxes
        if t["country"] == "CA":
            taxable = f["subtotal"] + freight
            for code, fld, rate in C.TAX_RULES[t["province"]]:
                assert f[fld] == C.money(taxable * rate), (case.id, code)
            if t["province"] == "QC":
                assert "qst_amount" in f and "hst_amount" not in f


def test_compute_taxes_by_province():
    by = {p: {t.field: t.amount for t in C.compute_taxes(p, Decimal("1000.00"))} for p in C.TAX_RULES}
    assert by["ON"] == {"hst_amount": Decimal("130.00")}
    assert by["NS"] == {"hst_amount": Decimal("140.00")}
    assert by["QC"] == {"gst_amount": Decimal("50.00"), "qst_amount": Decimal("99.75")}
    assert by["BC"] == {"gst_amount": Decimal("50.00"), "pst_amount": Decimal("70.00")}
    assert by["SK"]["pst_amount"] == Decimal("60.00") and by["MB"]["pst_amount"] == Decimal("70.00")
    assert C.compute_taxes("QC", Decimal("10.10"))[1].amount == Decimal("1.01")  # 1.007... rounds half-up


def test_formats():
    st = C.MoneyStyle(decimal=",", group=" ", symbol="$")
    assert st.fmt(Decimal("1234.56")) == "1 234,56 $"
    assert C.MoneyStyle(neg="paren").fmt(Decimal("-12")) == "(12.00)"
    assert C.MoneyStyle(symbol="$", code="CAD").fmt(Decimal("1234.5"), code=True) == "CAD $1,234.50"
    from datetime import date

    d = date(2026, 10, 7)
    assert C.format_date(d, "fr_long") == "7 octobre 2026"
    assert C.format_date(date(2026, 10, 1), "fr_long") == "1er octobre 2026"
    assert C.format_date(d, "mon_d_y") == "Oct 7, 2026"
    assert C.format_date(d, "dd/mm/yyyy") == "07/10/2026"


def test_scans_rotate_truth_boxes(tmp_path):
    cases = generate(tmp_path, 4, seed=2, scanned_fraction=1.0)
    for c in cases:
        assert c.scanned and c.truth["scan"]["format"] in ("pdf", "png") and c.path.suffix in (".pdf", ".png")
        assert c.path.suffix == ".pdf" or c.truth["page_count"] == 1
        assert all(-1.5 <= a <= 1.5 for a in c.truth["scan"]["angles"])
        if c.path.suffix == ".pdf":
            doc = pymupdf.open(c.path)
            assert not doc[0].get_text("words")  # image only


def test_rotate_box_matches_pil():
    np = pytest.importorskip("numpy")
    from PIL import Image

    w, h = 400, 600
    img = Image.new("L", (w, h), 255)
    img.paste(0, (60, 100, 180, 130))
    angle = 1.4
    rot = np.asarray(img.rotate(angle, resample=Image.NEAREST, fillcolor=255))
    ys, xs = np.nonzero(rot < 128)
    got = rotate_box([1, 60 / w, 100 / h, 180 / w, 130 / h], angle, w, h)
    assert got[1] * w == pytest.approx(xs.min(), abs=2) and got[3] * w == pytest.approx(xs.max() + 1, abs=2)
    assert got[2] * h == pytest.approx(ys.min(), abs=2) and got[4] * h == pytest.approx(ys.max() + 1, abs=2)
    assert rotate_box([1, 0.1, 0.2, 0.3, 0.4], 0.0, w, h) == [1, 0.1, 0.2, 0.3, 0.4]


# --- scoring ------------------------------------------------------------------------------------


def test_values_match_rules():
    assert values_match("grand_total", 1234.56, 1234.564)
    assert not values_match("grand_total", 1234.56, 1234.57)
    assert values_match("subtotal", -12.0, "-12.00")
    assert values_match("invoice_number", "INV-2026-00412", "inv 2026 00412")
    assert not values_match("invoice_number", "INV-2026-00412", "INV-2026-0412")
    assert values_match("gst_hst_registration_number", "123456782RT0001", "123456782 RT 0001")
    assert values_match("invoice_date", "2026-10-07", "2026-10-07")
    assert not values_match("invoice_date", "2026-10-07", "2026-07-10")
    assert values_match("vendor_name", "Groupe Côté ltée", "GROUPE COTE LTEE")
    assert values_match("payment_terms", "Net 30 days", "Net 30")
    assert values_match("payment_terms", "Payable sur réception", "Due on receipt")
    assert not values_match("payment_terms", "Net 30", "Net 45")
    assert values_match("currency", "CAD", "cad")
    assert not values_match("grand_total", 10.0, None)


def _truth() -> dict:
    box = [1, 0.5, 0.5, 0.6, 0.52]
    return {
        "id": "t1",
        "layout": "classic_left",
        "scanned": False,
        "fields": {
            "invoice_number": {"value": "A-1", "raw": "A-1", "box": [1, 0.7, 0.1, 0.8, 0.12], "also": []},
            "grand_total": {
                "value": 113.0,
                "raw": "113.00",
                "box": box,
                "also": [{"raw": "$113.00", "box": [1, 0.7, 0.9, 0.8, 0.92]}],
            },
            "subtotal": {"value": 100.0, "raw": "100.00", "box": [1, 0.5, 0.4, 0.6, 0.42], "also": []},
            "hst_amount": {"value": 13.0, "raw": "13.00", "box": [1, 0.5, 0.45, 0.6, 0.47], "also": []},
        },
        "implied": {"currency": "CAD", "tax_total": 13.0},
        "line_items": [
            {
                "description": "Widget",
                "quantity": 1,
                "unit_price": 100.0,
                "amount": 100.0,
                "box": [1, 0.1, 0.3, 0.9, 0.32],
            }
        ],
    }


def _fr(field, value, conf, status, box=None):
    return FieldResult(field, value, conf, status, [Box.from_list(box)] if box else [])


def test_score_case_and_metrics():
    truth = _truth()
    result = CaptureResult(
        fields={
            "invoice_number": _fr("invoice_number", "a1", 0.99, "verified", [1, 0.71, 0.1, 0.75, 0.12]),
            "grand_total": _fr("grand_total", 113.0, 0.95, "verified", [1, 0.72, 0.9, 0.75, 0.91]),  # on the stub copy
            "subtotal": _fr("subtotal", 113.0, 0.6, "likely", [1, 0.5, 0.5, 0.6, 0.52]),  # wrong value
            "currency": _fr("currency", "CAD", 0.9, "verified"),  # implied: ignored
            "tax_total": _fr("tax_total", 99.0, 0.8, "verified"),  # not printed, wrong: spurious
            "po_number": _fr("po_number", "PO-1", 0.3, "check"),  # not printed: spurious
        }
    )
    recs = {r["field"]: r for r in score_case(truth, result)}
    assert recs["invoice_number"]["correct"] and recs["invoice_number"]["box_hit"]
    assert recs["grand_total"]["correct"] and recs["grand_total"]["box_hit"]
    assert not recs["subtotal"]["correct"] and not recs["subtotal"]["box_hit"]
    assert not recs["hst_amount"]["reported"] and recs["hst_amount"]["present"]
    assert not recs["currency"]["scored"] and not recs["currency"]["spurious"]
    assert recs["tax_total"]["spurious"] and recs["po_number"]["spurious"]

    m = field_metrics(list(recs.values()))
    assert m["n"] == 4 and m["accuracy"] == 0.5
    assert m["verified_n"] == 3  # two right, one spurious
    assert m["verified_precision"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["verified_coverage"] == 0.5
    assert m["vl_precision"] == pytest.approx(2 / 4, abs=1e-4) and m["vl_coverage"] == 0.75
    assert m["spurious"] == 2 and m["box_hit"] == 1.0
    assert m["statuses"] == {"verified": 2, "likely": 1, "check": 0, "missing": 1}


def test_calibration_math():
    recs = [
        {"scored": True, "reported": True, "confidence": c, "correct": ok}
        for c, ok in [(0.95, True), (0.95, True), (0.95, False), (0.15, False), (0.15, True)]
    ]
    cal = calibration(recs)
    top = cal["bins"][9]
    assert top["n"] == 3 and top["conf"] == pytest.approx(0.95) and top["acc"] == pytest.approx(2 / 3, abs=1e-4)
    low = cal["bins"][1]
    assert low["n"] == 2 and low["acc"] == 0.5
    expected = 3 / 5 * abs(2 / 3 - 0.95) + 2 / 5 * abs(0.5 - 0.15)
    assert cal["ece"] == pytest.approx(expected, abs=1e-3)
    assert calibration([])["ece"] is None
    assert calibration([{"scored": True, "reported": True, "confidence": 1.0, "correct": True}])["bins"][9]["n"] == 1


def test_evaluate_reports_invoices_and_failures():
    from ap_coder.bench.generator import Case

    t_ok, t_bad = _truth(), {**_truth(), "id": "t2", "scanned": True, "layout": "quebec_fr"}
    perfect = CaptureResult(
        fields={k: _fr(k, v["value"], 0.99, "verified", v["box"]) for k, v in t_ok["fields"].items()}
    )
    wrong = CaptureResult(fields={**perfect.fields, "grand_total": _fr("grand_total", 1.0, 0.99, "verified")})
    cases = [Case("t1", None, None, t_ok), Case("t2", None, None, t_bad)]
    out = evaluate(cases, [{"result": perfect.to_dict()}, {"result": wrong.to_dict()}])
    rep = out["report"]
    inv = rep["summary"]["invoices"]
    assert inv["fully_correct"] == 0.5 and inv["touchless"] == 1.0 and inv["touchless_wrong"] == 1
    assert rep["digital"]["overall"]["accuracy"] == 1.0 and rep["scanned"]["overall"]["accuracy"] == 0.75
    assert rep["archetypes"]["quebec_fr"]["fully_correct"] == 0.0
    assert rep["line_items"]["truth"] == 2 and rep["line_items"]["matched"] == 0
    [fail] = out["failures"]
    assert fail["case"] == "t2" and fail["field"] == "grand_total" and fail["kind"] == "wrong" and fail["got"] == 1.0
    md = render_markdown({"config": {"reader": "x", "seed": 1, "generator_version": 1, "workers": 1}, **rep})
    assert "| grand_total |" in md and "ECE" in md


def test_harness_runs_end_to_end_with_the_stub(tmp_path):
    rep = run(n=5, seed=4, scanned=0.2, out=tmp_path, workers=1, reader="stub")
    assert rep["cases"] == 5 and rep["scanned_cases"] == 1
    for name in ("report.json", "report.md", "failures.jsonl"):
        assert (tmp_path / name).is_file()
    saved = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert saved["summary"] == rep["summary"]
    again = run(n=5, seed=4, scanned=0.2, out=tmp_path, workers=1, reader="stub")  # reuses the cases
    assert again["summary"] == rep["summary"]
    rows = [json.loads(x) for x in (tmp_path / "failures.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows and {"case", "field", "truth", "got", "status", "confidence", "reasons"} <= set(rows[0])
    assert not math.isnan(rep["summary"]["overall"]["accuracy"])
