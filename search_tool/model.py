"""Real quantized EmbeddingGemma 2 inference; no fallback model."""
import hashlib
import os
import sys
from pathlib import Path
import urllib.request
from contextlib import contextmanager
import ctypes
import numpy as np

MODEL_REVISION = '24d962e906c7d332c6428e71c9676855024569e2'
MODEL_SHA256 = 'e7a8a2204b91e0f96e92960e84a09a89212e1633dcb7575a9bf3378b4df77f4c'
MODEL_FILE = 'embeddinggemma-2-740m.litertlm'
MODEL_URL = f'https://huggingface.co/litert-community/embeddinggemma-2-740m-litert-lm/resolve/{MODEL_REVISION}/{MODEL_FILE}'
IDENTITY = f'embeddinggemma2:{MODEL_REVISION}:768:extract-v1:prefix-model-card'


def download_model(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.part')
    try:
        urllib.request.urlretrieve(MODEL_URL, temporary)
        with temporary.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != MODEL_SHA256:
            raise ValueError('Model checksum verification failed; retry download')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _native_diagnostics():
    """Keep C/C++ runtime logging out of the CLI/GUI JSON protocol.

    Engine calls run serially in the search worker process; fd redirection is
    process-wide and must not overlap unrelated stdout writers.
    """
    sys.stdout.flush()
    libc = ctypes.CDLL(None)
    libc.fflush(None)
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        yield
    finally:
        libc.fflush(None)
        os.dup2(saved, 1)
        os.close(saved)


class Engine:
    identity = IDENTITY

    def __init__(self, path):
        import litert_lm as lm
        self.lm = lm
        self.path = str(path)
        self.preference = os.environ.get('FINDMATCH_VISION_BACKEND', 'auto')
        if self.preference not in {'auto', 'cpu', 'gpu'}:
            raise ValueError('FINDMATCH_VISION_BACKEND must be auto, cpu, or gpu')
        lm.set_min_log_severity(lm.LogSeverity.ERROR)
        self.vision_backend = 'cpu' if self.preference == 'cpu' else 'gpu'
        try:
            self.engine = self._create(self.vision_backend)
        except (RuntimeError, OSError) as exc:
            if self.preference != 'auto':
                raise
            self.vision_backend = 'cpu'
            print(f'findmatch: GPU vision unavailable; using CPU ({exc}).', file=sys.stderr)
            self.engine = self._create('cpu')
        print(f'findmatch: vision backend: {self.vision_backend.upper()}.', file=sys.stderr)

    def _create(self, vision):
        cpu = self.lm.Backend.CPU(thread_count=4)
        with _native_diagnostics():
            return self.lm.EmbeddingEngine(
                self.path, backend=cpu,
                vision_backend=self.lm.Backend.GPU() if vision == 'gpu' else cpu,
                audio_backend=cpu)

    def embed(self, chunks, query=False):
        contents = []
        for chunk in chunks:
            if chunk.kind == 'text':
                text = chunk.value.strip()
                prefix = 'task: search result | query: ' if query else 'title: none | text: '
                contents.append(self.lm.Content.Text(prefix + text))
            elif chunk.kind == 'image':
                contents.append(self.lm.Content.ImageFile(chunk.value))
            elif chunk.kind == 'audio':
                contents.append(self.lm.Content.AudioFile(chunk.value))
            else:
                raise ValueError(f'Unknown modality: {chunk.kind}')
        options = self.lm.EmbeddingOptions(normalize=True, output_size=768, input_overflow_strategy=self.lm.InputOverflowStrategy.ERROR)
        try:
            with _native_diagnostics():
                result = self.engine.compute_embedding(contents, options)
        except (RuntimeError, OSError) as exc:
            if (getattr(self, 'preference', None) != 'auto' or
                    self.vision_backend != 'gpu' or not any(c.kind == 'image' for c in chunks)):
                raise
            print(f'findmatch: GPU vision failed; retrying on CPU ({exc}).', file=sys.stderr)
            self.engine.close()
            self.vision_backend = 'cpu'
            self.engine = self._create('cpu')
            with _native_diagnostics():
                result = self.engine.compute_embedding(contents, options)
        vector = np.asarray(result.embedding, dtype=np.float32)
        if vector.shape != (768,) or not np.isfinite(vector).all() or not np.linalg.norm(vector):
            raise ValueError('Model returned an invalid embedding')
        return vector / np.linalg.norm(vector)

    def close(self):
        self.engine.close()
