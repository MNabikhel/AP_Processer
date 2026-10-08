"""Demo mode: the sample invoices in the queue as if Azure had coded them, and removable again."""

from ap_coder import cli
from ap_coder.demo import DEMO_INVOICES, is_demo, load_demo, remove_demo
from ap_coder.store import APPROVED, REVIEW, Store


def test_demo_fills_the_queue_once_and_teaches(tmp_path):
    store = Store(tmp_path / "ap.db")
    result = load_demo(store)
    assert result == {"added": len(DEMO_INVOICES), "approved": 2, "to_review": len(DEMO_INVOICES) - 2}
    assert load_demo(store)["added"] == 0  # loading again adds nothing
    assert len(store.list_invoices(REVIEW)) == len(DEMO_INVOICES) - 2
    assert len(store.list_invoices(APPROVED)) == 2
    corrected = [r for r in store.feedback_rows() if r["outcome"] == "corrected"]
    assert [(r["suggested_gl"], r["final_gl"]) for r in corrected] == [("6000", "6400")]  # the Chinook brochure
    assert store.list_accounts("gl_accounts")  # sample setup loaded because nothing was imported


def test_demo_mistakes_are_flagged_or_visible(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_demo(store)
    pacific = next(i for i in store.list_invoices() if i["vendor_name"].startswith("Pacific"))
    inv = store.get_invoice(pacific["id"])
    assert inv["requires_review"] and inv["ai_output"]["line_items"][1]["predicted_gl_code"] == "6000"
    assert all(not issue["severity"] == "error" for i in store.list_invoices() for issue in
               (store.get_invoice(i["id"])["validation"] or {}).get("issues", []))  # fmt: skip


def test_removing_the_demo_keeps_real_invoices(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    real = store.add_invoice(tmp_path / "real.pdf", ground_truth, {"requires_review": False})
    load_demo(store)
    assert remove_demo(store) == len(DEMO_INVOICES)
    assert [i["id"] for i in store.list_invoices()] == [real]
    assert store.feedback_rows() == []
    assert not any(is_demo(i) for i in store.list_invoices_full())


def test_demo_command(tmp_path, capsys):
    db = tmp_path / "ap.db"
    assert cli.main(["--db", str(db), "demo"]) == 0
    assert "to review" in capsys.readouterr().err
    assert cli.main(["--db", str(db), "demo", "--remove"]) == 0
    assert Store(db).list_invoices() == []
