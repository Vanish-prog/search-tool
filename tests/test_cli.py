import json
from pathlib import Path
from search_tool.cli import main


def test_no_stops_without_loading_model_or_creating_index(tmp_path, capsys):
    root = tmp_path / 'files'
    root.mkdir()
    (root / 'note.txt').write_text('antenna')
    cache = tmp_path / 'cache'
    def forbidden():
        raise AssertionError('model must not load')
    code = main([str(root), 'antenna', '--cache-dir', str(cache)], engine_factory=forbidden, confirm=lambda _: False)
    assert code == 2
    assert not cache.exists()
    assert 'cancel' in capsys.readouterr().err.lower()


class TinyEngine:
    identity = 'test-only-not-a-production-model'
    def embed(self, chunks, query=False):
        import numpy as np
        text = ' '.join(c.value for c in chunks)
        return np.array([float('antenna' in text.lower()), 1.0])
    def close(self):
        pass


def test_yes_indexes_searches_and_reuses_unchanged_files(tmp_path, capsys):
    root = tmp_path / 'files'
    root.mkdir()
    note = root / 'rf.txt'
    note.write_text('antenna calibration')
    (root / 'food.txt').write_text('pasta recipes')
    cache = tmp_path / 'cache'
    argv = [str(root), 'antenna', '--cache-dir', str(cache), '--json']
    prompts = []
    def yes(message):
        prompts.append(message)
        return True
    assert main(argv, engine_factory=TinyEngine, confirm=yes) == 0
    output = json.loads(capsys.readouterr().out)
    assert Path(output[0]['path']) == note
    assert len(prompts) == 1
    db = next((cache / 'indexes').glob('*.sqlite3'))
    before = db.stat().st_mtime_ns
    assert main(argv, engine_factory=TinyEngine, confirm=lambda _: (_ for _ in ()).throw(AssertionError('must not ask'))) == 0
    assert db.stat().st_mtime_ns == before
    capsys.readouterr()
    note.write_text('pasta and cheese')
    assert main(argv, engine_factory=TinyEngine, confirm=yes) == 0
    assert len(prompts) == 2
    capsys.readouterr()
    note.unlink()
    assert main(argv, engine_factory=TinyEngine, confirm=yes) == 0
    output = json.loads(capsys.readouterr().out)
    assert len(output) == 1
    assert Path(output[0]['path']).name == 'food.txt'


def test_duplicate_and_renamed_content_reuses_embeddings(tmp_path, capsys):
    root = tmp_path / 'files'
    root.mkdir()
    (root / 'a.txt').write_text('antenna calibration')
    (root / 'b.txt').write_text('antenna calibration')
    cache = tmp_path / 'cache'
    documents = []

    class CountingEngine(TinyEngine):
        def embed(self, chunks, query=False):
            if not query:
                documents.append(chunks[0].value)
            return super().embed(chunks, query=query)

    argv = [str(root), 'antenna', '--cache-dir', str(cache), '--json']
    assert main(argv, engine_factory=CountingEngine, confirm=lambda _: True) == 0
    hits = json.loads(capsys.readouterr().out)
    assert len(hits) == 2
    assert documents == ['antenna calibration']
    (root / 'a.txt').rename(root / 'renamed.txt')
    assert main(argv, engine_factory=CountingEngine, confirm=lambda _: True) == 0
    hits = json.loads(capsys.readouterr().out)
    assert {Path(h['path']).name for h in hits} == {'b.txt', 'renamed.txt'}
    assert documents == ['antenna calibration']
    (root / 'renamed.txt').write_text('different contents')
    assert main(argv, engine_factory=CountingEngine, confirm=lambda _: True) == 0
    capsys.readouterr()
    assert documents == ['antenna calibration', 'different contents']


def test_bad_query_and_reference_fail_before_model_loading(tmp_path, capsys):
    root = tmp_path / 'files'
    root.mkdir()
    def forbidden():
        raise AssertionError('must not load model')
    assert main([str(root)], engine_factory=forbidden) == 1
    assert main([str(root), 'hello', '-r', str(tmp_path / 'missing')], engine_factory=forbidden) == 1
    assert 'missing' in capsys.readouterr().err


def test_rebuild_no_preserves_existing_index(tmp_path, capsys):
    root = tmp_path / 'files'
    root.mkdir()
    (root / 'a.txt').write_text('antenna')
    cache = tmp_path / 'cache'
    argv = [str(root), 'antenna', '--cache-dir', str(cache), '--json']
    assert main(argv, engine_factory=TinyEngine, confirm=lambda _: True) == 0
    capsys.readouterr()
    db = next((cache / 'indexes').glob('*.sqlite3'))
    before = db.read_bytes()
    assert main(argv + ['--rebuild'], engine_factory=TinyEngine, confirm=lambda _: False) == 2
    assert db.read_bytes() == before


def test_concurrent_search_refuses_locked_index(tmp_path, capsys):
    import fcntl
    import hashlib
    root = tmp_path / 'files'
    root.mkdir()
    cache = tmp_path / 'cache'
    lockpath = cache / 'locks' / (hashlib.sha256(bytes(str(root), 'utf8')).hexdigest() + '.lock')
    lockpath.parent.mkdir(parents=True)
    with lockpath.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main([str(root), 'query', '--cache-dir', str(cache)], engine_factory=lambda: (_ for _ in ()).throw(AssertionError('locked'))) == 1
    assert 'already' in capsys.readouterr().err


def test_cache_inside_directory_rejected(tmp_path, capsys):
    root = tmp_path / 'files'
    root.mkdir()
    assert main([str(root), 'hi', '--cache-dir', str(root / 'cache')], engine_factory=TinyEngine) == 1
    assert 'outside' in capsys.readouterr().err
