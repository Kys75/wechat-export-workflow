import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo


def load_config(filename):
    path = Path(filename).expanduser().resolve()
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for name in ("source", "keys", "decrypted", "exports"):
        value = Path(cfg[name]).expanduser()
        cfg[name] = (path.parent / value).resolve() if not value.is_absolute() else value.resolve()
    cfg["timezone"] = ZoneInfo(cfg.get("timezone", "Asia/Shanghai"))
    if cfg.get("attachments"):
        value = Path(cfg["attachments"]).expanduser()
        cfg["attachments"] = (path.parent / value).resolve() if not value.is_absolute() else value.resolve()
    cfg.setdefault("database_globs", ["contact/contact.db", "message/message_*.db", "session/session.db"])
    cfg.setdefault("sqlcipher", "sqlcipher")
    for name in ("decrypted", "exports", "keys"):
        if overlaps(cfg["source"], cfg[name]):
            raise ValueError("Source and output paths must not overlap in either direction")
    for name in ("exports", "keys"):
        if overlaps(cfg["decrypted"], cfg[name]):
            raise ValueError("Keys and exports must be separate from the replaceable decrypted directory")
    return cfg


def database_files(cfg):
    patterns = cfg.get("database_globs", ["contact/contact.db", "message/message_*.db", "session/session.db"])
    if not patterns or any(Path(p).is_absolute() or ".." in Path(p).parts for p in patterns):
        raise ValueError("database_globs must be nonempty relative safe patterns")
    return sorted({p for pattern in patterns for p in cfg["source"].glob(pattern) if p.is_file()})


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def overlaps(left, right):
    left, right = Path(left).resolve(), Path(right).resolve()
    return left == right or left in right.parents or right in left.parents


def write_private(path, text):
    path = Path(path)
    private_dir(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
