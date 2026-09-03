from types import SimpleNamespace

import numpy as np

from src.embeddings import (
    EmbeddingClient,
    chunk_code,
    rank_code_for_docs,
    retrieve,
)


class FakeEmbeddingsEndpoint:
    """Offline stand-in for client.embeddings with a text -> vector lookup."""

    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors
        self.batches: list[list[str]] = []

    def create(self, input, model):
        self.batches.append(list(input))
        data = [SimpleNamespace(embedding=self.vectors[text]) for text in input]
        return SimpleNamespace(data=data)


class FakeOpenAI:
    def __init__(self, vectors: dict[str, list[float]]):
        self.embeddings = FakeEmbeddingsEndpoint(vectors)


class TestChunkCode:
    def test_short_file_single_chunk(self):
        content = "line1\nline2\nline3"
        chunks = chunk_code("a.py", content, max_lines=60)
        assert len(chunks) == 1
        assert chunks[0].path == "a.py"
        assert chunks[0].start_line == 1
        assert chunks[0].content == content

    def test_long_file_windows(self):
        lines = [f"line{i}" for i in range(1, 131)]
        chunks = chunk_code("a.py", "\n".join(lines), max_lines=60)
        assert [c.start_line for c in chunks] == [1, 61, 121]
        assert chunks[0].content.splitlines()[0] == "line1"
        assert chunks[1].content.splitlines()[0] == "line61"
        assert chunks[2].content.splitlines()[-1] == "line130"

    def test_exact_multiple_no_empty_tail(self):
        lines = ["x"] * 120
        chunks = chunk_code("a.py", "\n".join(lines), max_lines=60)
        assert len(chunks) == 2

    def test_empty_content(self):
        assert chunk_code("a.py", "") == []


class TestEmbeddingClient:
    def _client(self, vectors):
        return EmbeddingClient(api_key="test", client=FakeOpenAI(vectors))

    def test_embed_returns_vectors_in_order(self):
        vectors = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
        result = self._client(vectors).embed(["a", "b"])
        assert result.shape == (2, 2)
        assert result[0].tolist() == [1.0, 0.0]
        assert result[1].tolist() == [0.0, 1.0]

    def test_embed_batches_requests(self):
        vectors = {f"t{i}": [float(i), 1.0] for i in range(250)}
        fake = FakeOpenAI(vectors)
        result = EmbeddingClient(api_key="test", client=fake).embed(list(vectors))
        assert len(fake.embeddings.batches) == 3
        assert [len(b) for b in fake.embeddings.batches] == [100, 100, 50]
        assert result.shape == (250, 2)

    def test_embed_empty_input_no_api_call(self):
        fake = FakeOpenAI({})
        result = EmbeddingClient(api_key="test", client=fake).embed([])
        assert result.shape == (0, 0)
        assert fake.embeddings.batches == []

    def test_embed_blank_text_replaced(self):
        vectors = {" ": [0.5, 0.5]}
        fake = FakeOpenAI(vectors)
        result = EmbeddingClient(api_key="test", client=fake).embed([""])
        assert result.shape == (1, 2)


class TestRankCodeForDocs:
    def test_correct_file_ranked_top1(self):
        doc_vecs = np.array([[1.0, 0.0], [0.0, 1.0]])
        code_vecs = np.array([[0.9, 0.1], [0.0, 1.0], [0.2, 0.8]])
        ranked = rank_code_for_docs(doc_vecs, code_vecs, top_k=2)
        assert ranked[0][0][0] == 0
        assert ranked[1][0][0] == 1
        assert ranked[0][0][1] >= ranked[0][1][1]

    def test_top_k_clamped_to_code_count(self):
        doc_vecs = np.array([[1.0, 0.0]])
        code_vecs = np.array([[1.0, 0.0], [0.0, 1.0]])
        ranked = rank_code_for_docs(doc_vecs, code_vecs, top_k=5)
        assert len(ranked[0]) == 2

    def test_zero_vector_does_not_crash(self):
        doc_vecs = np.array([[0.0, 0.0]])
        code_vecs = np.array([[1.0, 0.0]])
        ranked = rank_code_for_docs(doc_vecs, code_vecs, top_k=1)
        assert ranked == [[(0, 0.0)]]

    def test_empty_inputs(self):
        ranked = rank_code_for_docs(np.zeros((0, 0)), np.zeros((0, 0)))
        assert ranked == []
        ranked = rank_code_for_docs(np.array([[1.0, 0.0]]), np.zeros((0, 0)))
        assert ranked == [[]]


class TestRetrieve:
    def test_end_to_end_with_fake_client(self):
        vectors = {
            "doc: auth flow": [1.0, 0.0],
            "code: login()": [0.95, 0.05],
            "code: refund()": [0.0, 1.0],
        }
        client = EmbeddingClient(api_key="test", client=FakeOpenAI(vectors))
        ranked = retrieve(
            ["doc: auth flow"],
            ["code: login()", "code: refund()"],
            client,
            top_k=1,
        )
        assert ranked[0][0][0] == 0
        assert ranked[0][0][1] > 0.99
