import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from db.database import Database
from rag.config import RagConfig
from rag.interfaces import EmbeddingProtocol


@pytest.fixture
def temp_dir():
    """Provides a temporary directory that is automatically cleaned up."""
    temp_path = tempfile.mkdtemp()
    yield Path(temp_path)
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def db_path(temp_dir):
    """Provides a path to a temporary SQLite database."""
    return temp_dir / "test_university.db"


@pytest.fixture
def test_db(db_path):
    """Provides an initialized Database instance with seed data in a temporary file."""
    # We load fixtures from the project root fixtures.json
    db = Database(db_path=db_path, load_seed_data=True)
    yield db
    db.close()


@pytest.fixture
def mock_embedding():
    """Provides a mocked EmbeddingProtocol implementation."""
    class MockEmbedding(EmbeddingProtocol):
        def encode_batched(self, texts: list[str]) -> list[list[float]]:
            # Return mock 384-dimensional vectors (typical for paraphrase-multilingual-MiniLM-L12-v2)
            return [[0.1] * 384 for _ in texts]

    return MockEmbedding()


@pytest.fixture
def rag_config(temp_dir):
    """Provides a configured RagConfig pointing to temporary paths."""
    config = RagConfig(
        chroma_path=str(temp_dir / "chroma_db"),
        chroma_collection="test_collection",
        embedding_device="cpu",
        embedding_model="mock",
        chunker_type="recursive",
    )
    return config
