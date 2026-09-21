import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo


def sudo_user():
    if os.geteuid() == 0 and os.environ.get("SUDO_UID", "").isdigit():
        import pwd
        account = pwd.getpwuid(int(os.environ["SUDO_UID"]))
        if account.pw_uid != 0:
            return account
    return None


def expand_user(value):
    value = str(value)
    if value == "~" or value.startswith("~/"):
        account = sudo_user()
        home = Path(account.pw_dir) if account else Path.home()
        return home / value[2:] if value != "~" else home
    return Path(value).expanduser()


def load_config(filename):
    path = expand_user(filename).resolve()
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for name in ("source", "keys", "decrypted", "exports"):
        value = expand_user(cfg[name])
        cfg[name] = (path.parent / value).resolve() if not value.is_absolute() else value.resolve()
    cfg["timezone"] = ZoneInfo(cfg.get("timezone", "Asia/Shanghai"))
    if cfg.get("attachments"):
        value = expand_user(cfg["attachments"])
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


def private_dir(path, owner=None):
    path = Path(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        if owner:
            os.chown(directory, owner[0], owner[1])
    return path


def overlaps(left, right):
    left, right = Path(left).resolve(), Path(right).resolve()
    return left == right or left in right.parents or right in left.parents


def write_private(path, text, owner=None):
    path = Path(path)
    private_dir(path.parent, owner=owner)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        if owner:
            os.fchown(handle.fileno(), owner[0], owner[1])
        handle.write(text)
