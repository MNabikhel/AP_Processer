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


def test_a_data_folder_set_with_quotes_on_windows_is_the_folder(tmp_path, monkeypatch):
    # cmd's  set AP_PRIVATE_DIR="C:\Users\me\OneDrive - Acme\AP"  keeps the quotes in the value.
    from ap_coder import paths

    folder = tmp_path / "OneDrive - Acme" / "AP"
    monkeypatch.setenv("AP_PRIVATE_DIR", f' "{folder}" ')
    assert paths.private_dir() == folder
    monkeypatch.setenv("AP_ENV_FILE", f'"{folder / ".env"}"')
    assert paths.env_file() == folder / ".env"


def test_an_inline_comment_is_not_part_of_the_value(tmp_path):
    """Read as python-dotenv (the app) reads it, so the installer and Settings never write a comment back into a
    key or an endpoint."""
    from dotenv import dotenv_values

    env = tmp_path / ".env"
    lines = [
        "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://di-prod.cognitiveservices.azure.com/   # prod resource",
        'AZURE_OPENAI_DEPLOYMENT="gpt 4o" # the shared one',
        "AZURE_OPENAI_API_KEY='abc#123' # single quotes",
        "AP_REVIEWER=Pat#2",  # no space before #: part of the value
        "export AP_REVIEW_THRESHOLD=0.9",
        'AP_LLM_MODEL="say \\"hi\\" C:\\\\x"',
    ]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    values = read_env(env)
    assert values == {k: v for k, v in dotenv_values(env).items() if v is not None}
    assert values["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"] == "https://di-prod.cognitiveservices.azure.com/"
    assert values["AZURE_OPENAI_DEPLOYMENT"] == "gpt 4o" and values["AP_REVIEWER"] == "Pat#2"
    assert values["AP_LLM_MODEL"] == 'say "hi" C:\\x'

    write_env(env, {"AP_REVIEWER": "Jo"})  # the installer saving one answer keeps the others as they read
    assert read_env(env)["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"] == "https://di-prod.cognitiveservices.azure.com/"


def test_a_comment_right_after_the_equals_sign_is_an_empty_value(tmp_path):
    """``KEY= #paste key here`` is an empty key (the hint is not the key), as python-dotenv reads it; ``KEY=#x``,
    ``abc#def``, a URL's ``#frag`` and quoted values keep their ``#``."""
    from dotenv import dotenv_values

    env = tmp_path / ".env"
    lines = ["AZURE_OPENAI_API_KEY= #paste key here", "A=\t# tab", "B=#notcomment", "C=abc#def", "D=https://x.y/#frag",
             'E="abc #def"', "F='x #y'  # note", "G=val # comment", "H=  "]  # fmt: skip
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    values = read_env(env)
    assert values == {k: v for k, v in dotenv_values(env).items() if v is not None}
    assert values["AZURE_OPENAI_API_KEY"] == "" and values["B"] == "#notcomment"
