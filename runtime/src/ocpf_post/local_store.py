"""Private atomic state with one writer; never reset corrupt evidence."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile

from ocpf_post.state import ensure_private_dir


def read(path):
    if not path.exists():
        return {}
    if path.stat().st_size > 50_000_000:
        raise ValueError('State exceeds bounded size')
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('Invalid local state')
    return value


@contextmanager
def locked(path):
    import fcntl
    ensure_private_dir(path.parent)
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


@contextmanager
def try_locked(path):
    """Try the same single-writer lock without turning normal contention into an exception."""
    import fcntl
    ensure_private_dir(path.parent)
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        os.close(fd)


def write(path, value):
    ensure_private_dir(path.parent)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)
