import argparse
import json
import sys

from .config import load_config
from .refresh import refresh
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Local WeChat export; no network services")
    parser.add_argument("--config", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("refresh")
    scan = commands.add_parser("extract-keys")
    scan.add_argument("--pid", type=int, required=True)
    scan.add_argument("--raw", action="store_true", help="Also scan raw salt/key neighborhoods; slower")
    listing = commands.add_parser("list")
    listing.add_argument("--keyword", default="")
    search = commands.add_parser("search")
    search.add_argument("keyword")
    search.add_argument("--limit", type=int, default=30)
    export = commands.add_parser("export")
    choose = export.add_mutually_exclusive_group(required=True)
    choose.add_argument("--chat")
    choose.add_argument("--all", action="store_true")
    export.add_argument("--start", default="")
    export.add_argument("--end", default="")
    export.add_argument("--limit", type=int, default=0)
    export.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        if args.command == "doctor":
            import shutil
            result = {"source_exists": cfg["source"].is_dir(), "keys_exist": cfg["keys"].is_file(),
                      "decrypted_exists": cfg["decrypted"].is_dir(),
                      "sqlcipher_available": bool(shutil.which(cfg["sqlcipher"])),
                      "timezone": str(cfg["timezone"])}
        elif args.command == "extract-keys":
            from .keys import extract
            result = extract(cfg, args.pid, args.raw)
        elif args.command == "refresh":
            result = refresh(cfg)
        else:
            if getattr(args, "refresh", False):
                refresh(cfg)
            store = Store(cfg)
            if args.command == "list":
                result = store.chats(args.keyword)
            elif args.command == "search":
                result = store.search(args.keyword, args.limit)
            else:
                targets = list(store.locations) if args.all else [args.chat]
                result = [store.export(chat, args.start, args.end, args.limit) for chat in targets]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
