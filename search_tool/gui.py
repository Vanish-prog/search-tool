"""Launch the optional native GPUI frontend without loading model weights."""
import os
import shutil
import subprocess
import sys
from pathlib import Path


def launch(args):
    binary = os.environ.get('FINDMATCH_GUI_BINARY') or shutil.which('findmatch-gui')
    if not binary:
        local = Path(__file__).resolve().parents[1] / 'gui' / 'target' / 'debug' / 'findmatch-gui'
        if local.is_file():
            binary = str(local)
    if not binary:
        print('findmatch: GPUI frontend is not installed. Use the Nix flake package, '
              'or build it with: nix develop --command cargo build --manifest-path gui/Cargo.toml',
              file=sys.stderr)
        return 1
    command = [binary, '--cache-dir', str(args.cache_dir), '--limit', str(args.limit)]
    if args.directory is not None:
        command.extend(['--directory', str(args.directory)])
    if args.query:
        command.extend(['--query', args.query])
    for reference in args.reference:
        command.extend(['--reference', str(reference)])
    if args.rebuild:
        command.append('--rebuild')
    env = dict(os.environ)
    env['FINDMATCH_GUI_PYTHON'] = sys.executable
    # Ensure the subprocess can import the same checkout or packaged source,
    # even when the caller launched from outside the repository.
    source = str(Path(__file__).resolve().parents[1])
    env['PYTHONPATH'] = source + os.pathsep + env.get('PYTHONPATH', '')
    process = None
    try:
        process = subprocess.Popen(command, env=env, start_new_session=True)
        return process.wait()
    except OSError as exc:
        print(f'findmatch: cannot launch GPUI frontend: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        return 130
