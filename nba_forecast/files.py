"""Atomic file writes: a reader sees the previous file or the new one, never a partial one."""
import hashlib
import json
import os
from pathlib import Path
import threading


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def atomic_bytes(path, raw):
    """Write ``raw`` through a uniquely named temporary file beside ``path`` and rename it into place.

    A crash or a full disk leaves the previous file, or none, never a truncated
    one, and concurrent writers of the same path cannot interleave.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_json(path, value):
    atomic_bytes(path, canonical(value))


def archive_bytes(path, raw):
    """Store content-addressed bytes at ``path``, whose name carries their SHA-256.

    A file already there with different bytes can only be a damaged earlier
    write (a genuine digest collision is reported, never repaired over), so it
    is replaced. Returns ``'written'``, ``'unchanged'`` or ``'repaired'``.
    """
    path = Path(path)
    state = 'written'
    if path.exists():
        existing = path.read_bytes()
        if existing == raw:
            return 'unchanged'
        if hashlib.sha256(existing).hexdigest() == hashlib.sha256(raw).hexdigest():
            raise ValueError('Archive digest collision')
        state = 'repaired'
    atomic_bytes(path, raw)
    return state
