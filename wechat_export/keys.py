"""Explicit, local LLDB extraction; SQLCipher 4 page HMAC verifies every candidate."""
import hashlib
import hmac
import json
import os
import re
import struct
from pathlib import Path

from .config import write_private, database_files


def verify(key, page):
    if len(page) != 4096 or len(key) != 32:
        return False
    salt = bytes(byte ^ 0x3A for byte in page[:16])
    mac_key = hashlib.pbkdf2_hmac("sha512", key, salt, 2, 32)
    digest = hmac.new(mac_key, page[16:-64] + struct.pack("<I", 1), hashlib.sha512).digest()
    return hmac.compare_digest(digest, page[-64:])


def candidates(data, page, raw=False, window=8192):
    for match in re.finditer(rb"x'([0-9A-Fa-f]{64})(?:[0-9A-Fa-f]{32})?'", data):
        key = bytes.fromhex(match[1].decode())
        if verify(key, page):
            return key
    if raw:
        start = 0
        while True:
            pos = data.find(page[:16], start)
            if pos < 0:
                break
            for offset in range(max(0, pos - window), min(len(data) - 32, pos + window) + 1):
                key = data[offset:offset + 32]
                if key.count(0) <= 24 and verify(key, page):
                    return key
            start = pos + 1
    return None


def extract(cfg, pid, raw=False):
    cfg = dict(cfg)
    cfg["source"] = cfg["source"].resolve()
    if pid < 1:
        raise ValueError("Specify a positive WeChat process PID")
    if cfg["keys"].exists():
        raise ValueError("Key output already exists; choose a new key filename to avoid overwriting")
    try:
        import lldb
    except ImportError as exc:
        raise RuntimeError("LLDB Python module unavailable; see README for matching LLDB/Python setup") from exc
    pages = {}
    for path in database_files(cfg):
        if path.is_symlink():
            raise ValueError("Database symlinks are unsupported")
        with path.open("rb") as handle:
            page = handle.read(4096)
        if len(page) == 4096 and not page.startswith(b"SQLite format 3"):
            pages[path.relative_to(cfg["source"]).as_posix()] = page
    if not pages:
        raise ValueError("No encrypted database first pages found")
    debugger = lldb.SBDebugger.Create()
    process = None
    found = {}
    try:
        debugger.SetAsync(False)
        error = lldb.SBError()
        process = debugger.CreateTarget("").AttachToProcessWithID(debugger.GetListener(), pid, error)
        if error.Fail():
            raise RuntimeError("LLDB attach denied; see README. No system protections were changed")
        info, address = lldb.SBMemoryRegionInfo(), 0
        while process.GetMemoryRegionInfo(address, info).Success():
            end = info.GetRegionEnd()
            if end <= address:
                break
            if info.IsReadable():
                offset = info.GetRegionBase()
                tail = b""
                while offset < end and len(found) < len(pages):
                    size = min(1024 * 1024, end - offset)
                    data = process.ReadMemory(offset, size, error)
                    if error.Success() and data:
                        buf = tail + data
                        for name, page in pages.items():
                            if name not in found:
                                key = candidates(buf, page, raw)
                                if key:
                                    found[name] = key.hex()
                        tail = buf[-16416:]
                    else:
                        tail = b""
                    offset += size
            if len(found) == len(pages):
                break
            address = end
    finally:
        if process is not None and process.IsValid():
            process.Detach()
        lldb.SBDebugger.Destroy(debugger)
    if len(found) != len(pages):
        raise RuntimeError(f"Only {len(found)}/{len(pages)} keys verified; no partial key file saved")
    write_private(cfg["keys"], json.dumps(found, indent=2))
    return {"keys_saved": len(found)}
