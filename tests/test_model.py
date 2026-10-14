from pathlib import Path
import pytest
from search_tool.model import Engine, MODEL_REVISION


def test_real_model_text_and_image_embeddings(tmp_path):
    import numpy as np
    from PIL import Image
    from types import SimpleNamespace
    model = Path.home() / '.cache/findmatch/models/embeddinggemma-2-740m.litertlm'
    assert model.is_file(), 'Download the actual model before running integration test'
    picture = tmp_path / 'red.png'
    Image.new('RGB', (224, 224), 'red').save(picture)
    engine = Engine(model)
    try:
        text = engine.embed([SimpleNamespace(kind='text', value='red square')], query=True)
        image = engine.embed([SimpleNamespace(kind='image', value=str(picture))])
        import subprocess
        audio_path = tmp_path / 'tone.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-ar', '16000', '-ac', '1', str(audio_path)], check=True)
        audio = engine.embed([SimpleNamespace(kind='audio', value=str(audio_path))])
        mixed = engine.embed([SimpleNamespace(kind='text', value='red'), SimpleNamespace(kind='image', value=str(picture))], query=True)
        for vector in (text, image, audio, mixed):
            assert vector.shape == (768,)
            assert np.isfinite(vector).all()
            assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-5)
    finally:
        engine.close()


def test_download_rejects_bad_checksum(tmp_path, monkeypatch):
    from search_tool.model import download_model
    import urllib.request
    def wrong(url, path, report=None):
        Path(path).write_bytes(b'wrong model')
    monkeypatch.setattr(urllib.request, 'urlretrieve', wrong)
    with pytest.raises(ValueError, match='checksum'):
        download_model(tmp_path / 'model.litertlm')
    assert not (tmp_path / 'model.litertlm').exists()


def test_download_reports_bounded_progress_so_a_silent_fetch_is_visible(tmp_path, monkeypatch, capsys):
    from search_tool.model import download_model
    import urllib.request
    def fake(url, path, report=None):
        assert report is not None, 'a silent download is indistinguishable from a stalled backend'
        for block in range(1, 101):
            report(block, 5 * 1024 * 1024, 100 * 5 * 1024 * 1024)  # 5 MiB per block, 500 MiB total
            Path(path).write_bytes(b'x')
    monkeypatch.setattr(urllib.request, 'urlretrieve', fake)
    monkeypatch.setattr('search_tool.model.MODEL_SHA256', 'x'.rjust(64, '0'))
    monkeypatch.setattr('search_tool.model.hashlib.file_digest', lambda stream, name: type('D', (), {'hexdigest': lambda self: 'x'.rjust(64, '0')})())
    assert download_model(tmp_path / 'model.litertlm') is None
    lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith('Downloading EmbeddingGemma 2:')]
    percents = [int(line.rstrip('%').split()[-1]) for line in lines]
    assert percents[0] == 5 and percents[-1] == 100
    assert percents == sorted(percents)
    assert len(percents) == 20, f'progress must stay bounded, got {len(percents)} lines'


def test_model_rejects_context_overflow_instead_of_silently_truncating():
    from types import SimpleNamespace
    from litert_lm import InputOverflowStrategy
    class Capture:
        def compute_embedding(self, contents, options):
            assert options.input_overflow_strategy == InputOverflowStrategy.ERROR
            raise ValueError('context overflow')
    engine = object.__new__(Engine)
    import litert_lm
    engine.lm = litert_lm
    engine.engine = Capture()
    with pytest.raises(ValueError, match='context overflow'):
        engine.embed([SimpleNamespace(kind='text', value='long query')], query=True)
