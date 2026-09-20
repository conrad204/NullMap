"""Local MiniLM embeddings shared by offline indexing and online retrieval.

No hosted embedding endpoint is used. Loading/downloading a model is deferred
until embeddings are requested, so BM25 and metadata-only operation stay light.
"""

import asyncio
from functools import lru_cache

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = "cpu", batch_size: int = 64):
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError("Install the ingest extra to enable local embeddings: uv sync --extra ingest") from exc
            try:
                # Avoid repeated network checks when the shared model cache is already populated.
                self._model = SentenceTransformer(self.model_name, device=self.device,
                                                  local_files_only=True)
            except OSError:
                self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._load().encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                                   show_progress_bar=False, convert_to_numpy=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.encode([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.encode(texts)


@lru_cache(maxsize=4)
def get_embedder(model_name: str = DEFAULT_MODEL, device: str = "cpu") -> Embedder:
    return Embedder(model_name=model_name, device=device)


class QueryEmbedder:
    """One query embedding per call, loaded on first use and encoded off the loop.

    ``None`` rather than a zero vector when embeddings are disabled: a caller
    must say it could not embed, not search with a direction it invented.
    """

    def __init__(self, config):
        self.config = config
        self.embedder: Embedder | None = None

    async def embed(self, text: str) -> list[float] | None:
        if not self.config.embeddings_enabled:
            return None
        if self.embedder is None:
            self.embedder = get_embedder(self.config.embedding_model, self.config.embedding_device)
        return await asyncio.to_thread(self.embedder.embed_query, text)
