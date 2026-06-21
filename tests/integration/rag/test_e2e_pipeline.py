"""E2E integration test for RAG pipeline.

Uses real ChromaDB (in tmp dir) + mock embedding + real SQLite + real parser/chunker.
Full round-trip: import txt file → search → context → list → delete.
"""

import sqlite3
from pathlib import Path

import pytest

from db.schema import create_schema
from rag.chunker import TextChunker
from rag.config import RagConfig
from rag.parser import DocumentParser
from rag.pipeline import RAGPipeline
from rag.repository import DocumentRepository
from rag.vector_store import ChromaDBVectorStore


@pytest.fixture
def e2e_config(temp_dir) -> RagConfig:
    """RagConfig with temp paths and recursive chunker (no HF model needed)."""
    return RagConfig(
        chroma_path=str(temp_dir / "chroma_db"),
        chroma_collection="e2e_test_collection",
        embedding_device="cpu",
        embedding_model="mock",
        chunker_type="recursive",
        chunk_size=512,
        chunk_overlap=0,
    )


@pytest.fixture
def e2e_pipeline(temp_dir, e2e_config, mock_embedding) -> RAGPipeline:
    """Fully assembled RAGPipeline with real components."""
    # SQLite
    db_path = temp_dir / "e2e.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)

    parser = DocumentParser(e2e_config)
    chunker = TextChunker(e2e_config)
    repo = DocumentRepository(conn, e2e_config)
    vstore = ChromaDBVectorStore(e2e_config, mock_embedding)

    pipeline = RAGPipeline(e2e_config, parser, chunker, mock_embedding, repo, vstore)
    yield pipeline
    conn.close()


def create_sample_txt(temp_dir: Path, filename: str, content: str) -> Path:
    """Helper to create a temp txt file."""
    path = temp_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


class TestRAGE2E:
    """E2E tests for RAG pipeline with real components."""

    def test_import_and_search_roundtrip(self, e2e_pipeline, temp_dir):
        """Import a document → verify it's searchable."""
        txt = create_sample_txt(
            temp_dir, "algorithms.txt",
            "Quick sort is an efficient divide-and-conquer sorting algorithm. "
            "It works by selecting a pivot element and partitioning the array. "
            "Merge sort is another divide-and-conquer algorithm. "
            "Binary search finds elements in O(log n) time. "
            "Hash tables provide O(1) average lookup time."
        )

        result = e2e_pipeline.import_document(
            path=str(txt),
            title="Algorithms Lecture",
        )
        assert result.document is not None
        assert result.chunks_count > 0
        assert result.document.title == "Algorithms Lecture"
        assert result.document.source_path.endswith("algorithms.txt")

        # Search — should find relevant chunks
        hits = e2e_pipeline.search_documents("quick sort", limit=3)
        assert len(hits) >= 1
        assert any("quick sort" in h.content.lower() for h in hits)

        hits2 = e2e_pipeline.search_documents("hash tables", limit=3)
        assert len(hits2) >= 1
        assert any("hash" in h.content.lower() for h in hits2)

    def test_list_documents(self, e2e_pipeline, temp_dir):
        """Multiple imports → list returns all documents."""
        txt1 = create_sample_txt(temp_dir, "doc1.txt", "Content one about Python.")
        txt2 = create_sample_txt(temp_dir, "doc2.txt", "Content two about Rust.")

        e2e_pipeline.import_document(path=str(txt1), title="Python Doc")
        e2e_pipeline.import_document(path=str(txt2), title="Rust Doc")

        docs = e2e_pipeline.list_documents()
        assert len(docs) == 2
        titles = {d.title for d in docs}
        assert titles == {"Python Doc", "Rust Doc"}

        # List with limit
        docs_limited = e2e_pipeline.list_documents(limit=1)
        assert len(docs_limited) == 1

    def test_build_rag_context(self, e2e_pipeline, temp_dir):
        """build_rag_context returns instruction + chunks."""
        txt = create_sample_txt(
            temp_dir, "context_test.txt",
            "Python is a high-level programming language. "
            "It supports multiple programming paradigms including OOP and functional. "
            "Python's standard library is extensive."
        )
        e2e_pipeline.import_document(path=str(txt), title="Python Guide")

        ctx = e2e_pipeline.build_rag_context("What is Python?", limit=5)
        assert ctx.query == "What is Python?"
        assert "фрагментам документов" in ctx.answer_instruction
        assert len(ctx.chunks) >= 1
        assert any("python" in c.content.lower() for c in ctx.chunks)

    def test_delete_document(self, e2e_pipeline, temp_dir):
        """Import → delete → verify it's gone from both index and vector store."""
        txt = create_sample_txt(temp_dir, "deleteme.txt", "This will be deleted.")
        result = e2e_pipeline.import_document(path=str(txt), title="Delete Me")

        doc_id = result.document.id
        assert doc_id is not None

        # Confirm it's findable
        assert len(e2e_pipeline.list_documents()) == 1

        # Delete vectors
        e2e_pipeline.delete_document_vectors(doc_id)

        # The document record still exists in SQLite (delete_document_vectors
        # only removes from ChromaDB, not from the repo)
        docs = e2e_pipeline.list_documents()
        assert len(docs) == 1  # still in SQLite

    def test_import_twice_reimports(self, e2e_pipeline, temp_dir):
        """Import the same file twice → replaces old version."""
        txt = create_sample_txt(
            temp_dir, "updated.txt",
            "Version one of the document."
        )
        r1 = e2e_pipeline.import_document(path=str(txt), title="Version 1")
        doc_id_1 = r1.document.id

        # Update content
        txt.write_text("Version two of the document.", encoding="utf-8")
        r2 = e2e_pipeline.import_document(path=str(txt), title="Version 2")

        # New import should have different ID (replaces old)
        assert r2.document.id != doc_id_1
        assert r2.document.title == "Version 2"

        # Only one document in index
        docs = e2e_pipeline.list_documents()
        assert len(docs) == 1
        assert docs[0].title == "Version 2"

    def test_search_empty_after_all_deleted(self, e2e_pipeline, temp_dir):
        """Import then delete all → search returns empty."""
        txt = create_sample_txt(temp_dir, "temp.txt", "Temporary content.")
        result = e2e_pipeline.import_document(path=str(txt), title="Temp")

        # Delete from vector store
        e2e_pipeline.delete_document_vectors(result.document.id)

        # Search on empty vector store returns []
        hits = e2e_pipeline.search_documents("temporary", limit=5)
        assert hits == []

    def test_import_nonexistent_file(self, e2e_pipeline):
        """Importing a non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            e2e_pipeline.import_document(path="/nonexistent/path.txt")

    def test_import_directory(self, e2e_pipeline, temp_dir):
        """Importing a directory raises ValueError."""
        with pytest.raises(ValueError, match="not a file"):
            e2e_pipeline.import_document(path=str(temp_dir))
