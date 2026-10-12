"""Conversation memory: JSONL backend + store facade."""
from src.memory import ConversationStore
from src.memory.jsonl_backend import JsonlBackend


def test_append_load_and_turn_cap(tmp_path):
    store = ConversationStore(JsonlBackend(str(tmp_path)))
    key = ConversationStore.key("CASE-001", "s1")
    for i in range(5):
        store.append_turn(key, f"q{i}", f"a{i}")
    assert len(store.load_all(key)) == 10
    last2 = store.load(key, max_turns=2)
    assert [m["content"] for m in last2] == ["q3", "a3", "q4", "a4"]
    assert store.load(key, max_turns=0) == []


def test_keys_isolate_cases_and_are_path_safe(tmp_path):
    backend = JsonlBackend(str(tmp_path))
    store = ConversationStore(backend)
    store.append_turn(ConversationStore.key("CASE-001", "s"), "q", "a")
    assert store.load_all(ConversationStore.key("CASE-002", "s")) == []
    store.append_turn("../../etc/passwd", "q", "a")
    assert all(p.parent == tmp_path for p in tmp_path.iterdir())


def test_corrupt_lines_tolerated(tmp_path):
    backend = JsonlBackend(str(tmp_path))
    backend.append("k", "user", "hello")
    with backend._path("k").open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    backend.append("k", "assistant", "hi")
    assert [m["role"] for m in backend.load_messages("k")] == ["user", "assistant"]


def test_history_block_format():
    block = ConversationStore.format_history_block([{"role": "user", "content": "q"},
                                                    {"role": "assistant", "content": "a"}])
    assert block.startswith("[Conversation so far]") and "Analyst: q" in block and "[Current request]" in block
    assert ConversationStore.format_history_block([]) == ""
