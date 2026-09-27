# codex-memory

超轻量 AI 持久记忆系统 — SQLite + FTS5，零运行时依赖。

给 agent 用的跨会话记忆：写下就能记住，下次检索得回来。

```bash
pip install -e .
cm add "user/name" "Alaric"
cm add "pref/style" "简洁直接,重产出"
cm search "简洁直接"
cm get "user/name"          # 精确按键读取，支持含 / 的键
```

## 特性

- **零依赖**：只用 Python 标准库，SQLite 走 FTS5
- **CJK 友好**：trigram 分词，中文按字切三字窗口
- **键可读**：键名就是检索词，`user/name` 这类带斜杠的键照常可搜
- **自动迁移**：旧版本数据库打开时自动修复索引，无需手动干预
- **可验证**：`stats()` 报告真实可检索条数，不粉饰

## 命令

| 命令 | 说明 |
|---|---|
| `cm add KEY VALUE [--kind K]` | 写入或更新一条记忆 |
| `cm search QUERY [--limit N]` | 全文检索（trigram，最少 3 字符） |
| `cm get KEY` | 按键精确读取，绕过 FTS |
| `cm stats` | 条目数、字节数、索引健康度 |
| `cm doctor` | 检查并重建 FTS 索引 |
| `cm compact [--days N]` | 就地截断旧记录（不删除） |
| `cm forget KEY` | 删除一条记忆 |
| `cm profile [--raw]` | 显示用户画像 |

## v0.2.0 修复的问题

v0.1.0 有一个会造成**静默数据丢失**的缺陷，以及若干 API 正确性问题。全部有回归测试覆盖（`tests/test_store.py`，22 项）。

**1. 读一次就再也搜不到（严重）**

`memories` 表的 `AFTER UPDATE` 触发器只删除 FTS 索引条目、不重新写入。而 `recall()` 每次命中都会 `UPDATE access_count` —— 于是**任何被检索过的记忆，都会立刻从搜索索引中永久消失**：

```python
s.remember("k", "token")
s.recall("token")      # 命中
s.recall("token")      # 再次检索 -> 找不到
```

触发器补上重写语句即可。**旧数据库会在打开时自动修复**。

**2. 索引内容已丢失但行数看起来正常**

被旧触发器清空的行仍保留 rowid，所以按 rowid 统计"索引健康"会误报正常。`stats()` 的 `unindexed` 与 `unsearchable_count()` 按**实际能否匹配**来判断；检测到损坏即重建索引。

注意：FTS5 自带的 `integrity-check` 在这种损坏下**会静默通过**，不能作为检测手段。

**3. `compact()` 会删掉数据**

原实现对每条旧记录先 `DELETE` 再以 `<key>:summary` 重新插入：原键丢失、条目数不变、返回值虚假。现在改为**就地截断**到 200 字符，键与行都保留，索引不受影响。

**4. 检索词直接进 FTS5 语法**

`recall("user/name")` 会抛 `fts5: syntax error near "/"` —— 而斜杠正是本项目的标准键格式。现在每个词元被引号包裹，特殊字符一律按字面处理。

**5. 其余**

- `MemoryStore("路径")` 传字符串直接崩溃（`AttributeError`）
- `recall()` 返回自增**前**的 `hits`，永远差 1
- `forget()` 删不存在的键也返回 `True`（假成功）
- 缺少 `close()` / 上下文管理器，连接不释放
- `compact()` 边界用 `<` 而非 `<=`，同秒写入的记录永远压缩不到

## 已知限制

- trigram 分词**无法检索 2 字符及以下**的查询（中文同理，`雅思` 不可搜，`雅思7分` 可以）。现在会明确报错，而不是静默返回空。
- 检索词按空格切分后整体匹配，不支持 `AND` / `NEAR` 等 FTS5 高级语法。

## 开发

```bash
pip install -e ".[test]"
python -m pytest tests -q
```

CI 覆盖 Python 3.10 / 3.11 / 3.12 / 3.13。

## License

MIT
