"""The .env file as people on Windows leave it: saved by Notepad with a byte-order mark, a key set twice."""

from ap_coder.config import Settings
from ap_coder.envfile import read_env, write_env


def test_a_byte_order_mark_does_not_hide_the_first_setting(tmp_path, monkeypatch):
    # Notepad's "UTF-8 with BOM": the file starts with U+FEFF before the first key.
    env = tmp_path / ".env"
    env.write_bytes("AP_REVIEWER=Zoé Tremblay\r\nAP_REVIEW_THRESHOLD=0.9\r\n".encode("utf-8-sig"))
    assert read_env(env) == {"AP_REVIEWER": "Zoé Tremblay", "AP_REVIEW_THRESHOLD": "0.9"}

    write_env(env, {"AP_REVIEWER": "Pat"})
    assert env.read_text(encoding="utf-8-sig").count("AP_REVIEWER=") == 1
    assert read_env(env)["AP_REVIEWER"] == "Pat"

    monkeypatch.delenv("AP_REVIEWER", raising=False)
    monkeypatch.delenv("AP_REVIEW_THRESHOLD", raising=False)
    env.write_bytes("AP_REVIEW_THRESHOLD=0.7\r\n".encode("utf-8-sig"))
    assert Settings.from_env(env).engine.review_threshold == 0.7


def test_a_number_typed_with_a_decimal_comma_or_a_typo_does_not_stop_the_app(tmp_path, monkeypatch):
    # A French-Canadian keyboard writes 0,9; a typo must not stop every command (doctor included).
    monkeypatch.setenv("AP_REVIEW_THRESHOLD", "0,9")
    monkeypatch.setenv("AP_LLM_TIMEOUT_SECONDS", "10 min")
    monkeypatch.setenv("AP_VISION_MAX_PAGES", "five")
    monkeypatch.setenv("AZURE_OPENAI_TEMPERATURE", "0,2")
    s = Settings.from_env(tmp_path / "missing.env")
    assert s.engine.review_threshold == 0.9
    assert s.openai.temperature == 0.2
    assert s.llm.timeout_seconds == 600.0 and s.engine.vision_max_pages == 5  # the defaults


def test_a_setting_written_below_its_commented_example_is_the_one_changed(tmp_path):
    # The example line is still commented out above the line someone added by hand.
    env = tmp_path / ".env"
    env.write_text("# Empty = the loaded model.\n# AP_LLM_MODEL=\nAP_LLM_MODEL=old-model\n", encoding="utf-8")
    write_env(env, {"AP_LLM_MODEL": "new-model"})
    assert read_env(env)["AP_LLM_MODEL"] == "new-model"
    assert env.read_text(encoding="utf-8").count("AP_LLM_MODEL=new-model") == 1
