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
