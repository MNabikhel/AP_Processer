"""Touchless processing on the dashboard (ap_coder/web): Settings → Automation, the Learning page as a training view,
and what the review screen says an approval taught."""

import html
import re
from dataclasses import replace

import pytest

from ap_coder.core.store import Store, load_sample_setup
from tests.capture.test_touchless import _ready
from tests.web.test_web_invoice import form_of
from tests.webclient import add_clean, follow, post


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Lead Lee")
    return Store(tmp_path / "private" / "ap_coder.db")


def _switch(page: str) -> str:
    """The form that turns touchless processing on or off."""
    return page.split('action="/settings/automation/touchless"', 1)[1].split("</form>", 1)[0]


def test_automation_tab_turns_touchless_on_with_a_confirmation(client, store):
    _ready(store, name="Price $5 *Wholesale*")
    _ready(store, 12, key="id:V2", name="Chinook")
    page = html.unescape(client.get("/settings/automation").text)
    assert "Off: every invoice is reviewed by a person" in page
    assert "at least 20" in page and "Invoices in a row without a correction" in page and "the last 10" in page
    assert "5% of the invoices that skip review" in page
    assert "Bank details changed" in page and "Possible duplicate" in page and "5,000.00 CAD" in page
    assert "Credit note" in page and "Not in the vendor master" in page and "Over the approval limit" in page
    assert re.search(r'<button[^>]*type="submit"[^>]*disabled', _switch(page))  # needs "I understand"
    response = post(client, "/settings/automation/touchless", {"turn": "on"})
    assert response.status_code == 400 and not store.touchless_enabled()
    page = follow(client, post(client, "/settings/automation/touchless", {"turn": "on", "sure": "1"}))
    assert store.touchless_enabled()
    assert store.events(actions=["touchless_on"])[0]["actor"] == "Lead Lee"
    assert "Touchless processing is on: 1 vendor skipping review now." in page
    assert "On: a vendor that meets the bar" in page
    assert "Vendors skipping review" in page and "Skipped review, last 30 days" in page


def test_the_touchless_confirmation_is_asked_again_for_the_opposite_switch(client, store):
    """Found driving the dashboard: after turning touchless off, the menu showed "Turn on" with "I understand" still
    ticked from turning it off, so one more click approved invoices without a person with no fresh confirmation."""
    page = follow(client, post(client, "/settings/automation/touchless", {"turn": "on", "sure": "1"}))
    assert store.touchless_enabled()
    form = _switch(page)
    assert 'name="turn" value="off"' in form  # the form now turns it off...
    assert re.search(r'<input type="checkbox" name="sure"[^>]*>', form).group(0).count("checked") == 0  # unticked
    assert re.search(r'<button[^>]*type="submit"[^>]*disabled', form)  # ... and "Turn off" waits for its own tick
    response = post(client, "/settings/automation/touchless", {"turn": "off"})
    assert response.status_code == 400 and store.touchless_enabled()
    # The same "Turn on" sent again (a second click, an old tab) does not turn it off.
    again = follow(client, post(client, "/settings/automation/touchless", {"turn": "on", "sure": "1"}))
    assert store.touchless_enabled() and "already on" in again
    page = follow(client, post(client, "/settings/automation/touchless", {"turn": "off", "sure": "1"}))
    assert not store.touchless_enabled()
    form = _switch(page)
    assert 'name="turn" value="on"' in form and "checked" not in form
    assert re.search(r'<button[^>]*type="submit"[^>]*disabled', form)  # and "Turn on" again for its own


def test_automation_tab_saves_the_limit(client, store):
    page = follow(client, post(client, "/settings/automation/limit", {"limit": "2500"}))
    assert store.touchless_limit() == 2500.0 and "Saved: 2,500.00 CAD." in page
    assert store.events(actions=["settings_changed"])[0]["actor"] == "Lead Lee"


def test_learning_tab_has_no_per_vendor_turn_on_button(client, store):
    _ready(store)
    store.set_touchless(True, "Mia")
    page = client.get("/learning?tab=suppliers").text
    assert "Touchless" in page and "Skips review since" in page
    assert "Turn on autonomy" not in page and "/learning/suppliers/on" not in page
    assert "/learning/suppliers/hold" in page  # keeping a vendor supervised is the one per-vendor control


def test_approving_says_what_the_vendor_learned(client, store):
    load_sample_setup(store)
    invoice_id = add_clean(store)
    page = follow(client, post(client, f"/review/{invoice_id}/approve", form_of(store, invoice_id)))
    # one line, and what it learned folded under it (B-804)
    found = re.search(r"Approved [^<]*</div>.*?What it learned</summary><div>([^<]*coded as suggested\.)",
                      html.unescape(page), re.S)  # fmt: skip
    assert found, page
    text = found.group(1)
    assert "You changed no header field." in text and ": 1 invoice reviewed, 1 of about " in text
    assert "clean approvals toward skipping review." in text  # B-666


# --- Every vendor's record in a few queries (D-157), as one vendor's each gave it ----------------------------------


def _vendors(store: Store) -> None:
    """Vendors in every state: ready, corrected, past the window, with outcomes of no invoice, audited, held,
    suspended, none recorded, with and without a layout; two invoices recorded at the same moment."""
    from tests.capture.test_touchless import _at, _outcomes

    _ready(store)
    for i in range(25):
        source = "audit" if i % 7 == 3 else "review"
        store.record_outcomes("id:V2", 9 + i, _outcomes(bad=i in (4, 11), n=5), source=source, display_name="Chinook",
                              at=_at(i if i != 1 else 0))  # fmt: skip
    store.record_outcomes("id:V2", None, _outcomes(bad=True, n=3), display_name="Chinook", at=_at(30))
    for i in range(230):
        store.record_outcomes("id:V3", 70_000 + i, _outcomes(bad=i % 37 == 5, n=2), display_name="acme", at=_at(i))
    store.record_outcomes("id:V4", None, _outcomes(n=4), display_name="No Invoice", at=_at(5))
    store.save_supplier_profile("id:V5", display_name="Laid Out", template={"fields": {"total": [1, 2]}})
    store.save_supplier_profile("id:V6", display_name="Empty layout", template={})
    store.set_supplier_state("id:V2", "held", "Mia", "new contract")
    store.set_supplier_state("id:V3", "suspended", "Mia")


def _old_suppliers_context(store: Store) -> list:
    """``learning.suppliers_context``'s vendors as it worked them out: three queries a vendor, every one drawn."""
    from ap_coder.capture import supplier as s
    from ap_coder.core import ui
    from ap_coder.web.pages import learning as page

    HELD, SUPERVISED, autonomy_status, judged_stats = s.HELD, s.SUPERVISED, s.autonomy_status, s.judged_stats
    clean_invoices_to_go, to_go_text = s.clean_invoices_to_go, s.to_go_text
    policy, on, out = store.autonomy_policy(), store.touchless_enabled(), []
    for p in store.list_supplier_profiles():
        stats = store.supplier_stats(p["key"], policy.window)
        stored = p["state"] or SUPERVISED
        state, progress, why = autonomy_status(stats, policy, stored, p["autonomous_since"], touchless_on=on,
                                               suspended_at=p.get("suspended_at"))  # fmt: skip
        name = p["display_name"] or p["key"]
        recent = stats.history[: page.RECENT]
        corrected = sum(1 for h in recent if h["corrections"])
        judged = judged_stats(stats, stored, p.get("suspended_at"))
        to_go = clean_invoices_to_go(judged, policy)
        parts = [
            ui.plural(stats.invoices, "invoice") + " reviewed",
            f"{ui.plural(corrected, 'correction')} in the last {len(recent)}" if recent else "no corrections yet",
        ]
        if state in ("learning", "supervised"):
            said = to_go_text(to_go, done=judged.clean_streak)
            why = said[:1].upper() + said[1:] + "."
        why = re.sub(r", accuracy at least [\d.]+%", "", why)
        fields = [
            {
                "label": page.FIELD_LABELS.get(f, f),
                "n": fs.n,
                "corrected": fs.n - fs.correct,
                "accuracy": f"{fs.accuracy:.0%}",
                "tone": "ok" if fs.correct == fs.n else "warn",
            }
            for f, fs in sorted(stats.fields.items(), key=lambda kv: (kv[1].accuracy or 0, kv[0]))
        ]
        latest = []
        if stats.fields:
            latest = [
                {
                    "label": page.FIELD_LABELS.get(r["field"], r["field"]),
                    "read": r["ai_value"] if r["ai_value"] is not None else "(blank)",
                    "final": r["final_value"] if r["final_value"] is not None else "(blank)",
                    "at": str(r["at"])[:10],
                }
                for r in [r for r in store.supplier_outcomes(p["key"], limit=500) if not r["correct"]][:5]
            ]
        out.append(
            {
                "key": p["key"],
                "name": name,
                "state": state,
                "held": p["state"] == HELD,
                "invoices": stats.invoices,
                "row": page.built(ui.supplier_row(name, " · ".join(parts), state, progress, why)),
                "fields": fields,
                "window": ui.plural(stats.window_invoices, "reviewed invoice"),
                "latest": latest,
                "has_layout": bool(p.get("template")),
            }
        )
    out.sort(key=lambda r: (page._STATE_ORDER.get(r["state"], 9), -r["invoices"], r["name"].lower()))
    return out


@pytest.mark.parametrize("touchless", [False, True])
def test_every_vendors_record_at_once_is_each_ones_own(store, touchless):
    """Supplier learning and Settings → Automation read every vendor's record in a few queries (D-157): the same
    records, states, rows and counts as three queries a vendor gave; only the first vendors are drawn ("Show
    more")."""
    from ap_coder.web.pages import learning, settings_automation

    _vendors(store)
    store.set_touchless(touchless, "Mia")
    keys = [p["key"] for p in store.list_supplier_profiles()]
    for window in (200, 3, 1):
        each = {k: store.supplier_stats(k, window) for k in keys}
        assert store.all_supplier_stats(keys, window) == each
        no_fields = {k: replace(s, fields={}) for k, s in each.items()}
        assert store.all_supplier_stats(keys, window, fields=False) == no_fields
        assert store.supplier_fields(keys[1:3], window) == {k: each[k].fields for k in keys[1:3]}
    for among, most in ((500, 5), (7, 2)):
        each = {k: [r for r in store.supplier_outcomes(k, among) if not r["correct"]][:most] for k in keys}
        assert store.latest_corrections(keys, among, most) == each and any(each.values())
    brief = {p["key"]: p for p in store.supplier_profiles_brief()}
    assert list(brief) == keys and {k: bool(p["has_layout"]) for k, p in brief.items()} == {
        p["key"]: bool(p["template"]) for p in store.list_supplier_profiles()}  # fmt: skip
    old = _old_suppliers_context(store)
    assert learning.suppliers_context(store, 1000)["suppliers"] == old and len(old) == 6
    shown = learning.suppliers_context(store, 2)
    assert shown["suppliers"] == old[:2] and shown["suppliers_total"] == 6
    old_counts = {word: 0 for word in dict.fromkeys(settings_automation.STATE_WORDS.values())}
    for r in old:
        old_counts[settings_automation.STATE_WORDS.get(r["state"], "Learning")] += 1
    assert settings_automation.supplier_counts(store) == old_counts


def test_supplier_learning_lists_the_first_vendors_and_shows_more(client, store, monkeypatch):
    from ap_coder.web.pages import learning

    _vendors(store)
    monkeypatch.setattr(learning, "SUPPLIERS_SHOWN", 4)
    page = html.unescape(client.get("/learning?tab=suppliers").text)
    assert page.count('class="card an-supplier"') == 4 and "4 of 6 vendors shown" in page
    assert 'href="/learning?tab=suppliers&shown=8#vendor-5"' in page
    more = client.get("/learning?tab=suppliers&shown=8").text
    assert more.count('class="card an-supplier"') == 6 and "vendors shown" not in more
