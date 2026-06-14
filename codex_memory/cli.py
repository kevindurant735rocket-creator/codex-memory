import sys, argparse
from .store import MemoryStore

def main():
    store = MemoryStore()
    p = argparse.ArgumentParser(prog="cm")
    sub = p.add_subparsers(dest="cmd")
    a = sub.add_parser("add", help="记住一条信息")
    a.add_argument("key"); a.add_argument("value"); a.add_argument("--kind", default="fact")
    s = sub.add_parser("search", help="搜索记忆")
    s.add_argument("query"); s.add_argument("--limit", type=int, default=5)
    sub.add_parser("stats", help="存储统计")
    sub.add_parser("compact", help="压缩旧记录")
    f = sub.add_parser("forget", help="删除一条记录")
    f.add_argument("key")
    args = p.parse_args()
    if not args.cmd: p.print_help(); return
    if args.cmd == "add":
        store.remember(args.key, args.value, args.kind)
        print(f"✓ 记住: {args.key}")
    elif args.cmd == "search":
        for r in store.recall(args.query, args.limit) or []:
            print(f"  [{r['kind']}] {r['key']} = {r['value'][:120]}  (hits:{r['hits']})")
    elif args.cmd == "stats":
        s = store.stats(); print(f"条目: {s['entries']}, 大小: {s['bytes']} 字节")
    elif args.cmd == "compact":
        print(f"压缩了 {store.compact()} 条旧记录")
    elif args.cmd == "forget":
        store.forget(args.key); print(f"✓ 已忘记: {args.key}")

if __name__ == "__main__": main()
