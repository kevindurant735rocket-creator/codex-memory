# codex-memory

Cross-session memory for agents: a SQLite table with an FTS5 index, one
`MemoryStore` class, one `cm` CLI. Standard library only.

~280 lines of implementation, 193 lines of tests, 31 tests passing.

## Real output

```
$ cm add "user/name" "Alaric"
✓ 记住: user/name
$ cm add "pref/style" "简洁直接,重产出"
✓ 记住: pref/style

$ cm search "简洁直接"
  [fact] pref/style = 简洁直接,重产出  (hits:1)

$ cm search "user/name"
  [fact] user/name = Alaric  (hits:1)

$ cm get "user/name"
[fact] user/name = Alaric  (hits:1)

$ cm stats
条目: 4, 大小: 68 字节

$ cm doctor
✓ 索引健康, 无需修复

$ cm search "雅思"
✗ 搜索失败: query must be at least 3 characters (trigram tokenizer cannot match shorter terms), got 2
```

The 3-character floor is enforced with an explicit error, not a silent empty
result. `cm search "user/name"` works because keys are data, not FTS syntax.

```
$ python -m pytest tests -q
31 passed in 0.14s
```

## Install

```bash
pip install -e .
```

Python 3.10+ (uses `str | None`). No runtime dependencies — FTS5 ships with
CPython's bundled SQLite. Installs the `cm` console script.

## Usage

```bash
cm add KEY VALUE [--kind K]   # upsert
cm search QUERY [--limit N]   # default limit 5
cm get KEY                    # exact-key read, bypasses FTS
cm stats                      # entries, bytes, index health
cm doctor                     # audit and rebuild the FTS index
cm compact [--days N]         # default 30
cm forget KEY
cm profile [--raw]
```

`key` is `TEXT UNIQUE`, so `add` on an existing key overwrites value, kind, and
`updated`. `kind` defaults to `fact`.

Library use:

```python
from codex_memory import MemoryStore

with MemoryStore() as s:                      # ~/.codex-memory/store.db
    s.remember("pref/style", "简洁直接")
    s.recall("简洁直接")                        # [{"key": ..., "hits": 1, ...}]
    s.get("pref/style")
    s.forget("pref/style")                     # False if absent
    s.stats()                                  # entries/bytes/searchable/unindexed
    s.close()
```

**The DB path is hardcoded.** `MemoryStore()` with no argument resolves to
`~/.codex-memory/store.db`. The `cm` CLI passes no path and has no `--db` flag,
so there is no way to point the CLI at another database.

## What it does under the hood

- `memories` table + `memories_fts` as an **external-content** FTS5 virtual
  table, `tokenize='trigram'`.
- Three triggers mirror writes: `memories_ai` (insert into FTS),
  `memories_ad` (FTS `'delete'` command), `memories_au` (delete + re-insert).
- **Every token in a query is double-quoted** (`build_match_query`) before it
  reaches `MATCH`, so `/`, quotes, and other FTS metacharacters are treated as
  literals. This is what makes `user/name` keys searchable.
- `_repair_triggers()` runs on open: if `memories_au` does not contain the
  delete-then-insert statement, it drops the trigger, recreates the schema, and
  rebuilds the index if `unsearchable_count()` is nonzero. Sets
  `migrated_from_legacy`.
- `unsearchable_count()` probes each row with a MATCH on its own leading
  whitespace-delimited token — because a row can keep its rowid while having
  empty index content, a rowid join would report a corrupt store as healthy.
- `compact(days)` truncates values in place: `substr(value, 1, 200) || '…'`,
  only where `updated <= cutoff` and `kind != 'summary'` and `length > 200`.
  Keys, rows, and index entries all survive.

## Why the triggers matter

v0.1.0 shipped an `AFTER UPDATE` trigger that deleted from the FTS index but
never re-inserted. Since `recall()` bumps `access_count` on every hit, **any
memory you had ever searched became permanently unsearchable**:

```python
s.remember("k", "token")
s.recall("token")   # hit
s.recall("token")   # gone
```

The current trigger re-writes the row. Regression tests
(`test_reading_does_not_break_search`, `test_every_row_stays_indexed_after_reads`)
pin this. `test_detects_content_loss_even_when_rowids_look_healthy` covers the
rowid-count blind spot; note that FTS5's own `integrity-check` passes silently on
this corruption, which is why the check is a real MATCH probe.

## What this is not

- **Not an agent memory framework.** No LLM calls, no summarization, no
  embeddings, no recall-and-inject loop, no decay, no salience scoring. It is a
  keyed table with full-text search. Whatever decides *what* to store is your
  job.
- **Not vector search.** FTS5 trigram only — substring matching over 3-character
  windows. No semantic similarity; `"简洁"` does not find `"简洁直接"` as a
  concept, only as a literal substring.
- **Queries under 3 characters are rejected**, including CJK
  (`cm search "雅思"` errors). Substring matching also means a query can match
  mid-word.
- **No FTS5 query syntax is exposed.** Tokens are quoted literals, so `AND`,
  `OR`, `NEAR`, `*`, and column filters are inert — you cannot filter by `kind`
  or boost by `access_count`.
- **`cm profile` reads a file nothing writes.** It parses
  `~/.codex-memory/profile.json` and expects `name`, `github`, `tech_areas`,
  `style`, `projects`, `ai_roles`, `session_key`. No code in this repo creates
  that file, so out of the box it always prints
  `(无用户画像, 用 cm add 开始记忆)`. It is also a hardcoded personal profile
  shape, not a general feature.
- **`cm` always writes to your home directory.** No `--db`, no `--path`. Do not
  run it in an environment where you do not want `~/.codex-memory/` created.
- **No concurrency control.** Plain `sqlite3.connect` in default mode, no WAL,
  no `busy_timeout`, no locking strategy. Concurrent writers will hit
  `database is locked`.
- **`compact` is irreversible truncation, and its counter is easy to misread.**
  It returns `rowcount` from one `UPDATE`, and it also overwrites `updated` with
  the current time — so compacted rows look freshly modified to the next
  `compact` call.
- **No migrations framework.** Schema changes rely on `CREATE ... IF NOT
  EXISTS` plus a manual trigger check. There is no version table.
- **Not published to PyPI** — install from a clone. There is no `.github`
  workflow in this repo; the previous README claimed CI across Python
  3.10–3.13, which does not exist here. Run the tests yourself.

## Requirements

Python 3.10+, SQLite with FTS5 enabled (CPython's bundled build has it). No
runtime dependencies. `pip install -e ".[test]"` adds pytest for the suite.

## License

MIT