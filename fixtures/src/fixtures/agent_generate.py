import os
import argparse
import sys

from agent_tutor_sdk.db.database import Database
from fixtures.document_generator import MaterialDocumentGenerator
from fixtures.rag_tools import RagTools


def cmd_generate(args):
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

        print(
            f"\nГотово. Дисциплин: {len(disciplines)}, файлов в базе: {created_total}"
        )
    except RuntimeError as e:
        print(f"ERR {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Генерация учебных материалов для agent-tutor",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser(
        "generate", help="Сгенерировать материалы для одной дисциплины"
    )
    p_gen.add_argument("--discipline-id", "-d", required=True, help="ID дисциплины")
    p_gen.add_argument("--force", action="store_true", help="Пересоздать файлы")
    p_gen.add_argument("--model", "-m", help="Модель Ollama")
    p_gen.set_defaults(func=cmd_generate)

    p_all = sub.add_parser(
        "generate-all", help="Сгенерировать материалы для всех дисциплин"
    )
    p_all.add_argument("--force", action="store_true", help="Пересоздать файлы")
    p_all.add_argument("--model", "-m", help="Модель Ollama")
    p_all.set_defaults(func=cmd_generate_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
