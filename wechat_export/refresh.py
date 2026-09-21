import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from .config import private_dir, write_private, database_files, overlaps
from .store import connect


def read_keys(path):
    raw = json.loads(Path(path).read_text())
    keys = {name: key for name, key in raw.items() if not name.startswith("__")}
    if not keys:
        raise ValueError("Key file contains no database keys")
    for name, key in keys.items():
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts or rel.suffix != ".db":
            raise ValueError("Unsafe relative database path in key file")
        if not isinstance(key, str) or not re.fullmatch(r"[a-fA-F0-9]{64}(?:[a-fA-F0-9]{32})?", key):
            raise ValueError("Invalid raw SQLCipher key format")
    return keys


def signature(path):
    result = []
    for item in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        stat = item.stat() if item.exists() else None
        result.append((stat.st_size, stat.st_mtime_ns, stat.st_ino) if stat else None)
    return result


def refresh(cfg):
    cfg = dict(cfg)
    source = cfg["source"].resolve()
    cfg["source"] = source
    target = cfg["decrypted"]
    if any(overlaps(source, cfg[name]) for name in ("decrypted", "keys", "exports")):
        raise ValueError("Source and output paths must not overlap")
    if any(overlaps(target, cfg[name]) for name in ("keys", "exports")):
        raise ValueError("Keys and exports must not overlap the replaceable decrypted directory")
    marker = target / ".wechat-export-managed"
    if target.exists() and (not marker.is_file() or marker.read_text() != "wechat-export-v1\n"):
        raise ValueError("Existing decrypted directory is not managed by this tool; choose a new empty path")
    if not source.is_dir() or source.name != "db_storage":
        raise ValueError("source must be the chosen account's db_storage directory")
    keys = read_keys(cfg["keys"])
    files = database_files(cfg)
    if not files:
        raise ValueError("Source contains no databases")
    missing = [p for p in files if p.relative_to(source).as_posix() not in keys]
    if missing:
        raise ValueError(f"{len(missing)} databases lack keys; extract/import current keys first")
    private_dir(target.parent)
    lock = target.parent / ("." + target.name + ".refresh.lock")
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    backup = target.parent / ("." + target.name + ".previous-" + uuid.uuid4().hex)
    try:
        with tempfile.TemporaryDirectory(prefix=".wechat-refresh-", dir=target.parent) as workspace:
            staging = private_dir(Path(workspace) / "decrypted")
            write_private(staging / ".wechat-export-managed", "wechat-export-v1\n")
            snapshot = private_dir(Path(workspace) / "snapshot")
            before = {p: signature(p) for p in files}
            for src in files:
                if src.is_symlink() or source not in src.resolve().parents:
                    raise ValueError("Source database symlinks are not supported")
                rel = src.relative_to(source)
                copy = snapshot / rel
                private_dir(copy.parent)
                for suffix in ("", "-wal", "-shm"):
                    original = Path(str(src) + suffix)
                    if original.exists():
                        if original.is_symlink():
                            raise ValueError("Source sidecar symlinks are not supported")
                        shutil.copyfile(original, str(copy) + suffix)
                        os.chmod(str(copy) + suffix, 0o600)
            if files != database_files(cfg) or before != {p: signature(p) for p in files}:
                raise RuntimeError("Source changed during snapshot; quit WeChat and retry refresh")
            for src in files:
                rel = src.relative_to(source)
                out = staging / rel
                private_dir(out.parent)
                escaped = str(out).replace("'", "''")
                sql = (f"PRAGMA key=\"x'{keys[rel.as_posix()]}'\";\n"
                       "PRAGMA cipher_compatibility=4;\nPRAGMA cipher_page_size=4096;\n"
                       f"ATTACH DATABASE '{escaped}' AS clear KEY '';\n"
                       "SELECT sqlcipher_export('clear');\nDETACH DATABASE clear;\n")
                result = subprocess.run([cfg["sqlcipher"], "-batch", "-bail", str(snapshot / rel)],
                                        input=sql, text=True, capture_output=True, timeout=180)
                # Never return raw sqlcipher diagnostics: some builds echo SQL containing keys.
                if result.returncode or not out.exists():
                    raise RuntimeError("Decryption failed; check key, SQLCipher version and source snapshot")
                with connect(out) as db:
                    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise RuntimeError("Decrypted integrity check failed")
                os.chmod(out, 0o600)
            from datetime import datetime, timezone
            write_private(staging / "refresh.json", json.dumps({"completed_at": datetime.now(timezone.utc).isoformat(), "databases": len(files)}))
            if target.exists():
                target.rename(backup)
            try:
                staging.rename(target)
            except BaseException:
                if backup.exists():
                    backup.rename(target)
                raise
        if backup.exists():
            shutil.rmtree(backup)
        return {"refreshed": len(files)}
    finally:
        lock.unlink(missing_ok=True)
