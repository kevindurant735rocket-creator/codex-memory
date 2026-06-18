import sys, argparse, json
from pathlib import Path
from .store import MemoryStore

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
    sub.add_parser("compact", help="压缩旧记录")

    f = sub.add_parser("forget", help="删除一条记录")
    f.add_argument("key")

    pr = sub.add_parser("profile", help="显示用户画像 (跨会话记忆)")
    pr.add_argument("--raw", action="store_true", help="输出原始 JSON")

    args = p.parse_args()
    if not args.cmd: p.print_help(); return

    if args.cmd == "add":
        store.remember(args.key, args.value, args.kind)
        print(f"✓ 记住: {args.key}")

    elif args.cmd == "search":
        results = store.recall(args.query, args.limit)
        if not results: print("(无匹配)")
        for r in results:
            print(f"  [{r['kind']}] {r['key']} = {r['value'][:120]}  (hits:{r['hits']})")

    elif args.cmd == "stats":
        s = store.stats(); print(f"条目: {s['entries']}, 大小: {s['bytes']} 字节")

    elif args.cmd == "compact":
        print(f"压缩了 {store.compact()} 条旧记录")

    elif args.cmd == "forget":
        store.forget(args.key); print(f"✓ 已忘记: {args.key}")

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
