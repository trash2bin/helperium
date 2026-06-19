import os
import time
import argparse
import sys
from pathlib import Path

from db.database import Database
from fixtures.document_generator import MaterialDocumentGenerator
from rag.client import RagClient, RAG_SERVICE_URL
from mcp_server.tools.rag import RagTools

# Settings
os.environ["RAG_LOCAL_FILES_ONLY"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

def cmd_import(args):
    db = Database()
    rag = RagClient(RAG_SERVICE_URL)
    t0 = time.monotonic()

    def progress(stage, **kw):
        print(f"\n  [{stage.upper()}] ", end="", flush=True)

    try:
        result = rag.import_document_sync(
            path=args.path,
            discipline_id=args.discipline_id,
            title=args.title,
        )
        print(f"  done — {result.chunks_count} chunks, {time.monotonic()-t0:.1f}s")
    except (FileNotFoundError, ValueError) as e:
        print(f"ERR {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def cmd_list(args):
    """Показать загруженные документы."""
    db = Database()
    rag = RagClient(RAG_SERVICE_URL)

    docs = rag.list_documents_sync(discipline_id=args.discipline_id)
    if not docs:
        print("Документов нет.")
        return

    for doc in docs:
        print(f"  {doc.id}  {doc.title}  ({doc.mime_type})  {doc.source_path}")
    print(f"\nВсего: {len(docs)}")
    db.close()


def cmd_search(args):
    """Тестовый поиск по документам (без MCP-сервера)."""
    db = Database()
    rag = RagClient(RAG_SERVICE_URL)

    results = rag.search_documents_sync(
        query=args.query,
        discipline_id=args.discipline_id,
        limit=args.limit,
    )
    if not results:
        print("Ничего не найдено.")
        return

    for i, r in enumerate(results, 1):
        page_str = f"стр.{r.page}" if r.page is not None else "без стр."
        print(f"\n--- [{i}] score={r.score:.4f}  {r.document_title}  {page_str} ---")
        print(r.content[:500])
        if len(r.content) > 500:
            print("...")
    db.close()


def cmd_generate(args):
    """Сгенерировать PDF/DOCX-материалы для дисциплины."""
    if args.model:
        os.environ["DOCGEN_MODEL"] = args.model

    db = Database()

    rag = RagTools(db)
    generator = MaterialDocumentGenerator(db, rag)
    try:
        materials = generator.ensure_materials(
            discipline_id=args.discipline_id,
            force=args.force,
        )
        if not materials:
            print("Дисциплина не найдена или материалы не созданы.")
            db.close()
            return

        for material in materials:
            print(f"  {material.type}: {material.file_name}  {material.source_path}")
        print(f"\nВсего: {len(materials)}")
    except RuntimeError as e:
        print(f"ERR {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def cmd_generate_all(args):
    """Сгенерировать PDF/DOCX-материалы для всех дисциплин."""
    if args.model:
        os.environ["DOCGEN_MODEL"] = args.model

    db = Database()

    rag = RagTools(db)
    generator = MaterialDocumentGenerator(db, rag)
    disciplines = db.get_all_disciplines()
    created_total = 0

    try:
        for index, discipline in enumerate(disciplines, 1):
            print(f"[{index}/{len(disciplines)}] {discipline.name}")
            materials = generator.ensure_materials(discipline.id, force=args.force)
            for material in materials:
                print(f"  {material.type}: {material.file_name}")
            created_total += len(materials)

        print(f"\nГотово. Дисциплин: {len(disciplines)}, файлов в базе: {created_total}")
    except RuntimeError as e:
        print(f"ERR {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def _delete_documents(db: Database, rag: RagClient, rows) -> int:
    deleted = 0
    for row in rows:
        doc_id = row["id"]
        source_path = Path(row["source_path"])
        try:
            rag.delete_document_sync(document_id=doc_id)
        except Exception as exc:
            print(f"WARN не удалось удалить векторы {doc_id}: {exc}", file=sys.stderr)
        if source_path.exists():
            try:
                source_path.unlink()
            except OSError as exc:
                print(f"WARN не удалось удалить файл {source_path}: {exc}", file=sys.stderr)
        deleted += 1
    db.commit()
    return deleted


def _cleanup_empty_generated_dirs() -> None:
    generated_dir = Path("generated_materials").resolve()
    if not generated_dir.exists():
        return
    for path in sorted(generated_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            pass
    try:
        generated_dir.rmdir()
    except OSError:
        pass


def cmd_clear_generated(args):
    """Удалить сгенерированные материалы из SQLite, ChromaDB и с диска."""
    from db.database import Database
    db = Database()
    rag = RagClient(RAG_SERVICE_URL)

    # Получаем список документов через RAG клиент
    docs = rag.list_documents_sync(discipline_id=args.discipline_id)
    rows = [
        {"id": doc.id, "source_path": doc.source_path}
        for doc in docs
        if "generated_materials" in doc.source_path
    ]

    deleted = _delete_documents(db, rag, rows)
    _cleanup_empty_generated_dirs()
    print(f"Удалено документов: {deleted}")
    db.close()


def cmd_delete(args):
    """Удалить документ из индекса."""
    db = Database()
    rag = RagClient(RAG_SERVICE_URL)

    # По пути или по id
    if args.path:
        source_path = str(Path(args.path).resolve())
        # Нужно получить ID документа по пути
        docs = rag.list_documents_sync()
        row = None
        for doc in docs:
            if doc.source_path == source_path:
                row = {"id": doc.id, "title": doc.title, "source_path": doc.source_path}
                break
        if not row:
            print("Документ не найден.")
            db.close()
            return
    elif args.document_id:
        # По document_id - нужно получить информацию о документе
        docs = rag.list_documents_sync()
        row = None
        for doc in docs:
            if doc.id == args.document_id:
                row = {"id": doc.id, "title": doc.title, "source_path": doc.source_path}
                break
        if not row:
            print("Документ не найден.")
            db.close()
            return
    else:
        print("ERR укажите --path или --document-id", file=sys.stderr)
        sys.exit(1)

    doc_id = row["id"]
    title = row["title"]
    rag.delete_document_sync(document_id=doc_id)
    print(f"OK  удалён: {title} ({doc_id})")
    db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Управление документами RAG-системы agent-tutor",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # import
    p_import = sub.add_parser("import", help="Загрузить документ в индекс")
    p_import.add_argument("path", help="Путь к файлу (PDF, DOCX, TXT, MD, HTML)")
    p_import.add_argument("--discipline-id", "-d", help="ID дисциплины для привязки")
    p_import.add_argument("--title", "-t", help="Название документа")
    p_import.set_defaults(func=cmd_import)

    # list
    p_list = sub.add_parser("list", help="Показать загруженные документы")
    p_list.add_argument("--discipline-id", "-d", help="Фильтр по дисциплине")
    p_list.set_defaults(func=cmd_list)

    # search
    p_search = sub.add_parser("search", help="Тестовый поиск по документам")
    p_search.add_argument("query", help="Поисковый запрос")
    p_search.add_argument("--discipline-id", "-d", help="Фильтр по дисциплине")
    p_search.add_argument("--limit", "-n", type=int, default=5, help="Кол-во результатов")
    p_search.set_defaults(func=cmd_search)

    # generate
    p_generate = sub.add_parser("generate", help="Сгенерировать PDF/DOCX-материалы дисциплины")
    p_generate.add_argument("--discipline-id", "-d", required=True, help="ID дисциплины")
    p_generate.add_argument("--force", action="store_true", help="Пересоздать файлы")
    p_generate.add_argument("--model", "-m", help="Модель Ollama, например qwen2.5:0.5b")
    p_generate.set_defaults(func=cmd_generate)

    # generate-all
    p_generate_all = sub.add_parser("generate-all", help="Сгенерировать PDF/DOCX-материалы для всех дисциплин")
    p_generate_all.add_argument("--force", action="store_true", help="Пересоздать файлы")
    p_generate_all.add_argument("--model", "-m", help="Модель Ollama, например qwen2.5:0.5b")
    p_generate_all.set_defaults(func=cmd_generate_all)

    # clear-generated
    p_clear_generated = sub.add_parser(
        "clear-generated",
        help="Удалить сгенерированные материалы из базы, ChromaDB и с диска",
    )
    p_clear_generated.add_argument("--discipline-id", "-d", help="ID дисциплины")
    p_clear_generated.set_defaults(func=cmd_clear_generated)

    # delete
    p_delete = sub.add_parser("delete", help="Удалить документ из индекса")
    p_delete.add_argument("--path", help="Путь к файлу документа")
    p_delete.add_argument("--document-id", help="ID документа")
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    print("Ingest script started")
    main()
