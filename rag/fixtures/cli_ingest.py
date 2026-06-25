import os
import time
import argparse
import sys
from pathlib import Path

from agent_tutor_sdk.rag.client import RagClientSync, RAG_SERVICE_URL

# Settings
os.environ["RAG_LOCAL_FILES_ONLY"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"


def cmd_import(args):
    rag = RagClientSync(RAG_SERVICE_URL)
    t0 = time.monotonic()

    try:
        result = rag.import_document(
            path=args.path,
            discipline_id=args.discipline_id,
            title=args.title,
        )
        print(f"  done — {result.chunks_count} chunks, {time.monotonic() - t0:.1f}s")
    except (FileNotFoundError, ValueError) as e:
        print(f"ERR {e}", file=sys.stderr)
        sys.exit(1)


def cmd_list(args):
    rag = RagClientSync(RAG_SERVICE_URL)

    docs = rag.list_documents(discipline_id=args.discipline_id)
    if not docs:
        print("Документов нет.")
        return

    for doc in docs:
        print(f"  {doc.id}  {doc.title}  ({doc.mime_type})  {doc.source_path}")
    print(f"\nВсего: {len(docs)}")


def cmd_search(args):
    rag = RagClientSync(RAG_SERVICE_URL)

    results = rag.search_documents(
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


def _delete_documents(rag: RagClientSync, rows) -> int:
    deleted = 0
    for row in rows:
        doc_id = row["id"]
        source_path = Path(row["source_path"])
        try:
            rag.delete_document(document_id=doc_id)
        except Exception as exc:
            print(f"WARN не удалось удалить векторы {doc_id}: {exc}", file=sys.stderr)
        if source_path.exists():
            try:
                source_path.unlink()
            except OSError as exc:
                print(
                    f"WARN не удалось удалить файл {source_path}: {exc}",
                    file=sys.stderr,
                )
        deleted += 1
    return deleted


def _cleanup_empty_generated_dirs() -> None:
    generated_dir = Path("generated_materials").resolve()
    if not generated_dir.exists():
        return
    for path in sorted(
        generated_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True
    ):
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
    rag = RagClientSync(RAG_SERVICE_URL)

    docs = rag.list_documents(discipline_id=args.discipline_id)
    rows = [
        {"id": doc.id, "source_path": doc.source_path}
        for doc in docs
        if "generated_materials" in doc.source_path
    ]

    deleted = _delete_documents(rag, rows)
    _cleanup_empty_generated_dirs()
    print(f"Удалено документов: {deleted}")


def cmd_delete(args):
    rag = RagClientSync(RAG_SERVICE_URL)

    if args.path:
        source_path = str(Path(args.path).resolve())
        docs = rag.list_documents()
        row = None
        for doc in docs:
            if doc.source_path == source_path:
                row = {"id": doc.id, "title": doc.title, "source_path": doc.source_path}
                break
        if not row:
            print("Документ не найден.")
            return
    elif args.document_id:
        docs = rag.list_documents()
        row = None
        for doc in docs:
            if doc.id == args.document_id:
                row = {"id": doc.id, "title": doc.title, "source_path": doc.source_path}
                break
        if not row:
            print("Документ не найден.")
            return
    else:
        print("ERR укажите --path или --document-id", file=sys.stderr)
        sys.exit(1)

    doc_id = row["id"]
    title = row["title"]
    rag.delete_document(document_id=doc_id)
    print(f"OK  удалён: {title} ({doc_id})")


def main():
    parser = argparse.ArgumentParser(
        description="Управление документами RAG-системы agent-tutor",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="Загрузить документ в индекс")
    p_import.add_argument("path", help="Путь к файлу")
    p_import.add_argument("--discipline-id", "-d", help="ID дисциплины")
    p_import.add_argument("--title", "-t", help="Название")
    p_import.set_defaults(func=cmd_import)

    p_list = sub.add_parser("list", help="Список документов")
    p_list.add_argument("--discipline-id", "-d", help="Фильтр по дисциплине")
    p_list.set_defaults(func=cmd_list)

    p_search = sub.add_parser("search", help="Поиск по документам")
    p_search.add_argument("query", help="Запрос")
    p_search.add_argument("--discipline-id", "-d", help="Фильтр")
    p_search.add_argument("--limit", "-n", type=int, default=5)
    p_search.set_defaults(func=cmd_search)

    p_clear = sub.add_parser("clear-generated", help="Очистить сгенерированные")
    p_clear.add_argument("--discipline-id", "-d", help="Фильтр")
    p_clear.set_defaults(func=cmd_clear_generated)

    p_delete = sub.add_parser("delete", help="Удалить документ")
    p_delete.add_argument("--path", help="Путь к файлу")
    p_delete.add_argument("--document-id", help="ID документа")
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
