"""SQLite-backed memory store. One table, FTS5 index, auto-compaction."""
import sqlite3, json, time
from pathlib import Path

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
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(key, value, content='memories', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, key, value) VALUES (new.id, new.key, new.value);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.id, old.key, old.value);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.id, old.key, old.value);
END;
"""

class MemoryStore:
    def __init__(self, path: str | Path = None):
        path = path or Path.home() / ".codex-memory" / "store.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def remember(self, key: str, value: str, kind: str = "fact") -> bool:
        now = int(time.time())
        self.db.execute(
            "INSERT INTO memories(key,value,kind,created,updated) VALUES(?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (key, value, kind, now, now)
        )
        self.db.commit()
        return True

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        rows = self.db.execute(
            "SELECT m.key, m.value, m.kind, m.updated, m.access_count FROM memories m "
            "JOIN memories_fts f ON m.id = f.rowid "
            "WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?", (query, limit)
        ).fetchall()
        for r in rows:
            self.db.execute("UPDATE memories SET access_count = access_count + 1 WHERE key = ?", (r[0],))
        self.db.commit()
        return [{"key": r[0], "value": r[1], "kind": r[2], "updated": r[3], "hits": r[4]} for r in rows]

    def forget(self, key: str) -> bool:
        self.db.execute("DELETE FROM memories WHERE key = ?", (key,))
        self.db.commit()
        return True

    def compact(self, older_than_days: int = 30) -> int:
        cutoff = int(time.time()) - older_than_days * 86400
        old = self.db.execute(
            "SELECT key, value FROM memories WHERE updated < ? AND kind != 'summary'", (cutoff,)
        ).fetchall()
        for key, val in old:
            summary = val[:200] + "…" if len(val) > 200 else val
            self.db.execute(
                "INSERT INTO memories(key,value,kind,created,updated) VALUES(?,?,'summary',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                (key + ":summary", f"[压缩摘要] {summary}", cutoff, cutoff)
            )
            self.db.execute("DELETE FROM memories WHERE key = ?", (key,))
        self.db.commit()
        return len(old)

    def stats(self) -> dict:
        r = self.db.execute("SELECT COUNT(*), SUM(LENGTH(value)) FROM memories").fetchone()
        return {"entries": r[0], "bytes": r[1] or 0}
