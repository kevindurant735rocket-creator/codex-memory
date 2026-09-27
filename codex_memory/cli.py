import sys, argparse, json
from pathlib import Path
from .store import MemoryStore, QueryError

PROFILE_PATH = Path.home() / ".codex-memory" / "profile.json"

def main():
    store = MemoryStore()
    p = argparse.ArgumentParser(prog="cm")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="记住一条信息")
    a.add_argument("key"); a.add_argument("value"); a.add_argument("--kind", default="fact")

    s = sub.add_parser("search", help="搜索记忆")
    s.add_argument("query"); s.add_argument("--limit", type=int, default=5)

    sub.add_parser("stats", help="存储统计")

    c = sub.add_parser("compact", help="压缩旧记录 (就地截断, 不删除)")
    c.add_argument("--days", type=int, default=30, help="压缩 N 天未更新的记录 (默认 30)")

    f = sub.add_parser("forget", help="删除一条记录")
    f.add_argument("key")

    g = sub.add_parser("get", help="按键精确读取 (支持含 / 的键)")
    g.add_argument("key")

    sub.add_parser("doctor", help="检查并修复索引 (重建 FTS)")

    pr = sub.add_parser("profile", help="显示用户画像 (跨会话记忆)")
    pr.add_argument("--raw", action="store_true", help="输出原始 JSON")

    args = p.parse_args()
    if not args.cmd: p.print_help(); return

    if args.cmd == "add":
        store.remember(args.key, args.value, args.kind)
        print(f"✓ 记住: {args.key}")

    elif args.cmd == "search":
        try:
            results = store.recall(args.query, args.limit)
        except QueryError as e:
            print(f"✗ 搜索失败: {e}"); return
        if not results: print("(无匹配)")
        for r in results:
            print(f"  [{r['kind']}] {r['key']} = {r['value'][:120]}  (hits:{r['hits']})")

    elif args.cmd == "get":
        r = store.get(args.key)
        print("(未找到)" if r is None
              else f"[{r['kind']}] {r['key']} = {r['value']}  (hits:{r['hits']})")

    elif args.cmd == "stats":
        s = store.stats()
        print(f"条目: {s['entries']}, 大小: {s['bytes']} 字节")
        if s["unindexed"]:
            print(f"⚠️  {s['unindexed']} 条未进入索引, 运行 cm doctor 修复")

    elif args.cmd == "compact":
        print(f"压缩了 {store.compact(args.days)} 条旧记录 (就地截断, 未删除)")

    elif args.cmd == "doctor":
        broken = store.unsearchable_count()
        if broken:
            store.rebuild_index()
            print(f"✓ 修复了 {broken} 条未索引记录, 索引已重建")
        else:
            print("✓ 索引健康, 无需修复")

    elif args.cmd == "forget":
        print("✓ 已忘记" if store.forget(args.key) else f"(未找到: {args.key})")

    elif args.cmd == "profile":
        if PROFILE_PATH.exists():
            data = json.loads(PROFILE_PATH.read_text())
            if args.raw:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(f"👤 {data['name']}")
                print(f"   GitHub: {data['github']}")
                print(f"   技术方向: {', '.join(data['tech_areas'])}")
                print(f"   风格: {data['style']}")
                print(f"   项目: {', '.join(data['projects'])}")
                print(f"   AI分工已定义: {len(data['ai_roles'])} 个角色")
                print(f"   最后更新: {data['session_key']}")
        else:
            print("(无用户画像, 用 cm add 开始记忆)")

if __name__ == "__main__": main()
