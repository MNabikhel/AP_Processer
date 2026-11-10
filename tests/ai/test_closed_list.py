"""The chat model chooses among the accounts offered for each line (D-212), through the benches' stand-in LM Studio:
never from the whole chart, and an answer that is not an account offered leaves the line for a person, said."""

import json
import re
from types import SimpleNamespace

from ap_coder.ai.inference import NONE_OF_THESE, SUGGEST_MAX_LINES, CodingError, InvoiceCoder, suggest_accounts
from ap_coder.ai.offline_coder import code_from_capture
from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.core.reference_data import UNASSIGNED
from tests.ai.test_local_llm import chat_reply, lm_studio, local_settings, real_fetch  # noqa: F401 (fixtures)
from tests.books import FACILITIES, MAIN, captured, load_chart, load_journal, monthly
from tests.conftest import make_completion

JANITOR, CARPET, COURIER = f"{MAIN}.7340.3307", f"{MAIN}.7340.3313", f"{MAIN}.7420.4207"
NAME = "Brightway Janitorial Ltd."
MODELS = [{"id": "qwen2.5-7b-instruct", "type": "llm", "state": "loaded"}]


def _left(store, *texts):
    """An invoice of a supplier whose lines went to three accounts: every line left, with the accounts offered."""
    reference = load_chart(store)
    store.import_vendor_master([{"vendor_name": NAME.upper(), "erp_id": "740075", "status": "active"}], by_erp_id=True)
    load_journal(store, [*monthly(740075, NAME.upper(), JANITOR, "{month} janitorial", 12),
                         *monthly(740075, NAME.upper(), CARPET, "carpet cleaning", 5),
                         *monthly(740075, NAME.upper(), f"{FACILITIES}.7340.3307", "janitorial annex", 3)])  # fmt: skip
    result = code_from_capture(captured(NAME, *texts), reference, store.feedback_rows(), store)
    assert all(li.predicted_gl_code == UNASSIGNED for li in result.coding.line_items)
    return reference, result


def _ask(server, reference, result, notes):
    lines = [(n, li.description, li.amount) for n, li in enumerate(result.coding.line_items, 1)]
    coder = InvoiceCoder(local_settings(server.base_url), reference)
    return suggest_accounts(coder, NAME, lines, offered=result.offered, notes=notes)


def _answers(*accounts):
    rows = [{"line_number": n, "account": a, "reason": "what was bought"} for n, a in enumerate(accounts, 1) if a]
    return lambda body: chat_reply(json.dumps({"lines": rows}))


def test_the_model_chooses_among_the_accounts_offered_never_the_whole_chart(lm_studio, store):  # noqa: F811
    reference, result = _left(store, "Day porter service", "Courier parcel to the annex")
    server = lm_studio(["qwen2.5-7b-instruct"], MODELS, reply=_answers(CARPET, COURIER))
    notes: dict[int, str] = {}
    picks = _ask(server, reference, result, notes)
    assert picks == {1: ("7340.3313", MAIN, "what was bought"), 2: ("7420.4207", MAIN, "what was bought")}
    assert notes == {}
    sent = server.chats[0]
    offered = sent["response_format"]["json_schema"]["schema"]["properties"]["lines"]["items"]["properties"]["account"]
    assert offered["enum"][-1] == NONE_OF_THESE and JANITOR in offered["enum"] and len(offered["enum"]) <= 12
    assert f"{MAIN}.7340.9710" not in offered["enum"] and f"{MAIN}.7410.4107" not in offered["enum"]
    system, user = (m["content"] for m in sent["messages"])
    assert "GL Accounts" not in system and len(system) + len(user) < 4000  # no chart of accounts in the prompt
    assert (f"{JANITOR} | Janitorial Contract | General Administration | used 12 times for this supplier, e.g. "
            "“Dec janitorial”") in user  # fmt: skip
    assert re.search(rf"1\. Day porter service \| 100\.00 \| offered: {re.escape(JANITOR)}, ", user)
    assert f"{COURIER} | Courier - Domestic | General Administration | " in user  # the words of its name


def test_an_answer_that_is_not_an_account_offered_leaves_the_line_and_says_why(lm_studio, store):  # noqa: F811
    reference, result = _left(store, "Day porter service", "Window washing", "Sundry", "Odd job")
    outside = f"{MAIN}.7410.4107"  # in the chart, not offered for these lines
    wrong_unit = "04407711.7340.3313"  # an account offered, on a business unit it was not offered on
    server = lm_studio(["qwen2.5-7b-instruct"], MODELS, reply=_answers(outside, wrong_unit, NONE_OF_THESE, ""))
    notes: dict[int, str] = {}
    assert _ask(server, reference, result, notes) == {}
    assert notes == {
        1: f"The chat model answered {outside}, which is not one of the accounts offered",
        2: f"The chat model answered {wrong_unit}: that account was offered, but not on business unit 04407711",
        3: "The chat model found that none of the accounts offered fits (what was bought)",
        4: "The chat model gave no answer for this line",
    }


class _PromptCoder:
    """Enough of an InvoiceCoder for ``suggest_accounts``: keeps each prompt and its schema, answers none of them."""

    def __init__(self, reference, max_prompt_chars=24_000):
        self.reference, self.prompts, self.schemas = reference, [], []
        self.settings = local_settings("http://127.0.0.1:9/v1", max_prompt_chars=max_prompt_chars)

    def _prepare_local(self):
        return "qwen3.5-9b"

    def _local_create(self, model, messages, schema=None, **kw):
        self.prompts.append(messages)
        self.schemas.append(schema)
        numbers = [int(n) for n in re.findall(r"^(\d+)\. ", messages[-1]["content"], re.M)]
        return make_completion(json.dumps({"lines": [{"line_number": n, "account": JANITOR, "reason": "x"}
                                                     for n in numbers]}))  # fmt: skip


def test_a_200_line_invoice_is_asked_in_prompts_that_stay_within_the_budget(store):
    words = ["Day porter service", "Window washing", "Courier parcel", "Cell phone plan", "Stationery order"]
    texts = [f"{words[n % 5]} {n}: " + "long description of the work done, " * 6 for n in range(230)]
    reference, result = _left(store, *texts)
    lines = [(n, li.description, li.amount) for n, li in enumerate(result.coding.line_items, 1)]
    coder, notes = _PromptCoder(reference), {}
    picks = suggest_accounts(coder, NAME, lines, history="AP's lessons. " * 500, offered=result.offered, notes=notes)
    sizes = [sum(len(m["content"]) for m in prompt) for prompt in coder.prompts]
    assert max(sizes) <= 24_000 and len(coder.prompts) >= 5  # in batches, each within AP_LLM_MAX_PROMPT_CHARS
    assert max(len(json.dumps(schema)) for schema in coder.schemas) < 2_000  # (the whole chart was 320,000)
    assert sorted(picks) == list(range(1, SUGGEST_MAX_LINES + 1))
    assert set(notes) == set(range(SUGGEST_MAX_LINES + 1, 231)) and "first 200 lines" in notes[201]


def _pipeline_asks(monkeypatch, result, suggest):
    import ap_coder.ai.inference as inference

    monkeypatch.setattr(inference, "suggest_accounts", suggest)
    InvoicePipeline._local_accounts(SimpleNamespace(coder=SimpleNamespace(_local_model="qwen")), result, "")
    return result.coding.line_items


def test_the_models_account_is_a_guess_and_its_silence_is_said_on_the_line(store, monkeypatch):
    """B-2145: a chat model that was asked and gave nothing left no trace: the line still read as if nobody had
    been asked."""
    reference, result = _left(store, "Day porter service", "Window washing")
    seen = {}

    def one_of_two(coder, vendor, lines, history="", kept=None, within=None, offered=None, notes=None):
        seen["offered"] = offered
        notes[2] = "The chat model found that none of the accounts offered fits"
        return {1: ("7340.3313", MAIN, "carpets")}

    first, second = _pipeline_asks(monkeypatch, result, one_of_two)
    assert seen["offered"] is result.offered and seen["offered"][1][0].account == JANITOR
    assert (first.predicted_cost_center, first.predicted_gl_code) == (MAIN, "7340.3313")
    assert first.reasoning_justification.startswith("Suggested by the local model: carpets")
    assert first.decided_by == {"source": "model", "model": "qwen"} and len(first.candidates) >= 2
    assert second.predicted_gl_code == UNASSIGNED and second.decided_by == {}
    assert second.reasoning_justification.endswith("The chat model found that none of the accounts offered fits.")

    def silent(*args, **kwargs):
        raise CodingError("LM Studio gave an empty answer")

    reference, result = _left(store, "Day porter service")
    (line,) = _pipeline_asks(monkeypatch, result, silent)
    assert line.predicted_gl_code == UNASSIGNED and line.reasoning_justification == (
        "This supplier's 20 lines last year went to 3 accounts: 60% / 25% / 15%: pick the account. The chat model "
        "was asked and gave no answer (LM Studio gave an empty answer).")  # fmt: skip
