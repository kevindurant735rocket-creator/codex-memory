"""SQLite-backed memory store. FTS5 trigram tokenizer for CJK support.

Design notes
------------
* The FTS5 table is *external content* (``content='memories'``). Any UPDATE to
  the source table must be mirrored into the index: the ``memories_au`` trigger
  deletes the old entry and re-inserts the new one. A delete-only trigger
  silently makes every updated row unsearchable -- which is exactly what
  ``recall()`` does on every hit, because it bumps ``access_count``.
* ``trigram`` cannot match queries shorter than 3 characters, so short queries
  are rejected up front with a clear error instead of an empty result set.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

#: Minimum query length for the trigram tokenizer.
MIN_QUERY_CHARS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    created INTEGER NOT NULL,
    updated INTEGER NOT NULL,
    access_count INTEGER DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    key, value,
    content='memories',
    content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, key, value) VALUES (new.id, new.key, new.value);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.id, old.key, old.value);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.id, old.key, old.value);
    INSERT INTO memories_fts(rowid, key, value) VALUES (new.id, new.key, new.value);
END;
"""


class QueryError(ValueError):
    """Raised when a search query cannot be used against the trigram index."""


def _normalize_path(path: str | Path | None) -> Path:
    """Coerce ``path`` to a :class:`Path`, defaulting to the user store."""
    if path is None:
        return Path.home() / ".codex-memory" / "store.db"
    return Path(path).expanduser()


def build_match_query(raw: str) -> str:
    """Turn free text into a safe single FTS5 MATCH expression.

    FTS5 gives raw user input its own grammar: ``user/name`` is a syntax
    error, and an unbalanced quote raises. Escaping each token keeps the
    caller's text as a *literal* phrase instead of letting it steer the
    parser -- which is also the safe default, since a query is data, not
    trusted FTS syntax.
    """
    text = (raw or "").strip()
    if not text:
        raise QueryError("query is empty")
    if len(text) < MIN_QUERY_CHARS:
        raise QueryError(
            f"query must be at least {MIN_QUERY_CHARS} characters "
            f"(trigram tokenizer cannot match shorter terms), got {len(text)}"
        )
    tokens = [t.replace('"', '""') for t in text.split()]
    if not tokens:
        raise QueryError("query is empty")
    return " ".join(f'"{t}"' for t in tokens)


def _probe_token(value: str, max_len: int = 32) -> str:
    """Pick a substring of ``value`` that FTS5 indexed as a single term.

    Takes the leading run of characters up to the first whitespace, so the
    probe corresponds to one indexed term rather than a phrase that spans
    several. Returns "" when no usable term of at least
    :data:`MIN_QUERY_CHARS` characters exists.
    """
    head = value.strip()[:max_len]
    token = head.split()[0] if head.split() else ""
    return token if len(token) >= MIN_QUERY_CHARS else ""

class MemoryStore:
    def __init__(self, path: str | Path = None):
        self.path = _normalize_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        #: Set when an older store was repaired and rows were recovered.
        self.migrated_from_legacy = False
        # A pre-existing store may carry the old single-statement trigger, and
        # CREATE TRIGGER IF NOT EXISTS would silently leave it in place.
        self._repair_triggers()

    def _repair_triggers(self) -> None:
        """Migrate a store written by an older version.

        Two things can be wrong, and fixing only the first is not enough:

        1. The ``memories_au`` trigger may be the old delete-only version, which
           would unindex every row as soon as it is read.
        2. Rows already read under that trigger are present in the index with
           *empty* content. They keep their rowid, so a rowid count still looks
           healthy while MATCH can never find them again.

        The expensive content audit (:meth:`unsearchable_count`, one MATCH per
        row) only runs when the trigger check shows this store predates the
        fix. Stores already on the current schema pay a single catalog lookup,
        so opening stays fast as the store grows.
        """
        row = self.db.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='memories_au'"
        ).fetchone()
        expected = (
            "INSERT INTO memories_fts(memories_fts, rowid, key, value) "
            "VALUES('delete', old.id, old.key, old.value);"
        )
        if row is None or expected in row["sql"]:
            return  # current schema; nothing to migrate
        self.db.execute("DROP TRIGGER memories_au")
        self.db.executescript(SCHEMA)
        self.db.commit()
        if self._verify_index():
            self.migrated_from_legacy = True

    def _verify_index(self) -> int:
        """Rebuild the index if any row has become unmatchable, and report it."""
        broken = self.unsearchable_count()
        if broken:
            self.db.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
            self.db.commit()
        return broken

    def unsearchable_count(self) -> int:
        """Count rows present in the table whose indexed content is unusable.

        A row that lost its indexed content keeps its rowid, so joining on
        rowid is not enough to detect it: the row is present but matches
        nothing.

        Each row is probed with a slice of its own value. The probe is escaped
        via :func:`build_match_query`, so quotes in stored data cannot reach the
        FTS5 parser -- an earlier version interpolated the value directly and
        crashed on any value containing a double quote.

        Only the leading run of "safe" characters is used. Splitting a value on
        whitespace and quoting each piece can build a term the index never saw
        (``a" OR b`` indexes as ``a"``, ``OR``, ``b"``), which would report a
        healthy row as broken and trigger a pointless rebuild.
        """
        rows = self.db.execute("SELECT id, value FROM memories").fetchall()
        broken = 0
        for row in rows:
            probe = _probe_token(row["value"] or "")
            if not probe:
                continue
            hit = self.db.execute(
                "SELECT 1 FROM memories_fts WHERE memories_fts MATCH ? AND rowid = ?",
                (build_match_query(probe), row["id"]),
            ).fetchone()
            if hit is None:
                broken += 1
        return broken

    def rebuild_index(self) -> int:
        """Rebuild the FTS index from the source table. Returns row count."""
        self.db.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        self.db.commit()
        return self.stats()["entries"]

    def remember(self, key: str, value: str, kind: str = "fact") -> bool:
        if not key or not key.strip():
            raise ValueError("key must not be empty")
        now = int(time.time())
        self.db.execute(
            "INSERT INTO memories(key,value,kind,created,updated) VALUES(?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "kind=excluded.kind, updated=excluded.updated",
            (key, value, kind, now, now)
        )
        self.db.commit()
        return True

    def forget(self, key: str) -> bool:
        """Delete ``key``. Returns False if it was not present."""
        cur = self.db.execute("DELETE FROM memories WHERE key = ?", (key,))
        self.db.commit()
        return cur.rowcount > 0

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        match = build_match_query(query)
        rows = self.db.execute(
            "SELECT m.key, m.value, m.kind, m.updated, m.access_count FROM memories m "
            "JOIN memories_fts f ON m.id = f.rowid "
            "WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?", (match, limit)
        ).fetchall()
        if not rows:
            return []
        # Bump counters in one statement, then re-read so the returned counts
        # reflect this access instead of lagging one call behind.
        self.db.executemany(
            "UPDATE memories SET access_count = access_count + 1 WHERE key = ?",
            [(r["key"],) for r in rows],
        )
        self.db.commit()
        counts = dict(self.db.execute(
            "SELECT key, access_count FROM memories WHERE key IN "
            f"({','.join('?' * len(rows))})", [r["key"] for r in rows]
        ).fetchall())
        return [{"key": r["key"], "value": r["value"], "kind": r["kind"],
                 "updated": r["updated"], "hits": counts.get(r["key"], r["access_count"])}
                for r in rows]

    def get(self, key: str) -> dict | None:
        """Fetch one memory by exact key. Bypasses FTS, so keys with '/' work."""
        row = self.db.execute(
            "SELECT key, value, kind, updated, access_count FROM memories WHERE key = ?",
            (key,)
        ).fetchone()
        if row is None:
            return None
        return {"key": row["key"], "value": row["value"], "kind": row["kind"],
                "updated": row["updated"], "hits": row["access_count"]}

    def compact(self, older_than_days: int = 30) -> int:
        """Truncate values of stale rows in place, preserving keys and rows.

        The previous implementation deleted each row and re-inserted it under
        ``<key>:summary``. That dropped the original key, left the entry count
        unchanged, and reported a compression that never actually landed. This
        shortens the value in place: nothing is removed and each row keeps its
        identity and its place in the search index.
        """
        cutoff = int(time.time()) - older_than_days * 86400
        cur = self.db.execute(
            "UPDATE memories SET value = substr(value, 1, 200) || '…', updated = ? "
            "WHERE updated <= ? AND kind != 'summary' AND LENGTH(value) > 200",
            (cutoff, cutoff)
        )
        self.db.commit()
        return cur.rowcount

    def stats(self) -> dict:
        row = self.db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(value)), 0) AS b FROM memories"
        ).fetchone()
        searchable = self.db.execute(
            "SELECT COUNT(*) FROM memories m JOIN memories_fts f ON m.id = f.rowid"
        ).fetchone()[0]
        return {"entries": row["n"], "bytes": row["b"],
                "searchable": searchable, "unindexed": row["n"] - searchable}

    def close(self) -> None:
        try:
            self.db.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
