import argparse
import json

from .config import load_config
from .refresh import refresh as refresh_databases
from .store import Store


def build_server(cfg):
    from fastmcp import FastMCP
    mcp = FastMCP("wechat-export")

    @mcp.tool
    def list_chats(keyword: str = "") -> list[dict]:
        """Resolve chat identities and inspect the actual latest message timestamp."""
        return Store(cfg).chats(keyword)

    @mcp.tool
    def search_messages(keyword: str, limit: int = 30) -> list[dict]:
        """Search decoded text in every message shard; output contains private messages."""
        return Store(cfg).search(keyword, limit)

    @mcp.tool
    def refresh() -> dict:
        """Copy and decrypt current databases; failure leaves the last good snapshot intact."""
        return refresh_databases(cfg)

    @mcp.tool
    def export_conversation(chat: str, start: str = "", end: str = "", limit: int = 0,
                            refresh: bool = False, include_text: bool = False) -> dict:
        """Export locally. include_text explicitly sends up to 120000 characters to the client."""
        if refresh:
            refresh_databases(cfg)
        result = Store(cfg).export(chat, start, end, limit)
        if include_text:
            from pathlib import Path
            text = Path(result["markdown"]).read_text(encoding="utf-8")
            result.update(text=text[:120000], truncated=len(text) > 120000)
        return result

    return mcp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    build_server(load_config(args.config)).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
