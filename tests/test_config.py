import pytest

from bookfetch.sources.annas import DEFAULT_DOMAINS, MissingKeyError, domains, load_key


def test_domains_default():
    assert domains({}) == DEFAULT_DOMAINS


def test_domains_env_override():
    assert domains({"ANNAS_DOMAINS": " a.example , b.example,, "}) == ["a.example", "b.example"]


def test_domains_blank_override_falls_back():
    assert domains({"ANNAS_DOMAINS": " , "}) == DEFAULT_DOMAINS


def test_key_from_env():
    assert load_key(env={"ANNAS_KEY": " envkey \n"}) == "envkey"


def test_key_file_env(tmp_path):
    f = tmp_path / "key"
    f.write_text("filekey\n")
    assert load_key(env={"ANNAS_KEY_FILE": str(f)}) == "filekey"


def test_env_key_beats_key_file_env(tmp_path):
    f = tmp_path / "key"
    f.write_text("filekey")
    assert load_key(env={"ANNAS_KEY": "envkey", "ANNAS_KEY_FILE": str(f)}) == "envkey"


def test_flag_beats_env(tmp_path):
    f = tmp_path / "key"
    f.write_text("flagkey")
    assert load_key(str(f), env={"ANNAS_KEY": "envkey", "ANNAS_KEY_FILE": "/nonexistent"}) == "flagkey"


def test_no_key():
    with pytest.raises(MissingKeyError, match="ANNAS_KEY"):
        load_key(env={})


def test_missing_key_file(tmp_path):
    with pytest.raises(MissingKeyError, match="cannot read key file"):
        load_key(str(tmp_path / "missing"), env={})


def test_empty_key_file(tmp_path):
    f = tmp_path / "key"
    f.write_text("  \n")
    with pytest.raises(MissingKeyError, match="empty"):
        load_key(env={"ANNAS_KEY_FILE": str(f)})
