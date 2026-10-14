import json
import time
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


def pipeline_engine(sleeps, lock=None, record=None):
    """TinyEngine that records completion order, optionally sleeping."""
    import threading
    import time
    lock = lock or threading.Lock()

    class PipelineEngine(TinyEngine):
        def embed(self, chunks, query=False):
            if not query:
                time.sleep(sleeps)
                if record is not None:
                    with lock:
                        record.append(chunks[0].snippet)
                    return TinyEngine.embed(self, chunks, query=query)
            return TinyEngine.embed(self, chunks, query=query)

    return PipelineEngine


def fake_extractor(chunks_for, *, sleeps=0.0, scratch_marker=None, state=None):
    """Replacement for search_tool.extract.extract that records its own progress."""
    import threading
    import time
    from search_tool.extract import Chunk
    lock = state.setdefault('lock', threading.Lock()) if state is not None else threading.Lock()

    def extract(path, scratch):
        time.sleep(sleeps)
        if scratch_marker:
            (Path(scratch) / scratch_marker).write_text('scratch')
        if state is not None:
            with lock:
                state['produced'] = state.get('produced', 0) + 1
                state['peak'] = max(state.get('peak', 0), state['produced'] - state.get('consumed', 0))
        count = chunks_for(path)
        if count is None:
            raise ValueError(f'{path.name}: synthetic extraction failure')
        return [Chunk('text', f'{path.name}#{i}', f'line {i}', f'{path.name}#{i}') for i in range(1, count + 1)]

    return extract


def test_extraction_overlaps_inference_within_a_bounded_lookahead(tmp_path, monkeypatch, capsys):
    import search_tool.extract
    from search_tool.cli import EXTRACTION_LOOKAHEAD, main
    root = tmp_path / 'files'
    root.mkdir()
    for i in range(6):
        (root / f'file{i}.txt').write_text(f'antenna {i}')
    state = {}
    # Extraction far cheaper than inference, as with images: the producer must
    # fill its look-ahead and then block instead of running the whole library.
    monkeypatch.setattr(search_tool.extract, 'extract', fake_extractor(lambda _: 1, sleeps=0.01, state=state))
    completed = []

    class Engine(pipeline_engine(0.2, record=completed)):
        def embed(self, chunks, query=False):
            if not query:
                with state['lock']:
                    state['consumed'] = state.get('consumed', 0) + 1
            return super().embed(chunks, query=query)

    assert main([str(root), 'antenna', '--cache-dir', str(tmp_path / 'cache'), '--json'],
                engine_factory=Engine, confirm=lambda _: True) == 0
    capsys.readouterr()
    assert state['peak'] >= 2, 'inference must not wait for the next extraction to start'
    assert state['peak'] <= EXTRACTION_LOOKAHEAD + 1, f"look-ahead must stay bounded, peaked at {state['peak']}"


def test_scratch_directories_are_released_as_the_library_is_processed(tmp_path, monkeypatch, capsys):
    import search_tool.extract
    from search_tool.cli import main
    root = tmp_path / 'files'
    root.mkdir()
    for i in range(4):
        (root / f'file{i}.txt').write_text(f'antenna {i}')
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    monkeypatch.setenv('TMPDIR', str(scratch))
    monkeypatch.setattr(search_tool.extract, 'extract', fake_extractor(lambda p: None if p.name == 'file1.txt' else 2, sleeps=0.02, scratch_marker='decoded.png'))
    assert main([str(root), 'antenna', '--cache-dir', str(tmp_path / 'cache'), '--json'],
                engine_factory=TinyEngine, confirm=lambda _: True) == 0
    output = capsys.readouterr()
    assert 'Skipping file1.txt:' in output.err
    assert list(scratch.glob('findmatch-*')) == [], 'extraction scratch must not accumulate'


def test_every_chunk_reports_progress_so_one_large_file_cannot_look_stalled(tmp_path, monkeypatch, capsys):
    import search_tool.extract
    from search_tool.cli import main
    root = tmp_path / 'files'
    root.mkdir()
    (root / 'clip.mp4').write_text('antenna recording')
    (root / 'note.txt').write_text('antenna note')
    monkeypatch.setattr(search_tool.extract, 'extract', fake_extractor(lambda p: 3 if p.suffix == '.mp4' else 1))
    assert main([str(root), 'antenna', '--cache-dir', str(tmp_path / 'cache'), '--json'],
                engine_factory=TinyEngine, confirm=lambda _: True) == 0
    err = capsys.readouterr().err
    for position in (1, 2, 3):
        assert f'Processing 1/2: clip.mp4 (chunk {position}/3)' in err
    assert 'Processing 2/2: note.txt' in err


def test_producer_stops_and_releases_scratch_when_a_database_error_aborts_the_run(tmp_path, monkeypatch, capsys):
    import sqlite3
    import search_tool.extract
    from search_tool.cli import main
    from search_tool.core import Index
    root = tmp_path / 'files'
    root.mkdir()
    for i in range(40):
        (root / f'file{i}.txt').write_text(f'antenna {i}')
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    monkeypatch.setenv('TMPDIR', str(scratch))
    monkeypatch.setattr(search_tool.extract, 'extract', fake_extractor(lambda _: 2, sleeps=0.02, scratch_marker='decoded.png'))
    def broken(*args):
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(Index, 'replace', broken)
    start = time.monotonic()
    assert main([str(root), 'antenna', '--cache-dir', str(tmp_path / 'cache'), '--json'],
                engine_factory=TinyEngine, confirm=lambda _: True) == 1
    elapsed = time.monotonic() - start
    output = capsys.readouterr()
    assert 'disk full' in output.err and 'Skipping' not in output.err
    assert elapsed < 10, f'main must not wait for the producer, took {elapsed:.1f}s'
    assert list(scratch.glob('findmatch-*')) == [], 'aborted runs must release extraction scratch'


def test_failed_file_is_skipped_while_later_files_are_indexed_and_searched(tmp_path, capsys):
    import sqlite3
    import zipfile
    root = tmp_path / 'files'
    root.mkdir()
    bad = root / 'a.webp'
    with zipfile.ZipFile(bad, 'w') as archive:
        archive.writestr('animation/animation.json', '{}')
    (root / 'z.txt').write_text('antenna calibration')
    cache = tmp_path / 'cache'
    argv = [str(root), 'antenna', '--cache-dir', str(cache), '--json']
    assert main(argv, engine_factory=TinyEngine, confirm=lambda _: True) == 0
    output = capsys.readouterr()
    assert [Path(h['path']).name for h in json.loads(output.out)] == ['z.txt']
    assert 'Skipping a.webp:' in output.err
    assert 'findmatch: skipped 1 file(s); results exclude these files. Failed files remain pending for retry.' in output.err
    with sqlite3.connect(next((cache / 'indexes').glob('*.sqlite3'))) as db:
        assert db.execute('SELECT path FROM files').fetchall() == [('z.txt',)]
    prompts = []
    assert main(argv, engine_factory=TinyEngine, confirm=lambda p: prompts.append(p) or True) == 0
    assert prompts[0].startswith('1 files need processing.')
    assert 'Processing 1/1: a.webp' in capsys.readouterr().err


def test_failed_changed_file_drops_stale_results_and_can_be_retried(tmp_path, capsys):
    root = tmp_path / 'files'
    root.mkdir()
    note = root / 'note.txt'
    note.write_text('antenna')
    cache = tmp_path / 'cache'
    argv = [str(root), 'antenna', '--cache-dir', str(cache), '--json']
    assert main(argv, engine_factory=TinyEngine, confirm=lambda _: True) == 0
    capsys.readouterr()
    note.write_text('')
    assert main(argv, engine_factory=TinyEngine, confirm=lambda _: True) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == []
    assert 'No searchable content' in output.err
    note.write_text('antenna repaired')
    assert main(argv, engine_factory=TinyEngine, confirm=lambda _: True) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_file_inference_failure_continues_but_invalid_reference_is_fatal(tmp_path, capsys):
    root = tmp_path / 'files'
    root.mkdir()
    (root / 'a.txt').write_text('fail this document')
    (root / 'z.txt').write_text('antenna')
    cache = tmp_path / 'cache'

    class FailingEngine(TinyEngine):
        def embed(self, chunks, query=False):
            if not query and chunks[0].value == 'fail this document':
                raise RuntimeError('document inference failed')
            return super().embed(chunks, query=query)

    argv = [str(root), 'antenna', '--cache-dir', str(cache), '--json']
    assert main(argv, engine_factory=FailingEngine, confirm=lambda _: True) == 0
    output = capsys.readouterr()
    assert len(json.loads(output.out)) == 1
    assert 'Skipping a.txt: document inference failed' in output.err
    bad_reference = tmp_path / 'reference.webp'
    bad_reference.write_bytes(b'not an image')
    assert main(argv + ['-r', str(bad_reference)], engine_factory=FailingEngine, confirm=lambda _: True) == 1
    output = capsys.readouterr()
    assert not output.out
    assert 'reference.webp' in output.err


def test_database_failure_is_not_treated_as_a_bad_file(tmp_path, monkeypatch, capsys):
    import sqlite3
    from search_tool.core import Index
    root = tmp_path / 'files'
    root.mkdir()
    (root / 'note.txt').write_text('antenna')
    def broken(*args):
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(Index, 'replace', broken)
    assert main([str(root), 'antenna', '--cache-dir', str(tmp_path / 'cache')],
                engine_factory=TinyEngine, confirm=lambda _: True) == 1
    output = capsys.readouterr()
    assert 'disk full' in output.err
    assert 'Skipping' not in output.err


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
