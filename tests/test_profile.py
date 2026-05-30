import pytest

from src.lib.profile import ThreadPack, load_thread_pack


@pytest.fixture
def example_profile(monkeypatch):
    monkeypatch.setenv("PROFILE_DIR", "Profile.example")
    load_thread_pack.cache_clear()
    yield
    load_thread_pack.cache_clear()


def test_load_thread_pack_includes_thread_files_excludes_voice(example_profile):
    pack = load_thread_pack()
    assert isinstance(pack, ThreadPack)
    assert "Thread voice" in pack.thread_voice
    assert pack.thread_drafts.strip() != ""
    block = pack.as_prompt_block()
    # factual files included
    assert pack.resume in block
    # cold-email voice is intentionally excluded from the thread pack
    assert not hasattr(pack, "voice")
    assert not hasattr(pack, "past_drafts")
