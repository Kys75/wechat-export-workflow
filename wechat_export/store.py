import hashlib
import json
import re
import sqlite3
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, time, timedelta
from pathlib import Path

from .config import write_private


def connect(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def table_name(username):
    return "Msg_" + hashlib.md5(username.encode()).hexdigest()


def date_bound(value, timezone, end=False):
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if len(value) == 10 and end:
        dt = datetime.combine(dt.date() + timedelta(days=1), time())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone)
    return dt.timestamp()


def decode(raw):
    if raw is None:
        return None, ""
    data = raw.encode() if isinstance(raw, str) else bytes(raw)
    sender = None
    prefix = re.match(rb"^([A-Za-z0-9_@.-]+):\n", data)
    if prefix:
        sender = prefix[1].decode()
        data = data[prefix.end():]
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        import zstandard
        with zstandard.ZstdDecompressor().stream_reader(data) as reader:
            data = reader.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("Message exceeds decompression limit")
    return sender, data.decode("utf-8", errors="replace")


def render_content(kind, text):
    real, subtype = kind & 0xFFFFFFFF, kind >> 32
    if real == 1:
        return text
    if real == 49:
        try:
            node = ET.fromstring(text)
        except ET.ParseError:
            return "[app message: unreadable XML] " + text
        def tag(name):
            return node.findtext(".//" + name, default="")
        embedded_type = tag("type")
        subtype = subtype or (int(embedded_type) if embedded_type.isdigit() else 0)
        title = tag("title")
        if subtype == 57:
            return f"[reply to {tag('displayname')}: {tag('content')}] {title}"
        if subtype == 6:
            return f"[file metadata only] {title} ({tag('totallen')} bytes)"
        if subtype in (4, 5, 51, 63):
            return f"[link/share] {title} {tag('des')} {tag('url')}".strip()
        if subtype == 19:
            return f"[forwarded conversation] {title}"
        if subtype in (33, 36):
            return f"[mini app] {title}"
        return f"[app:{subtype}] {title}"
    label = {3: "image", 34: "voice", 42: "contact card", 43: "video",
             47: "sticker", 48: "location", 50: "call", 10000: "system",
             10002: "recall"}.get(real, f"type:{real}")
    if real in (10000, 10002):
        return f"[{label}] " + re.sub(r"<[^>]*>", "", text)
    return f"[{label}; media not extracted]"


class Store:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dbs = sorted((cfg["decrypted"] / "message").glob("message_*.db"))
        if not self.dbs:
            raise ValueError("No message databases; run refresh or configure decrypted")
        self.names = {}
        self.attachments = {}
        if cfg.get("attachments"):
            for path in cfg["attachments"].rglob("*"):
                if path.is_file() and not path.is_symlink():
                    self.attachments.setdefault(path.name, []).append(str(path))
        contact = cfg["decrypted"] / "contact" / "contact.db"
        if contact.is_file():
            with connect(contact) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in ("contact", "stranger"):
                    if table in tables:
                        for user, remark, nick in db.execute(f"SELECT username,remark,nick_name FROM {table}"):
                            self.names.setdefault(user, remark or nick or user)
        self.locations = {}
        for path in self.dbs:
            with connect(path) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "Name2Id" not in tables:
                    raise ValueError("Unsupported message schema: missing Name2Id")
                for (user,) in db.execute("SELECT user_name FROM Name2Id WHERE user_name != ''"):
                    if table_name(user) in tables:
                        self.locations.setdefault(user, []).append(path)

    def chats(self, keyword=""):
        result = []
        for user, paths in self.locations.items():
            name = self.names.get(user, user)
            if keyword.casefold() not in (name + " " + user).casefold():
                continue
            latest, count = 0, 0
            for path in paths:
                with connect(path) as db:
                    n, stamp = db.execute(f"SELECT count(*),max(create_time) FROM [{table_name(user)}]").fetchone()
                    count += n
                    latest = max(latest, stamp or 0)
            result.append({"username": user, "name": name, "count": count,
                           "latest": datetime.fromtimestamp(latest, self.cfg["timezone"]).isoformat() if latest else None})
        return sorted(result, key=lambda c: c["latest"] or "", reverse=True)

    def resolve(self, query):
        if query in self.locations:
            return query
        exact = [u for u in self.locations if self.names.get(u, u).casefold() == query.casefold()]
        candidates = exact or [c["username"] for c in self.chats(query)]
        if len(candidates) != 1:
            raise ValueError("Chat is missing or ambiguous; list chats and use an exact username")
        return candidates[0]

    def messages(self, user, start="", end="", limit=0):
        if limit < 0:
            raise ValueError("limit must be nonnegative")
        begin = date_bound(start, self.cfg["timezone"])
        finish = date_bound(end, self.cfg["timezone"], end=True)
        if begin is not None and finish is not None and begin > finish:
            raise ValueError("start must precede end")
        # Date-only end includes the whole day; explicit timestamps are inclusive.
        where, params = [], []
        if begin is not None:
            where.append("m.create_time >= ?")
            params.append(begin)
        if finish is not None:
            where.append("m.create_time " + ("<" if len(end) == 10 else "<=") + " ?")
            params.append(finish)
        records = []
        for path in self.locations[user]:
            with connect(path) as db:
                query = (f"SELECT m.local_id,m.local_type,m.create_time,m.message_content,n.user_name "
                         f"FROM [{table_name(user)}] m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id")
                if where:
                    query += " WHERE " + " AND ".join(where)
                for ident, kind, stamp, raw, sender in db.execute(query, params):
                    prefix, content = decode(raw)
                    sender = sender or prefix or "unknown"
                    body = render_content(kind, content)
                    files = []
                    if kind & 0xFFFFFFFF == 49:
                        try:
                            node = ET.fromstring(content)
                            title = node.findtext(".//title", default="")
                            if kind >> 32 == 6 or node.findtext(".//type") == "6":
                                files = self.attachments.get(title, [])
                                if len(files) == 1:
                                    body += " [local file] " + files[0]
                                elif files:
                                    body += f" [ambiguous: {len(files)} local files; see JSON candidates]"
                        except ET.ParseError:
                            pass
                    records.append({"id": ident, "shard": path.name, "timestamp": stamp,
                                    "sender": self.names.get(sender, sender), "sender_id": sender,
                                    "type": kind, "content": body, "original_content": content,
                                    "attachment_candidates": files})
        records.sort(key=lambda r: (r["timestamp"] or 0, r["shard"], r["id"]))
        return records[-limit:] if limit else records

    def search(self, keyword, limit=30):
        if not keyword or limit < 1:
            raise ValueError("keyword must be nonempty and limit positive")
        hits = []
        for user in self.locations:
            for row in self.messages(user):
                if keyword.casefold() in row["content"].casefold():
                    hits.append({"chat": user, **row})
        return sorted(hits, key=lambda r: r["timestamp"] or 0, reverse=True)[:limit]

    def export(self, chat, start="", end="", limit=0):
        user = self.resolve(chat)
        records = self.messages(user, start, end, limit)
        lines = ["# " + self.names.get(user, user), "", f"Messages: {len(records)}",
                 "Timezone: " + str(self.cfg["timezone"]), ""]
        for row in records:
            stamp = datetime.fromtimestamp(row["timestamp"], self.cfg["timezone"]).isoformat() if row["timestamp"] is not None else "unknown-time"
            lines += [f"## {stamp} | {row['sender']}", "", *["> " + line for line in row["content"].splitlines()], ""]
        stem = table_name(user) + "_" + uuid.uuid4().hex[:12]
        markdown = self.cfg["exports"] / (stem + ".md")
        raw_json = self.cfg["exports"] / (stem + ".json")
        write_private(markdown, "\n".join(lines))
        write_private(raw_json, json.dumps({"chat": user, "start": start, "end": end, "messages": records}, ensure_ascii=False, indent=2))
        return {"count": len(records), "markdown": str(markdown), "json": str(raw_json),
                "latest": records[-1]["timestamp"] if records else None}
