import asyncio
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import zstandard

from wechat_export.config import load_config
from wechat_export.keys import candidates, verify
from wechat_export.refresh import refresh, read_keys
from wechat_export.store import Store, date_bound, table_name


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = {"source": self.root / "db_storage", "keys": self.root / "keys.json",
                    "decrypted": self.root / "decrypted", "exports": self.root / "exports",
                    "sqlcipher": "sqlcipher", "timezone": ZoneInfo("Asia/Shanghai")}
        self.chat = "demo-room@chatroom"
        self.stamp = int(datetime(2025, 1, 2, tzinfo=self.cfg["timezone"]).timestamp())
        for i in range(2):
            path = self.cfg["decrypted"] / "message" / f"message_{i}.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE Name2Id(user_name TEXT)")
                db.executemany("INSERT INTO Name2Id VALUES (?)", [(self.chat,), ("demo-author",)])
                db.execute(f"CREATE TABLE [{table_name(self.chat)}](local_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content BLOB)")
                content = "ordinary text" if i == 0 else "compressed needle"
                blob = zstandard.ZstdCompressor().compress(content.encode())
                db.execute(f"INSERT INTO [{table_name(self.chat)}] VALUES (?,?,?,?,?)", (i, 1, self.stamp + i, 2, blob))
        contact = self.cfg["decrypted"] / "contact" / "contact.db"
        contact.parent.mkdir()
        with sqlite3.connect(contact) as db:
            db.execute("CREATE TABLE contact(username TEXT,remark TEXT,nick_name TEXT)")
            db.executemany("INSERT INTO contact VALUES (?,?,?)", [(self.chat, "Demo Room", ""), ("demo-author", "Demo Author", "")])
        (self.cfg["decrypted"] / ".wechat-export-managed").write_text("wechat-export-v1\n")

    def tearDown(self):
        self.tmp.cleanup()

    def add_message(self, kind, content, stamp=None):
        with sqlite3.connect(self.cfg["decrypted"] / "message/message_1.db") as db:
            db.execute(f"INSERT INTO [{table_name(self.chat)}] VALUES (?,?,?,?,?)", (3, kind, stamp if stamp is not None else self.stamp + 2, 2, content))

    def test_cross_shard_search_and_export(self):
        store = Store(self.cfg)
        self.assertEqual(store.chats()[0]["count"], 2)
        self.assertEqual(len(store.search("needle")), 1)
        result = store.export("Demo Room", "2025-01-02", "2025-01-02")
        self.assertEqual(result["count"], 2)
        self.assertIn("Demo Author", Path(result["markdown"]).read_text())
        self.assertEqual(Path(result["json"]).stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(result["json"], store.export(self.chat)["json"])

    def test_reply_and_original_xml_preserved(self):
        xml = "<msg><appmsg><title>reply body</title><refermsg><displayname>Quoted Author</displayname><content>quoted words</content></refermsg></appmsg></msg>"
        self.add_message((57 << 32) | 49, xml)
        record = Store(self.cfg).messages(self.chat)[-1]
        self.assertEqual(record["sender"], "Demo Author")
        self.assertIn("Quoted Author", record["content"])
        self.assertEqual(record["original_content"], xml)

    def test_inclusive_day_and_exact_timestamp(self):
        self.add_message(1, "following day", self.stamp + 86400)
        store = Store(self.cfg)
        self.assertEqual(len(store.messages(self.chat, "2025-01-02", "2025-01-02")), 2)
        self.assertEqual(len(store.messages(self.chat, end="2025-01-02 00:00:00")), 1)
        self.assertEqual(len(store.messages(self.chat, limit=1)), 1)
        with self.assertRaises(ValueError):
            store.messages(self.chat, "2025-01-04", "2025-01-02")

    def test_unknown_chat_and_bad_limit_fail(self):
        store = Store(self.cfg)
        with self.assertRaises(ValueError):
            store.resolve("missing")
        with self.assertRaises(ValueError):
            store.search("text", 0)

    def test_attachment_collision_is_explicit(self):
        self.cfg["attachments"] = self.root / "attachments"
        for folder in ("one", "two"):
            parent = self.cfg["attachments"] / folder
            parent.mkdir(parents=True)
            (parent / "demo.txt").write_text("synthetic")
        self.add_message((6 << 32) | 49, "<msg><appmsg><title>demo.txt</title></appmsg></msg>")
        record = Store(self.cfg).messages(self.chat)[-1]
        self.assertEqual(len(record["attachment_candidates"]), 2)
        self.assertIn("ambiguous", record["content"])

    def test_failed_refresh_keeps_previous_snapshot(self):
        self.cfg["source"].mkdir()
        path = self.cfg["source"] / "message/message_0.db"
        path.parent.mkdir()
        path.write_bytes(b"not an encrypted database")
        self.cfg["keys"].write_text(json.dumps({"message/message_0.db": "11" * 32}))
        with patch("wechat_export.refresh.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "secret should never appear")):
            with self.assertRaisesRegex(RuntimeError, "Decryption failed"):
                refresh(self.cfg)
        self.assertEqual(len(Store(self.cfg).messages(self.chat)), 2)

    def test_key_path_traversal_rejected(self):
        self.cfg["keys"].write_text(json.dumps({"../escape.db": "11" * 32}))
        with self.assertRaises(ValueError):
            read_keys(self.cfg["keys"])

    def test_output_ancestor_cannot_replace_source(self):
        self.cfg["decrypted"] = self.root
        with self.assertRaisesRegex(ValueError, "overlap"):
            refresh(self.cfg)
        self.assertTrue(self.root.is_dir())

    def test_unmanaged_output_cannot_be_replaced(self):
        (self.cfg["decrypted"] / ".wechat-export-managed").unlink()
        with self.assertRaisesRegex(ValueError, "not managed"):
            refresh(self.cfg)
        self.assertEqual(len(Store(self.cfg).messages(self.chat)), 2)

    def test_cli_end_to_end(self):
        cfg = {k: str(v) for k, v in self.cfg.items()}
        config = self.root / "config.json"
        config.write_text(json.dumps(cfg))
        output = subprocess.run([sys.executable, "-m", "wechat_export", "--config", str(config), "export", "--all"], capture_output=True, text=True)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertEqual(json.loads(output.stdout)[0]["count"], 2)

    def test_mcp_actual_client(self):
        from fastmcp import Client
        from wechat_export.mcp_server import build_server
        async def run():
            async with Client(build_server(self.cfg)) as client:
                names = {tool.name for tool in await client.list_tools()}
                self.assertEqual(names, {"list_chats", "search_messages", "refresh", "export_conversation"})
                result = await client.call_tool("export_conversation", {"chat": self.chat})
                self.assertFalse(result.is_error)
                self.assertIn('"count":2', result.content[0].text.replace(" ", ""))
        asyncio.run(run())

    def test_mcp_stdio_transport(self):
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        config = self.root / "stdio-config.json"
        config.write_text(json.dumps({k: str(v) for k, v in self.cfg.items()}))
        transport = StdioTransport(command=sys.executable,
            args=["-m", "wechat_export.mcp_server", "--config", str(config)])
        async def run():
            async with Client(transport) as client:
                result = await client.call_tool("search_messages", {"keyword": "needle"})
                self.assertFalse(result.is_error)
                self.assertIn("compressed needle", result.content[0].text)
        asyncio.run(run())

    @unittest.skipUnless(shutil.which("sqlcipher"), "SQLCipher CLI is optional for synthetic suite")
    def test_real_sqlcipher_roundtrip(self):
        version = subprocess.run(["sqlcipher", "--version"], capture_output=True, text=True)
        self.cfg["source"].mkdir()
        source = self.cfg["decrypted"] / "message/message_0.db"
        encrypted = self.cfg["source"] / "message/message_0.db"
        encrypted.parent.mkdir()
        key = "37" * 32
        sql = f"ATTACH DATABASE '{encrypted}' AS enc KEY \"x'{key}'\"; SELECT sqlcipher_export('enc'); DETACH DATABASE enc;"
        encrypted_run = subprocess.run(["sqlcipher", str(source)], input=sql, text=True, capture_output=True)
        self.assertEqual(encrypted_run.returncode, 0, encrypted_run.stderr)
        self.cfg["keys"].write_text(json.dumps({"message/message_0.db": key}))
        self.assertEqual(refresh(self.cfg), {"refreshed": 1})
        self.assertEqual(Store(self.cfg).export(self.chat)["count"], 1)


class KeyTest(unittest.TestCase):
    def test_ascii_and_raw_candidate_verification(self):
        key, salt = bytes(range(32)), bytes(range(16))
        page = salt + bytes((i % 251 for i in range(4016)))
        mac = hashlib.pbkdf2_hmac("sha512", key, bytes(b ^ 0x3A for b in salt), 2, 32)
        page += hmac.new(mac, page[16:] + struct.pack("<I", 1), hashlib.sha512).digest()
        self.assertTrue(verify(key, page))
        self.assertFalse(verify(bytes(32), page))
        self.assertEqual(candidates(b"x'" + key.hex().encode() + salt.hex().encode() + b"'", page), key)
        self.assertEqual(candidates(b"p" * 40 + key + b"p" * 5 + salt, page, raw=True, window=64), key)


if __name__ == "__main__":
    unittest.main()
