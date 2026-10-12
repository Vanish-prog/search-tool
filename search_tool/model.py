"""Real quantized EmbeddingGemma 2 inference; no fallback model."""
import hashlib
from pathlib import Path
import urllib.request
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


class Engine:
    identity = IDENTITY

    def __init__(self, path):
        import litert_lm as lm
        self.lm = lm
        lm.set_min_log_severity(lm.LogSeverity.ERROR)
        cpu = lm.Backend.CPU(thread_count=4)
        self.engine = lm.EmbeddingEngine(str(path), backend=cpu, vision_backend=cpu, audio_backend=cpu)

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
        result = self.engine.compute_embedding(contents, self.lm.EmbeddingOptions(normalize=True, output_size=768, input_overflow_strategy=self.lm.InputOverflowStrategy.ERROR))
        vector = np.asarray(result.embedding, dtype=np.float32)
        if vector.shape != (768,) or not np.isfinite(vector).all() or not np.linalg.norm(vector):
            raise ValueError('Model returned an invalid embedding')
        return vector / np.linalg.norm(vector)

    def close(self):
        self.engine.close()
