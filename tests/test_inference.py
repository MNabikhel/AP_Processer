import json

import pytest

from ap_coder.config import OpenAISettings, Settings
from ap_coder.extraction import result_from_text
from ap_coder.imaging import PageImage
from ap_coder.inference import CodingError, InvoiceCoder, ModelProfile

from .conftest import FakeOpenAI, make_completion


@pytest.mark.parametrize(
    ("name", "vision", "reasoning"),
    [
        ("gpt-4o", True, False),
        ("gpt-4o-mini", True, False),
        ("gpt-4.1", True, False),
        ("o4-mini", True, True),
        ("o3-mini", False, True),
        ("gpt-5", True, True),
        ("gpt-35-turbo", False, False),
    ],
)
def test_model_profile(name, vision, reasoning):
    profile = ModelProfile.resolve(name)
    assert (profile.supports_vision, profile.reasoning) == (vision, reasoning)


def test_profile_vision_override():
    assert ModelProfile.resolve("my-custom-model", supports_vision=False).supports_vision is False


def test_code_sends_strict_schema_and_parses(settings, reference, ground_truth, sample_markdown_path):
    client = FakeOpenAI(make_completion(json.dumps(ground_truth)))
    coder = InvoiceCoder(settings, reference, client=client)

    result = coder.code(result_from_text(sample_markdown_path))

    assert result.coding.invoice_number == "NW-2026-0912"
    assert result.usage == {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "total_tokens": 1200,
        "cached_prompt_tokens": 512,
    }
    call = client.calls[0]
    assert call["model"] == "gpt-4o"
    assert call["temperature"] == 0.0
    assert call["seed"] == 42
    assert call["max_completion_tokens"] == 8000
    assert call["response_format"]["json_schema"]["strict"] is True
    system, user = call["messages"]
    assert system["role"] == "system" and "GL Accounts" in system["content"]
    assert "CC410" in system["content"]
    assert "Carried forward" in user["content"]


def test_reasoning_model_omits_sampling_params(reference, ground_truth, sample_markdown_path):
    settings = Settings(openai=OpenAISettings(deployment="ap-coder", model_name="o4-mini", reasoning_effort="medium"))
    client = FakeOpenAI(make_completion(json.dumps(ground_truth)))
    InvoiceCoder(settings, reference, client=client).code(result_from_text(sample_markdown_path))
    call = client.calls[0]
    assert call["model"] == "ap-coder"
    assert "temperature" not in call and "seed" not in call
    assert call["reasoning_effort"] == "medium"


def test_vision_images_attached(settings, reference, ground_truth, sample_markdown_path):
    client = FakeOpenAI(make_completion(json.dumps(ground_truth)))
    coder = InvoiceCoder(settings, reference, client=client)
    img = PageImage(1, "image/png", b"\x89PNG")
    result = coder.code(result_from_text(sample_markdown_path), [img])
    content = client.calls[0]["messages"][1]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result.images_attached == 1


def test_vision_images_dropped_for_text_only_model(reference, ground_truth, sample_markdown_path):
    settings = Settings(openai=OpenAISettings(deployment="x", model_name="o3-mini"))
    client = FakeOpenAI(make_completion(json.dumps(ground_truth)))
    result = InvoiceCoder(settings, reference, client=client).code(
        result_from_text(sample_markdown_path), [PageImage(1, "image/png", b"x")]
    )
    assert isinstance(client.calls[0]["messages"][1]["content"], str)
    assert result.images_attached == 0


def test_repairs_value_level_errors(settings, reference, ground_truth, sample_markdown_path):
    bad = dict(ground_truth, invoice_date="14/09/2026")
    client = FakeOpenAI(make_completion(json.dumps(bad)), make_completion(json.dumps(ground_truth)))
    result = InvoiceCoder(settings, reference, client=client).code(result_from_text(sample_markdown_path))
    assert result.attempts == 2
    assert result.usage["total_tokens"] == 2400
    repair_msgs = client.calls[1]["messages"]
    assert repair_msgs[-2]["role"] == "assistant"
    assert "failed validation" in repair_msgs[-1]["content"]


def test_gives_up_after_repair_budget(settings, reference, ground_truth, sample_markdown_path):
    bad = json.dumps(dict(ground_truth, confidence_score=7))
    client = FakeOpenAI(make_completion(bad), make_completion(bad))
    with pytest.raises(CodingError, match="after 2 attempt"):
        InvoiceCoder(settings, reference, client=client).code(result_from_text(sample_markdown_path))


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"refusal": "no"}, "refused"),
        ({"finish_reason": "length"}, "truncated"),
        ({"finish_reason": "content_filter"}, "content filter"),
    ],
)
def test_terminal_failures(settings, reference, sample_markdown_path, kwargs, match):
    client = FakeOpenAI(make_completion("", **kwargs))
    with pytest.raises(CodingError, match=match):
        InvoiceCoder(settings, reference, client=client).code(result_from_text(sample_markdown_path))


def test_history_is_sent_with_the_document(settings, reference, ground_truth, sample_markdown_path):
    client = FakeOpenAI(make_completion(json.dumps(ground_truth)))
    coder = InvoiceCoder(settings, reference, client=client)
    coder.code(result_from_text(sample_markdown_path), history="## Approved coding history from your AP team\n| x |")
    system, user = client.calls[0]["messages"]
    assert user["content"].startswith("## Approved coding history")
    assert "Approved coding history" not in system["content"].split("# Enterprise Reference Data")[1]


def test_prompt_mentions_canadian_taxes(settings, reference):
    coder = InvoiceCoder(settings, reference, client=FakeOpenAI())
    assert "QST" in coder.system_prompt and "TVQ" in coder.system_prompt
    assert "tax_lines" in coder.schema["properties"]
