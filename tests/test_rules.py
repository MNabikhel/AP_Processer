"""Fixed coding rules: vendor and/or words in a line -> GL account (and cost center)."""

import json

import pytest

from ap_coder import rules
from ap_coder.inference import InvoiceCoder
from ap_coder.pipeline import InvoicePipeline
from ap_coder.rules import Rule
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store

from .conftest import FakeOpenAI, make_completion


@pytest.mark.parametrize(
    "rule, vendor, description, matches",
    [
        (Rule("Northwind", "", "6000"), "Northwind IT Solutions Inc.", "anything", True),
        (Rule("northwind it solutions inc", "", "6000"), "NORTHWIND IT SOLUTIONS, INC.", "x", True),
        (Rule("Purolator", "", "6000"), "Northwind IT Solutions Inc.", "x", False),
        (Rule("", "freight", "5200"), "Any", "Freight  charge", True),
        (Rule("", "freight charge", "5200"), "Any", "FREIGHT   charge to site", True),
        (Rule("", "freight", "5200"), "Any", "Delivery & handling", False),
        (Rule("Northwind", "delivery", "6800"), "Northwind IT", "Delivery & handling", True),
        (Rule("Northwind", "delivery", "6800"), "Other Co", "Delivery & handling", False),
        (Rule("", "", "6000"), "Any", "x", False),  # a rule needs a vendor or words
        (Rule("Northwind", "", ""), "Northwind", "x", False),  # ...and a GL account
    ],
)
def test_matching(rule, vendor, description, matches):
    assert rule.matches(vendor, description) is matches


def test_most_specific_rule_wins():
    vendor_only = Rule("Northwind", "", "6000", id=1)
    words = Rule("", "delivery", "6800", id=2)
    longer_words = Rule("", "delivery & handling", "6810", id=3)
    both = Rule("Northwind", "delivery", "6820", id=4)
    all_rules = [vendor_only, words, longer_words, both]
    assert rules.rule_for(all_rules, "Northwind IT", "Delivery & handling") == both
    assert rules.rule_for(all_rules, "Other", "Delivery & handling") == longer_words
    assert rules.rule_for(all_rules, "Northwind IT", "Laptop") == vendor_only
    assert rules.rule_for(all_rules, "Other", "Laptop") is None


def test_apply_changes_only_matching_lines(ground_truth):
    coding = InvoiceCoding.model_validate(ground_truth)
    rule = Rule("", "delivery", "5200", "CC100")
    coded, changes = rules.apply(coding, [rule, Rule("", "laptop", "6010")])  # the laptop rule changes nothing
    assert [c["line_number"] for c in changes] == [5]
    assert changes[0] == {"line_number": 5, "rule": "any vendor, line contains “delivery”", "gl_from": "6800",
                          "gl_to": "5200", "cc_from": "CC400", "cc_to": "CC100"}  # fmt: skip
    lines = {li.line_number: li for li in coded.line_items}
    assert (lines[5].predicted_gl_code, lines[5].predicted_cost_center) == ("5200", "CC100")
    assert lines[1].predicted_gl_code == "6010" and coding.line_items[4].predicted_gl_code == "6800"  # a copy
    assert "Line 5: 5200 · CC100" in rules.describe(changes[0]) and "6800 · CC400" in rules.describe(changes[0])
    _, none = rules.apply(coding, [])
    assert none == []
    kept, _ = rules.apply(coding, [Rule("", "delivery", "5200")])  # no cost center: the AI's is kept
    assert kept.line_items[4].predicted_cost_center == "CC400"


def test_store_keeps_rules_and_their_age(tmp_path):
    store = Store(tmp_path / "r.db")
    store.save_coding_rules([Rule("Purolator", "", "5200"), Rule("", "", "6000"), Rule("", "freight", "5200.0")])
    saved = store.coding_rules()
    assert [(r.vendor, r.contains, r.gl_code) for r in saved] == [("Purolator", "", "5200"), ("", "freight", "5200")]
    first_id = saved[0].id
    store.save_coding_rules([Rule("", "courier", "5200"), *saved])
    again = {r.vendor or r.contains: r.id for r in store.coding_rules()}
    assert again["Purolator"] == first_id and again["courier"] > first_id  # unchanged rules keep their id
    assert store.events(limit=1)[0]["action"] == "rules_changed"


def test_rules_are_applied_when_processing(tmp_path, settings, reference, ground_truth, sample_markdown_path):
    store = Store(tmp_path / "r.db")
    store.save_coding_rules([Rule("", "delivery", "6900")])
    client = FakeOpenAI(make_completion(json.dumps(ground_truth)))
    pipe = InvoicePipeline(settings, reference, coder=InvoiceCoder(settings, reference, client=client), store=store)
    result = pipe.process(sample_markdown_path)
    inv = store.get_invoice(result.invoice_id)
    assert inv["ai_output"]["line_items"][4]["predicted_gl_code"] == "6900"
    assert inv["meta"]["rules_applied"][0]["gl_from"] == "6800"


def test_suggestions_from_past_coding():
    feedback = [{"vendor_key": "acme", "vendor_name": "Acme Ltd", "final_gl": "6000", "final_cc": "CC1"}] * 5 + [
        {"vendor_key": "mixed", "vendor_name": "Mixed", "final_gl": gl, "final_cc": ""} for gl in "1234567"
    ]
    ((rule, n),) = rules.suggest(feedback, [])
    assert (rule.vendor, rule.gl_code, rule.cost_center, n) == ("Acme Ltd", "6000", "CC1", 5)
    assert rules.suggest(feedback, [Rule("acme ltd", "", "6000")]) == []  # already a rule
    assert rules.suggest(feedback[:4], []) == []  # too few lines


def test_upgrade_from_version_10(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    Store(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE coding_rules")
        conn.execute("UPDATE settings SET value = '10' WHERE key = 'schema_version'")
    store = Store(path)
    assert store.coding_rules() == [] and store.get_setting("schema_version") == "11"
