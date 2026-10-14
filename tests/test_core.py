from pathlib import Path
from search_tool.core import scan, plan


def test_scan_and_plan_new_files(tmp_path):
    root = tmp_path / 'files'
    root.mkdir()
    (root / 'note.txt').write_text('hello')
    (root / '.git').mkdir()
    (root / '.git' / 'config').write_text('ignore')
    (root / 'link').symlink_to(root / 'note.txt')
    found = scan(root)
    assert list(found) == ['note.txt']
    pending, deleted = plan(found, {})
    assert pending == ['note.txt']
    assert deleted == []


def test_embedding_cache_reuses_decoded_media_not_temporary_paths(tmp_path):
    import numpy as np
    from search_tool.core import Index
    from search_tool.extract import Chunk

    first = tmp_path / 'first.png'
    second = tmp_path / 'second.png'
    first.write_bytes(b'identical decoded media')
    second.write_bytes(first.read_bytes())
    calls = []

    class Engine:
        def embed(self, chunks):
            calls.append(chunks[0].value)
            return np.array([1.0, 0.0])

    db = tmp_path / 'index.sqlite3'
    with Index(db, 'model-v1') as index:
        index.embedding(Chunk('image', str(first), 'page 1', 'first'), Engine())
    with Index(db, 'model-v1') as index:
        np.testing.assert_array_equal(
            index.embedding(Chunk('image', str(second), 'page 2', 'second'), Engine()),
            [1.0, 0.0])
        index.embedding(Chunk('audio', str(second), 'audio', 'audio'), Engine())
        second.write_bytes(b'changed decoded media')
        index.embedding(Chunk('image', str(second), 'page 2', 'second'), Engine())
    assert calls == [str(first), str(second), str(second)]


def test_invalid_embedding_is_not_cached(tmp_path):
    import pytest
    from search_tool.core import Index
    from search_tool.extract import Chunk

    class Engine:
        def embed(self, chunks):
            return [float('nan'), 0.0]

    with Index(tmp_path / 'index.sqlite3', 'model-v1') as index:
        with pytest.raises(ValueError, match='Invalid embedding'):
            index.embedding(Chunk('text', 'test', 'text', 'test'), Engine())
        assert index.db.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0] == 0


def test_index_persists_and_ranks_best_chunk_per_file(tmp_path):
    from search_tool.core import Index
    db = tmp_path / 'index.sqlite3'
    with Index(db, 'model-v1') as index:
        index.replace('a.txt', (1, 2, 3), [('text', 'chunk 1', 'apple', [1.0, 0.0]), ('text', 'chunk 2', 'pear', [0.8, 0.6])])
        index.replace('b.txt', (4, 5, 6), [('text', 'chunk 1', 'car', [0.0, 1.0])])
    with Index(db, 'model-v1') as index:
        assert index.inventory() == {'a.txt': (1, 2, 3), 'b.txt': (4, 5, 6)}
        hits = index.search([1.0, 0.0], 10)
        assert [h['path'] for h in hits] == ['a.txt', 'b.txt']
        assert hits[0]['location'] == 'chunk 1'
        assert hits[0]['score'] == 1.0
        index.remove(['b.txt'])
        assert list(index.inventory()) == ['a.txt']
