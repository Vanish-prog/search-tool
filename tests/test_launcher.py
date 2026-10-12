import os
import subprocess
from pathlib import Path


def test_launcher_works_through_symlink(tmp_path):
    launcher = Path(__file__).resolve().parents[1] / 'bin/findmatch'
    link = tmp_path / 'findmatch'
    link.symlink_to(launcher)
    env = dict(os.environ)
    env.pop('LD_LIBRARY_PATH', None)
    result = subprocess.run([str(link), '--help'], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert 'EmbeddingGemma 2' in result.stdout
    assert 'Examples:' in result.stdout
    assert 'Process these files' in result.stdout
    assert 'metadata only' in result.stdout
    assert 'Exit codes:' in result.stdout
    assert 'Maximum number of results' in result.stdout
