"""Interactive local directory search."""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import fcntl
from pathlib import Path
from .core import scan, plan, Index


def ask(message):
    print(message, file=sys.stderr, end=' ', flush=True)
    try:
        return input().strip().lower() in {'y', 'yes'}
    except EOFError:
        return False


def main(argv=None, *, engine_factory=None, confirm=None):
    parser = argparse.ArgumentParser(
        prog='findmatch',
        description='Local multimodal directory search using EmbeddingGemma 2.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Examples:
  findmatch --gui
  findmatch --gui ~/Pictures "similar damage" -r ./front.jpg -r ./side.jpg
  findmatch ~/Documents "antenna calibration procedures"
  findmatch ~/Pictures "similar damage" --reference ./example.jpg
  findmatch ~/Recordings --reference ./voice-query.wav --json --limit 5

GUI:
  --gui opens a native GPUI window; directory and query may be entered there.
  Attach multiple reference files, browse ranked results, and view previews.
  Images preview inline; documents show matched text. Audio/video and other
  files open in their default application. Processing/download require
  separate approval in the window. --json is CLI-only.

Processing:
  New or changed files prompt: Process these files before searching? [y/N]
  Only y/yes proceeds. No, blank input, or EOF cancels processing and search.
  Unchanged files reuse the index; deleted files are removed from it.
  The initial model download requires separate permission. Search is local.

File support:
  Text/code, PDF, Office/OpenDocument text, images, audio, and sampled video.
  Unsupported binaries are indexed by filename/type/size metadata only.

Exit codes:
  0 success; 1 processing/runtime error; 2 cancelled or invalid arguments;
  130 interrupted. See README.md for extraction limits and installation.
''')
    parser.add_argument('--gui', action='store_true', help='Open the native GPUI interface with multiple reference attachments and result previews; directory/query are optional')
    parser.add_argument('directory', nargs='?', type=Path, help='Directory to search recursively (required without --gui; symlinks are skipped)')
    parser.add_argument('query', nargs='?', default='', help='Natural-language query; optional when using --reference')
    parser.add_argument('--reference', '-r', action='append', type=Path, default=[], help='Reference file to combine with the query; repeat for multiple files')
    parser.add_argument('--cache-dir', type=Path, default=Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'findmatch', help='Model/index storage outside the search directory (default: $XDG_CACHE_HOME/findmatch or ~/.cache/findmatch)')
    parser.add_argument('--rebuild', action='store_true', help='Ask permission to replace this directory index')
    parser.add_argument('--limit', type=int, default=10, help='Maximum number of results (default: 10; must be positive)')
    parser.add_argument('--json', action='store_true', help='Print results as JSON; prompts remain on stderr')
    args = parser.parse_args(argv)
    if args.gui:
        if args.json:
            parser.error('--json cannot be combined with --gui')
        if args.limit < 1:
            parser.error('--limit must be positive')
        from .gui import launch
        return launch(args)
    if args.directory is None:
        parser.error('directory is required without --gui')
    lock = None
    try:
        root = args.directory.resolve(strict=True)
        if not args.query.strip() and not args.reference:
            raise ValueError('Provide a query or at least one --reference')
        if args.limit < 1:
            raise ValueError('--limit must be positive')
        for reference in args.reference:
            if not reference.resolve(strict=True).is_file():
                raise ValueError(f'Reference is not a regular file: {reference}')
        cache = args.cache_dir.resolve()
        if cache == root or cache.is_relative_to(root):
            raise ValueError('--cache-dir must be outside the searched directory')
        key = hashlib.sha256(os.fsencode(root)).hexdigest()
        dbpath = cache / 'indexes' / f'{key}.sqlite3'
        lockpath = cache / 'locks' / f'{key}.lock'
        if lockpath.exists():
            lock = lockpath.open('a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another search is already processing this directory')
        found = scan(root)
        stored = {}
        if dbpath.exists():
            with sqlite3.connect(f'file:{dbpath}?mode=ro', uri=True) as db:
                stored = {r[0]: tuple(r[1:]) for r in db.execute('SELECT path,size,mtime,ctime FROM files')}
        pending, deleted = plan(found, {} if args.rebuild else stored)
        if (pending or args.rebuild) and not (confirm or ask)(f'{len(pending)} files need processing. Process these files before searching? [y/N]'):
            print('Search cancelled; no files processed.', file=sys.stderr)
            return 2
        if lock is None:
            lockpath.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock = lockpath.open('a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another search is already processing this directory')
            current = scan(root)
            if current != found:
                raise ValueError('Directory changed during confirmation; retry search')
        from .extract import extract, Chunk
        from .model import Engine, IDENTITY, MODEL_FILE, download_model
        model_path = cache / 'models' / MODEL_FILE
        if engine_factory is None and not model_path.exists():
            if not (confirm or ask)('Download EmbeddingGemma 2 (approximately 485 MB) for local inference? [y/N]'):
                print('Search cancelled; model not downloaded.', file=sys.stderr)
                return 2
            download_model(model_path)
        engine = engine_factory() if engine_factory else Engine(model_path)
        try:
            if args.rebuild:
                dbpath.unlink(missing_ok=True)
            with Index(dbpath, engine.identity) as index:
                index.remove(deleted)
                for number, rel in enumerate(pending, 1):
                    print(f'Processing {number}/{len(pending)}: {rel}', file=sys.stderr)
                    with tempfile.TemporaryDirectory(prefix='findmatch-') as directory:
                        chunks = extract(root / rel, Path(directory))
                        if not chunks:
                            raise ValueError(f'No searchable content: {rel}')
                        encoded = [(c.kind, c.location, c.snippet, engine.embed([c])) for c in chunks]
                        stat = (root / rel).stat()
                        if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != found[rel]:
                            raise ValueError(f'File changed while processing: {rel}; retry search')
                        index.replace(rel, found[rel], encoded)
                with tempfile.TemporaryDirectory(prefix='findmatch-query-') as directory:
                    query = [Chunk('text', args.query, 'query', args.query)] if args.query.strip() else []
                    for reference in args.reference:
                        query.extend(extract(reference.resolve(strict=True), Path(directory)))
                    hits = index.search(engine.embed(query, query=True), args.limit)
                for hit in hits:
                    hit['path'] = str(root / hit['path'])
                if args.json:
                    print(json.dumps(hits, ensure_ascii=False))
                else:
                    for hit in hits:
                        print(f"{hit['score']:.4f}\t{hit['path']}\t{hit['location']}")
                        if hit['snippet']:
                            print(f"  {hit['snippet']}")
        finally:
            engine.close()
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f'findmatch: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Search interrupted.', file=sys.stderr)
        return 130
    finally:
        if lock is not None:
            lock.close()


if __name__ == '__main__':
    sys.exit(main())
