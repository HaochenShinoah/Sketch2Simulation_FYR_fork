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
