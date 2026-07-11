"""Золотой QA датасет для RAG-бенчмарка.

Содержит 10 синтетических учебных документов по 6 дисциплинам
и 27 запросов с известной релевантностью.

Метрики: Recall@1, Recall@3, Recall@5, MRR.
Baseline хранится в rag/tests/benchmark/baseline.json.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("rag.benchmark")

BASELINE_PATH = Path(__file__).parent / "baseline.json"

# ── Золотой датасет ──────────────────────────────────────────────────
# (title, discipline_name, content)

GOLDEN_DOCUMENTS: List[Tuple[str, str, str]] = [
    # ── Алгоритмы (2) ──────────────────────────────────────────────
    (
        "Быстрая сортировка (Quick Sort)",
        "Алгоритмы и структуры данных",
        (
            "Быстрая сортировка (quick sort) — это один из самых эффективных алгоритмов "
            "сортировки, работающий по принципу «разделяй и властвуй». Алгоритм выбирает "
            "опорный элемент (pivot) и перестраивает массив так, что элементы меньше опорного "
            "оказываются слева, а больше — справа. Затем рекурсивно сортирует левую и правую "
            "части. Средняя временная сложность — O(n log n), худшая — O(n²) при неудачном "
            "в��боре опорного элемента. На практике часто выбирают случайный pivot или "
            "медиану из трёх элементов, чтобы избежать худшего случая. Быстрая сортировка "
            "не является устойчивой (stable), но работает на месте (in-place) с O(log n) "
            "дополнительной памяти для рекурсивных вызовов. В стандартной библиотеке многих "
            "языков программирования используется именно quick sort как основной алгоритм "
            "сортировки массивов. Для связанных списков быстрая сортировка менее эффективна, "
            "там чаще применяется сортировка слиянием. Важно помнить, что quick sort чувствителен "
            "к выбору опорного элемента: для уже отсортированного массива простой выбор "
            "первого элемента как pivot даёт квадратичную сложность."
        ),
    ),
    (
        "Бинарный поиск в отсортированном массиве",
        "Алгоритмы и структуры данных",
        (
            "Бинарный поиск — это алгоритм поиска элемента в отсортированном массиве за "
            "O(log n) времени. Алгоритм работает путём деления массива пополам на каждом шаге: "
            "сравнивает искомый элемент со средним элементом массива и, в зависимости от "
            "результата, продолжает поиск в левой или правой половине. Требование к данным: "
            "массив должен быть отсортирован. Если массив не отсортирован, бинарный поиск "
            "неприменим — нужно использовать линейный поиск (O(n)) или сначала отсортировать "
            "данные. Существует несколько вариаций: поиск первого вхождения, последнего "
            "вхождения, поиск элемента, ближайшего к заданному. Бинарный поиск можно "
            "реализовать как итеративно (с циклами), так и рекурсивно. В Python бинарный "
            "поиск реализован в модуле bisect. На практике бинарный поиск используется "
            "повсеместно: от поиска в базах данных (B-деревья) до отладки (git bisect)."
        ),
    ),
    # ── Базы данных (2) ────────────────────────────────────────────
    (
        "SQL JOIN: типы и применение",
        "Базы данных",
        (
            "SQL JOIN — это операция соединения таблиц в реляционных базах данных. "
            "Основные типы: INNER JOIN возвращает только строки, где есть совпадение "
            "в обеих таблицах. LEFT JOIN возвращает все строки из левой таблицы и "
            "совпадающие из правой. RIGHT JOIN — наоборот. FULL OUTER JOIN — все строки "
            "из обеих таблиц. CROSS JOIN — декартово произведение. Для работы JOIN "
            "необходимо указать условие соединения через ON или USING. Важно выбирать "
            "правильный тип JOIN: INNER JOIN эффективнее, но может потерять данные; "
            "LEFT JOIN безопаснее, но может вернуть NULL в несовпадающих строках. "
            "Множественные JOIN в одном запросе выполняются слева направо, но "
            "оптимизатор БД может изменить порядок для повышения произво��ительности."
        ),
    ),
    (
        "Индексы в базах данных: B-деревья и хеш-таблицы",
        "Базы данных",
        (
            "Индексы в базах данных ускоряют поиск записей ценой дополнительного "
            "дискового пространства и замедления операций записи. Наиболее распространённый "
            "тип — B-дерево (B-tree). B-деревья поддерживают эффективный поиск по "
            "диапазону, сортировку и операции больше/меньше за O(log n). Хеш-индексы, "
            "напротив, работают только на точное равенство, но быстрее B-деревьев "
            "при поиске по ключу. Составные индексы (composite index) учитывают "
            "порядок столбцов: для запроса WHERE a=1 AND b=2 индекс (a, b) эффективен, "
            "а (b, a) — нет. Не следует создавать индексы на каждом столбце: каждый "
            "индекс замедляет INSERT, UPDATE, DELETE. Оптимальная стратегия — "
            "анализировать реальные запросы через EXPLAIN и добавлять индексы под "
            "конкретные паттерны доступа."
        ),
    ),
    # ── Сети (2) ────────────────────────────────────────────────────
    (
        "Протокол TCP: надёжная передача данных",
        "Компьютерные сети",
        (
            "TCP (Transmission Control Protocol) — транспортный протокол, обеспечивающий "
            "надёжную доставку данных между приложениями. TCP устанавливает соединение "
            "через тройное рукопожатие (SYN, SYN-ACK, ACK) перед передачей данных. "
            "Механизмы TCP: подтверждение получения (ACK), повторная передача потерянных "
            "пакетов, контроль перегрузки (congestion control) и управление потоком (flow "
            "control) через скользящее окно. TCP гарантирует, что данные доставляются в "
            "правильном порядке и без потерь, но за это приходится платить задержками. "
            "Для приложений реального времени (видеозвонки, игры) часто используется UDP, "
            "который не гарантирует доставку, но имеет меньшую задержку."
        ),
    ),
    (
            "DNS: система доменных имён",
            "Компьютерные сети",
            (
                "DNS (Domain Name System) преобразует человекочитаемые доменные имена "
                "(например, example.com) в IP-адреса. DNS — это распределённая иерархическая "
                "система: корневые серверы → серверы верхнего уровня (.com, .ru) → "
                "авторитетные серверы доменов. При вводе URL в браузере сначала проверяется "
                "локальный кэш DNS, затем запрос идёт к рекурсивному резолверу (обычно "
                "DNS провайдера), который последовательно опрашивает серверы, пока не "
                "найдёт IP-адрес. DNS использует протокол UDP (порт 53) для запросов и "
                "TCP для зонных передач. Типы записей: A (IPv4), AAAA (IPv6), CNAME (алиас), "
                "MX (почта), TXT (произвольный текст). Безопасность: DNSSEC защищает от "
                "подмены ответов, но внедрён не везде."
            ),
        ),
    # ── ML (2) ─────────────────────────────────────────────────────
    (
        "Деревья решений в машинном обучении",
        "Машинное обучение",
        (
            "Деревья решений (decision trees) — это метод машинного обучения, "
            "использующий древовидную структуру для принятия решений на основе "
            "признаков данных. Каждый внутренний узел проверяет значение одного "
            "признака, каждая ветвь — результат проверки, а листья содержат "
            "итоговый прогноз. Деревья решений легко интерпретируются и не требуют "
            "масштабирования данных. Основные алгоритмы построения: ID3 (использует "
            "информационный выигрыш), C4.5 (коэффициент усиления), CART (индекс "
            "Джини). Деревья склонны к переобучению — для борьбы применяют "
            "ограничение глубины, минимальное количество образцов в листе и "
            "прунинг (обрезание ветвей). Ансамбли деревьев (Random Forest, "
            "Gradient Boosting) обычно работают лучше одного дерева."
        ),
    ),
    (
        "Нейронные сети: основы и архитектура",
        "Машинное обучение",
        (
            "Нейронные сети — это вычислительные модели, вдохновлённые структурой "
            "биологических нейронов. Простейшая форма — полносвязная нейронная сеть "
            "(Multi-Layer Perceptron), состоящая из входного слоя, одного или нескольких "
            "скрытых слоёв и выходного слоя. Каждый нейрон вычисляет взвешенную сумму "
            "входов и пропускает её через нелинейную функцию активации (ReLU, сигмоида, "
            "tanh). Обучение происходит через обратное распространение ошибки "
            "(backpropagation) и градиентный спуск. Свёрточные нейронные сети (CNN) "
            "эффективны для изображений, рекуррентные (RNN) — для последовательностей, "
            "трансформеры — для текста. Для обучения нейронных сетей требуются "
            "большие объёмы данных и вычислительные ресурсы (GPU)."
        ),
    ),
    # ── Криптография (2) ──────────────────────────────────────────
    (
        "Симметричное и асимметричное шифрование",
        "Криптография",
        (
            "Шифрование делится на два основных типа: симметричное (один ключ для "
            "шифрования и расшифровки) и асимметричное (открытый и закрытый ключи). "
            "Симметричное шифрование (AES, ChaCha20) быстрое и эффективное для больших "
            "объёмов данных. Асимметричное шифрование (RSA, ECC) использует пару ключей: "
            "открытый ключ для шифрования и закрытый для расшифровки. Асимметричное "
            "шифрование медленнее, поэтому на практике используется гибридная схема: "
            "асимметричное шифрование для обмена симметричным ключом (TLS/SSL), "
            "а симметричное — для шифрования самого трафика. Размер ключа важен: "
            "AES-128 обеспечивает достаточную стойкость, RSA-2048 — минимальный "
            "рекомендуемый размер. Квантовые компьютеры угрожают RSA и ECC, "
            "но не AES с достаточно длинным ключом."
        ),
    ),
    (
        "Электронная цифровая подпись",
        "Криптография",
        (
            "Электронная цифровая подпись (ЭЦП) — это криптографический механизм, "
            "обеспечивающий подлинность и целостность документа. ЭЦП использует "
            "асимметричную криптографию: отправитель подписывает хеш документа своим "
            "закрытым ключом, получатель проверяет подпись открытым ключом отправителя. "
            "Популярные алгоритмы: RSA-PSS, ECDSA, EdDSA. Хеш-функции (SHA-256, SHA-3) "
            "гарантируют, что изменение документа меняет хеш, и подпись становится "
            "недействительной. ЭЦП не шифрует документ — она только подтверждает, "
            "что документ подписан конкретным лицом и не был изменён после подписания. "
            "Для юридической значимости ЭЦП в России используется ГОСТ Р 34.10."
        ),
    ),
    # ── Веб (2) ────────────────────────────────────────────────────
    (
        "REST API: принципы проектирования",
        "Веб-технологии",
        (
            "REST (Representational State Transfer) — архитектурный стиль для "
            "проектирования веб-API. Основные принципы: ресурсы идентифицируются "
            "через URL, для операций используются HTTP методы (GET — чтение, "
            "POST — создание, PUT — полное обновление, PATCH — частичное обновление, "
            "DELETE — удаление), ответы в JSON или XML, отсутствие состояния на "
            "сервере (stateless). Хороший REST API использует множественное число "
            "для ресурсов (/users, /orders), поддерживает фильтрацию через query "
            "параметры (?status=active), возвращает осмысленные HTTP коды (200 OK, "
            "201 Created, 400 Bad Request, 404 Not Found, 500 Internal Server Error). "
            "Версионирование API (/v1/users) позволяет вносить изменения без "
            "поломки существующих клиентов."
        ),
    ),
    (
        "HTTP: протокол передачи гипертекста",
        "Веб-технологии",
        (
            "HTTP (HyperText Transfer Protocol) — основной протокол передачи данных "
            "в вебе. HTTP — протокол прикладного уровня, работающий поверх TCP. "
            "Запрос состоит из метода (GET, POST, PUT, DELETE), URL, заголовков "
            "и тела. Ответ состоит из статус-кода, заголовков и тела. HTTP/1.1 "
            "ввёл keep-alive для переиспользования соединений. HTTP/2 добавил "
            "мультиплексирование (несколько запросов по одному соединению) и "
            "сжатие заголовков. HTTP/3 работает поверх QUIC (UDP) для снижения "
            "задержек. HTTPS — HTTP поверх TLS — обязателен для современных "
            "веб-приложений. Кэширование управляется заголовками Cache-Control, "
            "ETag и Last-Modified."
        ),
    ),
]


def _normalize_query(query: str) -> str:
    """Привести запрос к единому виду."""
    return query.strip()


def evaluate_retrieval(
    pipeline: Any,
    documents: Optional[List[Tuple[str, str, str]]] = None,
    queries: Optional[List[Tuple[str, List[str], Optional[str]]]] = None,
) -> Dict[str, Any]:
    """Оценить качество поиска RAG-пайплайна.

    Args:
        pipeline: RAGPipeline с методами search_documents(query, discipline_id, limit)
        documents: список (title, discipline_name, content) — если None, использует GOLDEN_DOCUMENTS
        queries: список (query, [expected_doc_titles], discipline_or_None) — если None, GOLDEN_QUERIES

    Returns:
        dict с метриками: recall@1, recall@3, recall@5, mrr,
        per_query результаты, total_docs_imported, запросы без документов
    """
    docs = documents or GOLDEN_DOCUMENTS
    qs = queries or GOLDEN_QUERIES

    # Строим маппинг: title -> набор discipline_id'ов
    # Поскольку discipline_name может повторяться, найдём документы по title
    known_titles: Dict[str, str] = {d[0]: d[1] for d in docs}

    results: List[Dict[str, Any]] = []
    found_any = 0
    total = 0

    for query_text, expected_titles, discipline_filter in qs:
        total += 1
        query_text = _normalize_query(query_text)

        try:
            search_results = pipeline.search_documents(
                query=query_text,
                discipline_id=discipline_filter,
                limit=5,
            )
        except Exception as exc:
            logger.warning("Search failed for query %r: %s", query_text, exc)
            results.append({
                "query": query_text,
                "expected": expected_titles,
                "discipline": discipline_filter,
                "found_titles": [],
                "found_count": 0,
                "recall@1": 0.0,
                "recall@3": 0.0,
                "recall@5": 0.0,
                "mrr": 0.0,
                "error": str(exc),
            })
            continue

        # Какие названия документов вернулись в top-5
        result_titles: List[str] = []
        for sr in search_results:
            if sr.document_title not in result_titles:
                result_titles.append(sr.document_title)

        # Проверяем, какие ожидаемые документы нашлись
        hits_at_k: Dict[int, int] = {1: 0, 3: 0, 5: 0}
        first_rank: Optional[int] = None

        for expected in expected_titles:
            if expected in known_titles:
                try:
                    rank = result_titles.index(expected)
                    # rank = 0 → top-1, rank < 3 → top-3, rank < 5 → top-5
                    for k in [1, 3, 5]:
                        if rank < k:
                            hits_at_k[k] += 1
                    if first_rank is None or rank < first_rank:
                        first_rank = rank + 1  # 1-based
                except ValueError:
                    pass

        n_expected = len([t for t in expected_titles if t in known_titles])
        n_found = len(result_titles)

        recall_1 = hits_at_k[1] / n_expected if n_expected > 0 else 0.0
        recall_3 = hits_at_k[3] / n_expected if n_expected > 0 else 0.0
        recall_5 = hits_at_k[5] / n_expected if n_expected > 0 else 0.0
        mrr_q = 1.0 / first_rank if first_rank is not None else 0.0

        if n_found > 0:
            found_any += 1

        results.append({
            "query": query_text,
            "expected": expected_titles,
            "found_titles": result_titles,
            "found_count": n_found,
            "recall@1": recall_1,
            "recall@3": recall_3,
            "recall@5": recall_5,
            "mrr": mrr_q,
            "discipline": discipline_filter,
        })

    # Агрегированные метрики
    if total == 0:
        return {
            "recall@1": 0.0,
            "recall@3": 0.0,
            "recall@5": 0.0,
            "mrr": 0.0,
            "per_query": [],
            "total_queries": 0,
            "queries_with_results": 0,
            "info": "No queries evaluated",
        }

    recall_1_avg = sum(r["recall@1"] for r in results) / total
    recall_3_avg = sum(r["recall@3"] for r in results) / total
    recall_5_avg = sum(r["recall@5"] for r in results) / total
    mrr_avg = sum(r["mrr"] for r in results) / total

    return {
        "recall@1": round(recall_1_avg, 4),
        "recall@3": round(recall_3_avg, 4),
        "recall@5": round(recall_5_avg, 4),
        "mrr": round(mrr_avg, 4),
        "per_query": results,
        "total_queries": total,
        "queries_with_results": sum(1 for r in results if r["found_count"] > 0),
    }


def print_report(
    metrics: Dict[str, Any],
    baseline: Optional[Dict[str, Any]] = None,
    file=sys.stdout,
) -> None:
    """Вывести читаемый отчёт о качестве поиска.

    Если baseline передан — показывает diff (зелёным/красным).
    """
    header = "╔══════════════════════════════════════════════╗\n"
    header += "║     RAG Search Quality Benchmark Report     ║\n"
    header += "╚══════════════════════════════════════════════╝"
    print(header, file=file)
    print(f"  Запросов: {metrics['total_queries']}", file=file)
    print(f"  Запросы с результатами: {metrics['queries_with_results']}", file=file)
    print(file=file)

    for metric in ["recall@1", "recall@3", "recall@5", "mrr"]:
        value = metrics[metric]
        if baseline and metric in baseline:
            delta = value - baseline[metric]
            delta_str = f"  ({'+' if delta > 0 else ''}{delta:.4f})"
        else:
            delta_str = ""
        bar = "█" * int(value * 40) + "░" * (40 - int(value * 40))
        print(f"  {metric:>8} │ {bar} │ {value:.4f}{delta_str}", file=file)

    print(file=file)
    print("  ── Детали по запросам ──", file=file)
    for r in metrics["per_query"]:
        err_mark = " ⚠️" if r.get("error") else ""
        print(
            f"  • {r['query'][:60]:<60}"
            f"  R@5={r['recall@5']:.2f}  MRR={r['mrr']:.2f}{err_mark}",
            file=file,
        )

    if baseline:
        print(file=file)
        print("  Зелёный = лучше baseline, красный = хуже", file=file)


def load_baseline() -> Optional[Dict[str, Any]]:
    """Загрузить baseline из файла."""
    if BASELINE_PATH.exists():
        try:
            return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load baseline: %s", exc)
    return None


def save_baseline(metrics: Dict[str, Any]) -> None:
    """Сохранить метрики как baseline."""
    # Сохраняем только агрегированные метрики + метаданные
    baseline = {
        "recall@1": metrics["recall@1"],
        "recall@3": metrics["recall@3"],
        "recall@5": metrics["recall@5"],
        "mrr": metrics["mrr"],
        "total_queries": metrics["total_queries"],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "per_query_summary": [
            {"query": r["query"][:60], "recall@5": r["recall@5"], "mrr": r["mrr"]}
            for r in metrics["per_query"]
        ],
    }
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Baseline saved to %s", BASELINE_PATH)


def get_discipline_id_map(disciplines: List[Any]) -> Dict[str, str]:
    """Получить маппинг название_дисциплины → её ID из списка Entity.

    Args:
        disciplines: список Entity с полями name и id

    Returns:
        dict {discipline_name: discipline_id}
    """
    result = {}
    for d in disciplines:
        if hasattr(d, "name") and hasattr(d, "id"):
            result[d.name] = d.id
        elif isinstance(d, dict):
            result.get(d.get("name", ""), d.get("id", ""))
    return result


def create_golden_documents(
    tmp_dir: Path,
    discipline_map: Optional[Dict[str, str]] = None,
) -> List[Tuple[str, str, str, Optional[str]]]:
    """Создать файлы золотых документов во временной директории.

    Args:
        tmp_dir: директория для файлов
        discipline_map: {discipline_name: discipline_id} — если None, discipline_id = None

    Returns:
        список (title, filepath, content, discipline_id)
    """
    if discipline_map is None:
        discipline_map = {}

    created: List[Tuple[str, str, str, Optional[str]]] = []
    for title, disc_name, content in GOLDEN_DOCUMENTS:
        disc_id = discipline_map.get(disc_name)
        # Транслитерируем название в имя файла
        safe_name = title.lower().replace(" ", "_").replace("(", "").replace(")", "")
        safe_name = "".join(c for c in safe_name if c.isascii() or c == "_")
        fname = f"{safe_name[:60]}.txt"
        fpath = tmp_dir / fname
        fpath.write_text(
            f"# {title}\n\nДисциплина: {disc_name}\n\n{content}",
            encoding="utf-8",
        )
        created.append((title, str(fpath), content, disc_id))
    return created


def import_golden_documents(
    pipeline: Any,
    tmp_dir: Path,
    documents: Optional[List[Tuple[str, str, str]]] = None,
) -> int:
    """Импортировать золотые документы через pipeline.

    Каждый документ создаётся как .txt файл, затем импортируется
    через pipeline.import_document().

    Returns:
        количество импортированных документов
    """
    docs = documents or GOLDEN_DOCUMENTS
    imported = 0

    for title, disc_name, content in docs:
        safe_name = title.lower().replace(" ", "_").replace("(", "").replace(")", "")
        safe_name = "".join(c for c in safe_name if c.isascii() or c == "_")
        fname = f"{safe_name[:60]}.txt"
        fpath = tmp_dir / fname

        if not fpath.exists():
            fpath.write_text(
                f"# {title}\n\nДисциплина: {disc_name}\n\n{content}",
                encoding="utf-8",
            )

        try:
            result = pipeline.import_document(
                path=str(fpath),
                title=title,
            )
            imported += 1
            logger.debug("Imported %s: %d chunks", title, result.chunks_count)
        except Exception as exc:
            logger.warning("Failed to import %s: %s", title, exc)

    return imported


# ── Золотые запросы ──────────────────────────────────────────────────
# (query, [expected_doc_titles], discipline_filter_or_None)

GOLDEN_QUERIES: List[Tuple[str, List[str], Optional[str]]] = [
    # ── Алгоритмы (4) ─────────────────────────────────────────────
    (
        "Как работает быстрая сортировка?",
        ["Быстрая сортировка (Quick Sort)"],
        None,
    ),
    (
        "Сложность quicksort O n log n",
        ["Быстрая сортировка (Quick Sort)"],
        None,
    ),
    (
        "Как найти элемент в отсортированном массиве?",
        ["Бинарный поиск в отсортированном массиве"],
        None,
    ),
    (
        "Бинарный поиск vs линейный поиск сложность",
        ["Бинарный поиск в отсортированном массиве"],
        None,
    ),
    # ── Базы данных (4) ──────────────────────────────────────────
    (
        "Чем отличается INNER JOIN от LEFT JOIN?",
        ["SQL JOIN: типы и применение"],
        None,
    ),
    (
        "Какие бывают типы соединения таблиц в SQL?",
        ["SQL JOIN: типы и применение"],
        None,
    ),
    (
        "Как работают B-tree индексы в базах данных?",
        ["Индексы в базах данных: B-деревья и хеш-таблицы"],
        None,
    ),
    (
        "Когда использовать хеш-индекс, а когда B-дерево?",
        ["Индексы в базах данных: B-деревья и хеш-таблицы"],
        None,
    ),
    # ── Сети (4) ─────────────────────────────────────────────────
    (
        "Как TCP обеспечивает надёжную доставку данных?",
        ["Протокол TCP: надёжная передача данных"],
        None,
    ),
    (
        "Тройное рукопожатие TCP SYN ACK",
        ["Протокол TCP: надёжная передача данных"],
        None,
    ),
    (
        "Как работает DNS преобразование домена в IP?",
        ["DNS: система доменных имён"],
        None,
    ),
    (
        "Типы DNS записей A AAAA MX CNAME",
        ["DNS: система доменных имён"],
        None,
    ),
    # ── ML (4) ───────────────────────────────────────────────────
    (
        "Что такое деревья решений и как их обучить?",
        ["Деревья решений в машинном обучении"],
        None,
    ),
    (
        "Как бороться с переобучением деревьев решений?",
        ["Деревья решений в машинном обучении"],
        None,
    ),
    (
        "Из чего состоит нейронная сеть?",
        ["Нейронные сети: основы и архитектура"],
        None,
    ),
    (
        "Обратное распространение ошибки backpropagation",
        ["Нейронные сети: основы и архитектура"],
        None,
    ),
    # ── Криптография (4) ─────────────────────────────────────────
    (
        "В чем отличие симметричного от асимметричного шифрования?",
        ["Симметричное и асимметричное шифрование"],
        None,
    ),
    (
        "Гибридная схема шифрования TLS",
        ["Симметричное и асимметричное шифрование"],
        None,
    ),
    (
        "Как работает электронная цифровая подпись?",
        ["Электронная цифровая подпись"],
        None,
    ),
    (
        "ЭЦП ECDSA EdDSA проверка подписи",
        ["Электронная цифровая подпись"],
        None,
    ),
    # ── Веб-технологии (3) ───────────────────────────────────────
    (
        "Принципы REST API методы HTTP",
        ["REST API: принципы проектирования"],
        None,
    ),
    (
        "Что такое HTTP и какие есть версии?",
        ["HTTP: протокол передачи гипертекста"],
        None,
    ),
    (
        "HTTPS TLS кэширование Cache-Control",
        ["HTTP: протокол передачи гипертекст��"],
        None,
    ),
    # ── Мульти-документные запросы (3) ───────────────────────────
    (
        "Алгоритмы сортировки и поиска в программировании",
        ["Быстрая сортировка (Quick Sort)", "Бинарный поиск в отсортированном массиве"],
        None,
    ),
    (
        "Архитектура веб-приложений: протоколы и API",
        ["HTTP: протокол передачи гипертекста", "REST API: принципы проектирования"],
        None,
    ),
    (
        "Криптография: защита данных и проверка подлинности",
        ["Симметричное и асимметричное шифрование", "Электронная цифровая подпись"],
        None,
    ),
    # ── Краевые случаи (1) ───────────────────────────────────────
    (
        "qsort partition hoare lomuto",
        ["Быстрая сортировка (Quick Sort)"],
        None,
    ),
]


if __name__ == "__main__":
    # Справка
    print("Модуль golden_qa — импортируй в тестах, не запускай напрямую.")
    print(f"Датасет: {len(GOLDEN_DOCUMENTS)} документов, {len(GOLDEN_QUERIES)} запросов.")
    print(f"Baseline: {BASELINE_PATH}")
