"""Regression tests.

Each test pins a defect that shipped in v0.1.0, so the behaviour cannot
silently regress again. Run with:  python -m pytest tests -q
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_memory.store import MemoryStore, QueryError, build_match_query


@pytest.fixture
def store(tmp_path):
    with MemoryStore(tmp_path / "t.db") as s:
        yield s


# --- D1: reading a memory must not make it unfindable ------------------------
def test_reading_does_not_break_search(store):
    store.remember("a/b", "unique_token_a")
    store.recall("unique_token_a")
    store.recall("unique_token_a")
    assert len(store.recall("unique_token_a", 5)) == 1, "row became unsearchable after read"


def test_every_row_stays_indexed_after_reads(store):
    for i in range(6):
        store.remember(f"k{i}", f"token{i}")
    for i in range(6):
        store.recall(f"token{i}", 5)
    assert store.unsearchable_count() == 0
    assert store.stats()["unindexed"] == 0


def test_upsert_keeps_new_value_searchable(store):
    store.remember("k", "old_token_value")
    store.remember("k", "new_token_value")
    assert len(store.recall("new_token_value", 5)) == 1
    assert store.recall("old_token_value", 5) == []


# --- D2: compaction must not destroy data ------------------------------------
def test_compact_preserves_keys_and_rows(store):
    store.remember("keep/one", "A" * 500)
    store.remember("keep/two", "B" * 500)
    n = store.compact(older_than_days=0)
    assert n == 2, "compact reported no work"
    assert store.stats()["entries"] == 2
    assert store.get("keep/one") is not None
    assert store.get("keep/two") is not None
    assert len(store.get("keep/one")["value"]) == 201


def test_compact_leaves_fresh_rows_alone(store):
    store.remember("fresh", "A" * 500)
    assert store.compact(older_than_days=30) == 0
    assert len(store.get("fresh")["value"]) == 500


def test_compacted_rows_remain_searchable(store):
    store.remember("k", "A" * 500)
    store.compact(older_than_days=0)
    assert len(store.recall("AAAA", 5)) == 1


# --- D3: FTS5 query syntax must not leak in ----------------------------------
@pytest.mark.parametrize("q", ['"', "name AND", "NEAR(a b)", "()", "*"])
def test_hostile_queries_do_not_crash(store, q):
    try:
        store.recall(q, 5)
    except QueryError:
        pass  # explicit, actionable error


@pytest.mark.parametrize(
    "value",
    ['has " AND NOT x', 'a" OR b', 'x"*"', '""', "简洁直接,重产出", "a b c d", "v/with/slash"],
)
def test_quote_heavy_values_do_not_break_open_or_audit(store, value):
    """Storing quotes must not crash the store, nor look like corruption."""
    store.remember("k", value)
    assert store.unsearchable_count() == 0


def test_migration_flag_only_set_once(tmp_path):
    """The expensive audit must not repeat on every open of a repaired store."""
    path = tmp_path / "legacy.db"
    _make_legacy_store(path, damaged=True)
    with MemoryStore(path) as s:
        assert s.migrated_from_legacy is True
    with MemoryStore(path) as s:
        assert s.migrated_from_legacy is False


def test_slash_keys_are_searchable(store):
    store.remember("user/name", "Alaric")
    assert len(store.recall("user/name", 5)) == 1


@pytest.mark.parametrize("q", ["", "a", "ab"])
def test_short_queries_raise_clear_error(store, q):
    with pytest.raises(QueryError):
        store.recall(q)


def test_build_match_query_quotes_tokens():
    assert build_match_query("hello world") == '"hello" "world"'
    # FTS5 escapes an embedded quote by doubling it, inside a quoted string.
    assert build_match_query('say "hi"') == '"say" """hi"""'


# --- D4/D6/D7: API honesty and lifecycle -------------------------------------
def test_accepts_str_path(tmp_path):
    with MemoryStore(str(tmp_path / "s.db")) as s:
        s.remember("k", "v")
        assert s.get("k") is not None


def test_forget_reports_honest_result(store):
    store.remember("gone", "v")
    assert store.forget("gone") is True
    assert store.forget("never/existed") is False


def test_remember_rejects_empty_key(store):
    with pytest.raises(ValueError):
        store.remember("", "v")


def test_hits_reflect_this_access(store):
    store.remember("k", "hello")
    assert store.recall("hello", 5)[0]["hits"] == 1
    assert store.recall("hello", 5)[0]["hits"] == 2


def test_get_bypasses_fts_and_handles_slash(store):
    store.remember("user/name", "Alaric")
    assert store.get("user/name")["value"] == "Alaric"
    assert store.get("missing/key") is None


# --- migration from the broken v0.1.0 trigger --------------------------------
def _make_legacy_store(path: Path, damaged: bool = False) -> None:
    """Build a store using the v0.1.0 delete-only trigger.

    With ``damaged=True`` one row is read while the broken trigger is active,
    which empties its indexed content while leaving the rowid in place.
    """
    db = sqlite3.connect(str(path))
    db.executescript("""
    CREATE TABLE memories(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE,
      value TEXT, kind TEXT, created INT, updated INT, access_count INT DEFAULT 0);
    CREATE VIRTUAL TABLE memories_fts USING fts5(key,value,content='memories',
      content_rowid='id',tokenize='trigram');
    CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
      INSERT INTO memories_fts(rowid,key,value) VALUES(new.id,new.key,new.value); END;
    CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
      INSERT INTO memories_fts(memories_fts,rowid,key,value)
      VALUES('delete',old.id,old.key,old.value); END;
    """)
    for i in range(3):
        db.execute("INSERT INTO memories(key,value,kind,created,updated) "
                   "VALUES(?,?,'fact',0,0)", (f"k{i}", f"token{i}"))
    db.commit()
    if damaged:
        db.execute("UPDATE memories SET access_count=access_count+1 WHERE key='k0'")
    db.commit()
    db.close()


def test_migrates_store_with_delete_only_trigger(tmp_path):
    path = tmp_path / "legacy.db"
    _make_legacy_store(path, damaged=True)
    with MemoryStore(path) as s:
        assert len(s.recall("token0", 5)) == 1, "memory lost to old trigger was not recovered"
        assert s.unsearchable_count() == 0


def test_detects_content_loss_even_when_rowids_look_healthy(tmp_path):
    """A row emptied by the old trigger keeps its rowid, so counts lie."""
    path = tmp_path / "legacy.db"
    _make_legacy_store(path, damaged=True)
    probe = MemoryStore.__new__(MemoryStore)
    probe.path = path
    probe.db = sqlite3.connect(str(path))
    probe.db.row_factory = sqlite3.Row
    with probe:
        assert probe.stats()["unindexed"] == 0, "rowid join cannot see the damage"
        assert probe.unsearchable_count() == 1, "content audit must catch it"
