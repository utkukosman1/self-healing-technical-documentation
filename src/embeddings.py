"""Embedding + in-memory cosine retrieval (doc -> code mapping)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from openai import OpenAI

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
_BATCH_SIZE = 100


@dataclass
class CodeChunk:
    """One window of lines from a code file."""

    path: str
    start_line: int  # 1-based
    content: str


def chunk_code(path: str, content: str, max_lines: int = 60) -> list[CodeChunk]:
    """Split a file into sequential line windows for embedding."""
    lines = content.splitlines()
    if not lines:
        return []
    return [
        CodeChunk(
            path=path,
            start_line=start + 1,
            content="\n".join(lines[start:start + max_lines]),
        )
        for start in range(0, len(lines), max_lines)
    ]


class EmbeddingClient:
    """Thin wrapper around OpenAI embeddings with batching."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_EMBEDDING_MODEL,
        client: OpenAI | None = None,
    ):
        self.model = model
        self._client = client if client is not None else OpenAI(api_key=api_key)

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed texts, returning an (n, dim) float array in input order."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            batch = [t if t.strip() else " " for t in texts[start:start + _BATCH_SIZE]]
            response = self._client.embeddings.create(input=batch, model=self.model)
            vectors.extend(item.embedding for item in response.data)
        return np.asarray(vectors, dtype=np.float32)


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def rank_code_for_docs(
    doc_vecs: np.ndarray,
    code_vecs: np.ndarray,
    top_k: int = 5,
) -> list[list[tuple[int, float]]]:
    """For each doc vector, the indices + cosine scores of the top_k code vectors."""
    if doc_vecs.size == 0 or code_vecs.size == 0:
        return [[] for _ in range(doc_vecs.shape[0])]
    sims = _normalize_rows(doc_vecs) @ _normalize_rows(code_vecs).T
    k = min(top_k, code_vecs.shape[0])
    results: list[list[tuple[int, float]]] = []
    for row in sims:
        best = np.argsort(-row)[:k]
        results.append([(int(i), float(row[i])) for i in best])
    return results


def retrieve(
    doc_texts: list[str],
    code_texts: list[str],
    client: EmbeddingClient,
    top_k: int = 5,
) -> list[list[tuple[int, float]]]:
    """Embed both sides and rank code texts for each doc text."""
    doc_vecs = client.embed(doc_texts)
    code_vecs = client.embed(code_texts)
    return rank_code_for_docs(doc_vecs, code_vecs, top_k)
