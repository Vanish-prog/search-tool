"""Backend selection tests without model weights or a graphics device."""
from types import SimpleNamespace

import numpy as np
import pytest

from search_tool.model import Engine


def runtime(monkeypatch, *, fail_gpu=False, fail_inference=False):
    import litert_lm as lm
    created = []

    class FakeEngine:
        def __init__(self, path, **kwargs):
            self.gpu = isinstance(kwargs['vision_backend'], lm.Backend.GPU)
            self.closed = False
            self.calls = 0
            created.append(self)
            if self.gpu and fail_gpu:
                raise RuntimeError('No adapters found')
            assert isinstance(kwargs['backend'], lm.Backend.CPU)
            assert isinstance(kwargs['audio_backend'], lm.Backend.CPU)

        def compute_embedding(self, contents, options):
            self.calls += 1
            assert options.output_size == 768
            assert options.input_overflow_strategy == lm.InputOverflowStrategy.ERROR
            if self.gpu and fail_inference:
                raise RuntimeError('GPU device lost')
            return SimpleNamespace(embedding=np.ones(768))

        def close(self):
            self.closed = True

    monkeypatch.setattr(lm, 'EmbeddingEngine', FakeEngine)
    monkeypatch.setattr(lm, 'set_min_log_severity', lambda _: None)
    monkeypatch.delenv('FINDMATCH_VISION_BACKEND', raising=False)
    return created


def test_auto_prefers_gpu_vision(monkeypatch, capsys):
    created = runtime(monkeypatch)
    engine = Engine('model')
    assert created[0].gpu
    assert 'GPU' in capsys.readouterr().err
    engine.close()
    assert created[0].closed


def test_auto_falls_back_without_gpu(monkeypatch, capsys):
    created = runtime(monkeypatch, fail_gpu=True)
    engine = Engine('model')
    assert len(created) == 2 and not created[-1].gpu
    assert 'CPU' in capsys.readouterr().err
    engine.close()


def test_cpu_override_never_probes_gpu(monkeypatch):
    created = runtime(monkeypatch)
    monkeypatch.setenv('FINDMATCH_VISION_BACKEND', 'cpu')
    engine = Engine('model')
    assert len(created) == 1 and not created[0].gpu
    engine.close()


def test_explicit_gpu_reports_failure(monkeypatch):
    created = runtime(monkeypatch, fail_gpu=True)
    monkeypatch.setenv('FINDMATCH_VISION_BACKEND', 'gpu')
    with pytest.raises(RuntimeError, match='No adapters'):
        Engine('model')
    assert len(created) == 1


def test_auto_retries_image_on_cpu_after_gpu_failure(monkeypatch, capsys):
    created = runtime(monkeypatch, fail_inference=True)
    engine = Engine('model')
    chunk = SimpleNamespace(kind='image', value='picture.png')
    assert engine.embed([chunk]).shape == (768,)
    assert created[0].closed and not created[-1].gpu
    engine.embed([chunk])
    assert len(created) == 2 and created[-1].calls == 2
    assert 'CPU' in capsys.readouterr().err
    engine.close()


@pytest.mark.parametrize('preference', ['auto', 'gpu'])
def test_input_errors_do_not_trigger_backend_fallback(monkeypatch, preference):
    created = runtime(monkeypatch)
    monkeypatch.setenv('FINDMATCH_VISION_BACKEND', preference)
    engine = Engine('model')

    def overflow(contents, options):
        raise ValueError('context overflow')

    created[0].compute_embedding = overflow
    with pytest.raises(ValueError, match='context overflow'):
        engine.embed([SimpleNamespace(kind='image', value='picture.png')])
    assert len(created) == 1
    engine.close()


def test_explicit_gpu_does_not_retry_inference(monkeypatch):
    created = runtime(monkeypatch, fail_inference=True)
    monkeypatch.setenv('FINDMATCH_VISION_BACKEND', 'gpu')
    engine = Engine('model')
    with pytest.raises(RuntimeError, match='device lost'):
        engine.embed([SimpleNamespace(kind='image', value='picture.png')])
    assert len(created) == 1
    engine.close()


def test_native_runtime_diagnostics_stay_off_json_stdout(monkeypatch, capfd):
    import os
    import litert_lm as lm
    runtime(monkeypatch)
    original = lm.EmbeddingEngine

    def noisy(*args, **kwargs):
        os.write(1, b'native GPU diagnostic\n')
        return original(*args, **kwargs)

    monkeypatch.setattr(lm, 'EmbeddingEngine', noisy)
    engine = Engine('model')
    engine.close()
    captured = capfd.readouterr()
    assert captured.out == ''
    assert 'native GPU diagnostic' in captured.err


def test_invalid_backend_fails_before_engine_creation(monkeypatch):
    created = runtime(monkeypatch)
    monkeypatch.setenv('FINDMATCH_VISION_BACKEND', 'invalid')
    with pytest.raises(ValueError, match='FINDMATCH_VISION_BACKEND'):
        Engine('model')
    assert not created
