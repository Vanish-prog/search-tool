"""Filesystem inventory and index synchronization."""
import os
import sqlite3
import numpy as np
from pathlib import Path

IGNORE = {'.git', '.venv', '__pycache__', 'node_modules', '.findmatch'}


def scan(root: Path) -> dict:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f'Not a directory: {root}')
    found = {}
    def error(exc):
        raise exc
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=error):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            found[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return found


def plan(found: dict, stored: dict):
    return (sorted(p for p, stamp in found.items() if stored.get(p) != stamp),
            sorted(set(stored) - set(found)))


class Index:
    def __init__(self, path, identity):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta (identity TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, ctime INTEGER);
            CREATE TABLE IF NOT EXISTS chunks (path TEXT REFERENCES files(path) ON DELETE CASCADE,
                kind TEXT, location TEXT, snippet TEXT, vector BLOB);
        ''')
        row = self.db.execute('SELECT identity FROM meta').fetchone()
        if row and row[0] != identity:
            self.db.close()
            raise ValueError('Index uses a different model or extraction version; use --rebuild.')
        if not row:
            self.db.execute('INSERT INTO meta VALUES (?)', (identity,))
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def inventory(self):
        return {row[0]: tuple(row[1:]) for row in self.db.execute('SELECT path,size,mtime,ctime FROM files')}

    def replace(self, path, stamp, chunks):
        with self.db:
            self.db.execute('DELETE FROM files WHERE path=?', (path,))
            self.db.execute('INSERT INTO files VALUES (?,?,?,?)', (path, *stamp))
            for kind, location, snippet, vector in chunks:
                v = np.asarray(vector, dtype='<f4')
                if v.ndim != 1 or not np.isfinite(v).all() or not np.linalg.norm(v):
                    raise ValueError('Invalid embedding')
                v = v / np.linalg.norm(v)
                self.db.execute('INSERT INTO chunks VALUES (?,?,?,?,?)',
                                (path, kind, location, snippet, v.astype('<f4').tobytes()))

    def remove(self, paths):
        with self.db:
            self.db.executemany('DELETE FROM files WHERE path=?', [(p,) for p in paths])

    def search(self, vector, limit):
        q = np.asarray(vector, dtype=np.float32)
        if q.ndim != 1 or not np.isfinite(q).all() or not np.linalg.norm(q):
            raise ValueError('Invalid query embedding')
        q /= np.linalg.norm(q)
        best = {}
        for path, kind, location, snippet, blob in self.db.execute('SELECT * FROM chunks'):
            v = np.frombuffer(blob, dtype='<f4')
            if v.shape != q.shape:
                raise ValueError('Embedding dimension mismatch; rebuild the index')
            score = float(np.dot(v, q))
            if path not in best or score > best[path]['score']:
                best[path] = dict(path=path, kind=kind, location=location, snippet=snippet, score=score)
        return sorted(best.values(), key=lambda h: (-h['score'], h['path']))[:limit]
