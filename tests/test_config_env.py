"""The .env reader must accept what Windows tools actually write."""

from __future__ import annotations

from sketch2sim import config


def test_load_dotenv_accepts_bom_utf16_quotes_and_comments(tmp_path, monkeypatch):
    cases = {
        "ascii.env": b"OPENAI_API_KEY=sk-ascii\n",
        "bom.env": "OPENAI_API_KEY=sk-bom\n".encode("utf-8-sig"),  # Notepad
        "utf16.env": "OPENAI_API_KEY=sk-utf16\n".encode(
            "utf-16"
        ),  # PowerShell 5.1 echo
        "quoted.env": b'export OPENAI_API_KEY="sk-quoted"  # trailing comment\n',
        "comment.env": b"# only a comment\nOPENAI_API_KEY=sk-inline # note\n",
    }
    expected = {
        "ascii.env": "sk-ascii",
        "bom.env": "sk-bom",
        "utf16.env": "sk-utf16",
        "quoted.env": "sk-quoted",
        "comment.env": "sk-inline",
    }
    for name, data in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        loaded = config.load_dotenv(path)
        assert loaded == {"OPENAI_API_KEY": expected[name]}, name
    # an existing variable is never overridden
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert config.load_dotenv(tmp_path / "ascii.env") == {}
    assert config.load_dotenv(tmp_path / "missing.env") == {}


def test_default_models_and_effort():
    """Every agent defaults to GPT-6 Astra; the embedding model is separate."""
    import re
    from pathlib import Path

    assert config.DEFAULT_VISION_MODEL == "gpt-6-astra"
    assert config.DEFAULT_CODE_MODEL == "gpt-6-astra"
    assert config.DEFAULT_REASONING_CODE_MODEL == "gpt-6-astra"
    assert config.DEFAULT_LIGHT_MODEL == "gpt-6-astra"
    assert config.DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"
    source = Path(config.__file__).read_text(encoding="utf-8")
    assert re.search(r'_env_str\("S2S_LLM_REASONING_EFFORT", "max"\)', source)


def test_default_caps_and_timeout_fit_reasoning_effort_max():
    """At effort "max" the reasoning tokens count against the per-agent caps,
    so every cap leaves room for a long think, and a call may run long."""
    caps = {
        "descriptor": config.DESCRIPTOR_MAX_OUTPUT_TOKENS,
        "description_validator": config.DESCRIPTION_VALIDATOR_MAX_OUTPUT_TOKENS,
        "extractor": config.EXTRACTOR_MAX_OUTPUT_TOKENS,
        "basis": config.BASIS_MAX_OUTPUT_TOKENS,
        "instantiation": config.INSTANTIATION_MAX_OUTPUT_TOKENS,
        "configuration": config.CONFIGURATION_MAX_OUTPUT_TOKENS,
        "fixer": config.FIXER_MAX_OUTPUT_TOKENS,
    }
    assert all(cap >= 32000 for cap in caps.values()), caps
    # the cs7 descriptor used 27 325 output tokens (25 863 of them reasoning)
    assert caps["descriptor"] >= 2 * 27325
    assert caps["fixer"] == max(caps.values())  # it echoes the whole script
    assert config.LLM_TIMEOUT >= 3600
    assert config.LLM_MAX_RETRIES >= 3
