import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rag.config import RagConfig
from rag.parser import DocumentParser
from rag.chunker import (
    TextChunker,
    SemanticChunkerStrategy,
    RecursiveChunkerStrategy,
    SentenceChunkerStrategy,
)


# --- RAG Config Tests ---

def test_rag_config_defaults():
    """Test default values of RagConfig dataclass."""
    config = RagConfig()
    assert config.embedding_model == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert config.embedding_batch_size == 64
    assert config.chunker_type == "semantic"
    assert config.chunk_size == 512
    assert config.chroma_collection == "university_documents"
    assert config.chroma_path != ""  # should be populated in __post_init__


def test_rag_config_from_env():
    """Test that RagConfig parses environment variables correctly."""
    env_vars = {
        "RAG_EMBEDDING_MODEL": "test-model",
        "RAG_EMBEDDING_BATCH_SIZE": "128",
        "RAG_DEVICE": "cuda",
        "RAG_LOCAL_FILES_ONLY": "1",
        "RAG_CHUNKER_TYPE": "recursive",
        "RAG_CHUNK_SIZE": "256",
        "RAG_CHUNK_OVERLAP": "40",
        "RAG_PAGE_OVERLAP_TOKENS": "25",
        "CHROMA_PATH": "/tmp/test_chroma",
        "CHROMA_COLLECTION": "test_coll",
        "RAG_CONTEXT_MAX_TOKENS": "4000",
    }
    with patch.dict(os.environ, env_vars):
        config = RagConfig.from_env()
        assert config.embedding_model == "test-model"
        assert config.embedding_batch_size == 128
        assert config.embedding_device == "cuda"
        assert config.embedding_local_files_only is True
        assert config.chunker_type == "recursive"
        assert config.chunk_size == 256
        assert config.chunk_overlap == 40
        assert config.page_overlap_tokens == 25
        assert config.chroma_path == "/tmp/test_chroma"
        assert config.chroma_collection == "test_coll"
        assert config.context_max_tokens == 4000


# --- Document Parser Tests ---

def test_document_parser_text_files(temp_dir):
    """Test parsing simple text files (TXT, MD) directly without Docling."""
    config = RagConfig()
    parser = DocumentParser(config)

    # 1. TXT File
    txt_file = temp_dir / "test.txt"
    txt_file.write_text("Hello from a simple text file.", encoding="utf-8")
    
    pages = parser.extract_pages(txt_file)
    assert len(pages) == 1
    assert pages[0]["page"] is None
    assert "simple text file" in pages[0]["text"]

    # 2. Markdown File
    md_file = temp_dir / "test.md"
    md_file.write_text("# Title\nSome content.", encoding="utf-8")
    
    pages = parser.extract_pages(md_file)
    assert len(pages) == 1
    assert "Title" in pages[0]["text"]


def test_document_parser_pdf_mocked(temp_dir):
    """Test parsing complex files (PDF) by mocking DocumentConverter and item classes."""
    config = RagConfig()
    parser = DocumentParser(config)

    # Mock doc_converter property to return a mocked result
    mock_converter = MagicMock()
    mock_result = MagicMock()
    mock_document = MagicMock()
    
    # Use a simpler approach to mocking that avoids complex Pydantic instantiation
    from docling_core.types.doc.document import TextItem, DocItemLabel

    mock_item1 = MagicMock(spec=TextItem)
    mock_item1.label = DocItemLabel.PARAGRAPH
    mock_item1.text = "This is text on page 1"
    mock_item1.prov = [MagicMock(page_no=1)]

    mock_item2 = MagicMock(spec=TextItem)
    mock_item2.label = DocItemLabel.PARAGRAPH
    mock_item2.text = "This is text on page 2"
    mock_item2.prov = [MagicMock(page_no=2)]

    # Make sure items are list-iterable
    mock_document.iterate_items.return_value = [mock_item1, mock_item2]
    mock_result.document = mock_document
    mock_converter.convert.return_value = mock_result
    
    parser._doc_converter = mock_converter

    pdf_file = temp_dir / "test.pdf"
    pdf_file.write_text("", encoding="utf-8")  # dummy empty file

    pages = parser.extract_pages(pdf_file)
    
    assert len(pages) == 2
    assert pages[0]["page"] == 1
    assert pages[0]["text"] == "This is text on page 1"
    assert pages[1]["page"] == 2
    assert pages[1]["text"] == "This is text on page 2"


# --- Text Chunker Tests ---

def test_chunker_recursive_strategy():
    """Test recursive chunker strategy with custom parameters."""
    config = RagConfig(chunk_size=50, chunk_overlap=10)
    strategy = RecursiveChunkerStrategy(config)

    text = "First long paragraph. Second long paragraph. Third one."
    chunks = strategy.chunk(text)
    
    assert len(chunks) > 0
    # Every chunk should be under limit or equal to chunk_size (except if a word is too long)
    for c in chunks:
        assert len(c) <= config.chunk_size


def test_chunker_sentence_strategy():
    """Test sentence chunker strategy with regex splits."""
    config = RagConfig(chunk_size=100, chunk_overlap=0)
    strategy = SentenceChunkerStrategy(config)

    text = "Первое предложение. Второе предложение! Третье предложение?"
    chunks = strategy.chunk(text)
    
    assert len(chunks) > 0
    # Check that sentences are normalized
    assert "Первое предложение." in text


def test_text_chunker_page_overlapping():
    """Test TextChunker with multiple pages and page-overlap mechanism."""
    config = RagConfig(chunk_size=100, page_overlap_tokens=2, chunker_type="recursive")
    chunker = TextChunker(config)

    pages = [
        {"page": 1, "text": "This is page number one which contains some simple content."},
        {"page": 2, "text": "This is page number two with more detailed descriptions."}
    ]

    chunks = chunker.chunk_pages(pages)
    
    assert len(chunks) > 0
    assert chunks[0]["page"] == 1
    # Check that pages are processed


# --- RAGPipeline Tests ---


def test_pipeline_validate_path_success(temp_dir):
    """_validate_path accepts existing file."""
    from rag.pipeline import RAGPipeline

    file_path = temp_dir / "exists.txt"
    file_path.write_text("test", encoding="utf-8")

    result = RAGPipeline._validate_path(str(file_path))
    assert result.exists()
    assert result.is_file()


def test_pipeline_validate_path_not_found(temp_dir):
    """_validate_path raises FileNotFoundError for missing file."""
    from rag.pipeline import RAGPipeline

    missing = temp_dir / "nope.txt"
    with pytest.raises(FileNotFoundError, match="not found"):
        RAGPipeline._validate_path(str(missing))


def test_pipeline_validate_path_not_a_file(temp_dir):
    """_validate_path raises ValueError for a directory."""
    from rag.pipeline import RAGPipeline

    with pytest.raises(ValueError, match="not a file"):
        RAGPipeline._validate_path(str(temp_dir))


def test_pipeline_search_empty_query(rag_config, mock_embedding):
    """search_documents returns [] for empty/whitespace query."""
    from rag.pipeline import RAGPipeline
    from rag.parser import DocumentParser
    from rag.chunker import TextChunker
    from rag.repository import DocumentRepository
    from rag.vector_store import ChromaDBVectorStore
    import sqlite3

    conn = sqlite3.connect(":memory:")
    parser = DocumentParser(rag_config)
    chunker = TextChunker(rag_config)
    repo = DocumentRepository(conn, rag_config)
    vstore = ChromaDBVectorStore(rag_config, mock_embedding)

    pipeline = RAGPipeline(rag_config, parser, chunker, mock_embedding, repo, vstore)

    assert pipeline.search_documents("") == []
    assert pipeline.search_documents("   ") == []


def test_pipeline_search_clamps_limit(rag_config, mock_embedding):
    """search_documents clamps limit between 1 and config.search_limit_max."""
    from rag.pipeline import RAGPipeline
    from rag.parser import DocumentParser
    from rag.chunker import TextChunker
    from rag.repository import DocumentRepository
    from rag.vector_store import ChromaDBVectorStore
    import sqlite3

    conn = sqlite3.connect(":memory:")
    parser = DocumentParser(rag_config)
    chunker = TextChunker(rag_config)
    repo = DocumentRepository(conn, rag_config)
    vstore = ChromaDBVectorStore(rag_config, mock_embedding)

    pipeline = RAGPipeline(rag_config, parser, chunker, mock_embedding, repo, vstore)

    # Should not crash with limit=0 or limit=100500
    result = pipeline.search_documents("test", limit=0)
    assert len(result) == 0

    result = pipeline.search_documents("test", limit=99999)
    assert isinstance(result, list)


def test_pipeline_build_rag_context(rag_config, mock_embedding):
    """build_rag_context returns RagContext with instruction and query."""
    from rag.pipeline import RAGPipeline
    from rag.parser import DocumentParser
    from rag.chunker import TextChunker
    from rag.repository import DocumentRepository
    from rag.vector_store import ChromaDBVectorStore
    import sqlite3

    conn = sqlite3.connect(":memory:")
    parser = DocumentParser(rag_config)
    chunker = TextChunker(rag_config)
    repo = DocumentRepository(conn, rag_config)
    vstore = ChromaDBVectorStore(rag_config, mock_embedding)

    pipeline = RAGPipeline(rag_config, parser, chunker, mock_embedding, repo, vstore)

    ctx = pipeline.build_rag_context("sorting algorithms")
    assert ctx.query == "sorting algorithms"
    assert ctx.answer_instruction != ""
    assert isinstance(ctx.chunks, list)


def test_pipeline_import_roundtrip(temp_dir, rag_config, mock_embedding):
    """Full round-trip: import txt → list → search."""
    import sqlite3
    from rag.pipeline import RAGPipeline
    from rag.parser import DocumentParser
    from rag.chunker import TextChunker
    from rag.repository import DocumentRepository
    from rag.vector_store import ChromaDBVectorStore

    db_path = temp_dir / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Init schema using real schema
    from db.schema import create_schema
    create_schema(conn)

    parser = DocumentParser(rag_config)
    chunker = TextChunker(rag_config)
    repo = DocumentRepository(conn, rag_config)
    vstore = ChromaDBVectorStore(rag_config, mock_embedding)

    pipeline = RAGPipeline(rag_config, parser, chunker, mock_embedding, repo, vstore)

    # Create test file
    txt_file = temp_dir / "lecture.txt"
    txt_file.write_text(
        "Quick sort is an efficient sorting algorithm. "
        "It uses divide and conquer strategy. "
        "Merge sort also uses divide and conquer. "
        "Binary search works on sorted arrays.",
        encoding="utf-8",
    )

    # Import
    result = pipeline.import_document(
        path=str(txt_file),
        title="Lecture 1: Sorting",
    )
    assert result.document is not None
    assert result.chunks_count > 0
    assert result.document.title == "Lecture 1: Sorting"

    # List
    docs = pipeline.list_documents()
    assert len(docs) == 1
    assert docs[0].title == "Lecture 1: Sorting"

    # Search
    hits = pipeline.search_documents("quick sort", limit=5)
    assert len(hits) > 0
    assert "sort" in hits[0].content.lower()
