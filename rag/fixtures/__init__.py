"""rag.fixtures — CLI-инструменты для RAG-сервиса.

Раньше жили в отдельной директории fixtures/. Переехали сюда, потому что:

  - ``rag.fixtures.cli_docgen`` (бывший ``agent-generate``) генерирует
    документы и индексирует их через rag-сервис — это часть жизненного
    цикла документов, которым владеет rag.

  - ``rag.fixtures.cli_ingest`` (бывший ``agent-ingest``) — CLI для
    импорта/поиска/удаления документов в rag-индексе. По смыслу это
    обёртка над :class:`agent_tutor_sdk.rag.client.RagClientSync`, но
    жить ей естественнее рядом с сервисом, а не в SDK (SDK — библиотека,
    не место для CLI-парсеров).

  - ``document_generator`` использует :class:`rag.fixtures.rag_tools.RagTools`
    — тонкую HTTP-обёртку над ``RagClientSync``, сохраняющую старый
    контракт ``pipeline.repository/chunker/vector_store`` для совместимости.
    Если когда-нибудь захочется убрать шим — document_generator можно
    переписать на прямые вызовы ``RagClientSync`` без потери функциональности.

Эта директория не зависит от ``fixtures/`` (старого одноимённого пакета) и
наоборот — старый ``fixtures/`` остаётся для тех, кто ещё использует его
entrypoints (``agent-ingest``, ``agent-generate``, ``agent-seedgen``).
При желании старый пакет можно удалить.
"""
