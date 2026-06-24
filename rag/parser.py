"""Парсинг документов в список страниц."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling_core.types.doc.document import TextItem, TableItem

from rag.config import RagConfig
from rag._types import PageDict

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DocumentParser:
    """Извлекает текст из файлов постранично."""

    def __init__(self, config: RagConfig) -> None:
        self.config = config
        self._doc_converter: DocumentConverter | None = None

    @property
    def doc_converter(self) -> DocumentConverter:
        """Ленивая инициализация DocumentConverter."""
        if self._doc_converter is None:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.accelerator_options.device = self.config.embedding_device
            self._doc_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                }
            )
        return self._doc_converter

    def extract_pages(self, source_path: Path) -> list[PageDict]:
        """Извлечь текст постранично из файла."""
        suffix = source_path.suffix.lower()

        # Простые текстовые форматы — читаем напрямую
        if suffix in {".txt", ".md", ".markdown", ".csv", ".json", ".py"}:
            return [{"page": None, "text": source_path.read_text(encoding="utf-8")}]

        # PDF и другие сложные форматы — через Docling
        result = self.doc_converter.convert(str(source_path))
        dl_doc = result.document

        # Собираем текст по страницам
        page_lines: dict[int, list[str]] = {}
        for item in dl_doc.iterate_items():
            text = ""
            if isinstance(item, TextItem):
                text = item.text
            elif isinstance(item, TableItem):
                # Таблицы экспортируем в markdown для сохранения структуры
                text = item.export_to_markdown()
            else:
                continue

            if not text.strip():
                continue

            # Определяем номер страницы
            page_no = 0
            if item.prov:
                page_no = item.prov[0].page_no
            page_lines.setdefault(page_no, []).append(text)

        # Fallback на markdown-экспорт
        if not page_lines:
            md_text = dl_doc.export_to_markdown()
            if md_text.strip():
                return [{"page": None, "text": md_text}]
            return []

        # Собираем результат
        result_pages: list[PageDict] = []
        for page_no in sorted(page_lines):
            result_pages.append(
                {
                    "page": page_no if page_no > 0 else None,
                    "text": "\n".join(page_lines[page_no]),
                }
            )
        return result_pages
