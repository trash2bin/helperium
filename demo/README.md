# demo/ — демо-сценарии и внешние интеграции

Каталог `demo/` содержит демонстрационные/тестовые материалы для helperium.
Здесь НЕ живёт production-код — это витрина интеграций и реалистичных данных.

## Структура

```
demo/
├── autoparts-store/     ← FOREIGN: копия внешнего Django-магазина автозапчастей
│                         (реалистичная БД для бенчей/демо, 1.7M товаров)
│                         см. demo/autoparts-store/README.foreign.md
├── web/                 ← dev-only reverse proxy (demo/web, :8080)
└── tests/               ← юнит-тесты demo
```

## autoparts-store (внешний проект, не модифицировать)

- **Что это:** Django 5 / PostgreSQL 16 интернет-магазин автозапчастей.
- **Зачем в helperium:** реалистичная, ветвистая БД (дерево категорий, FK,
  JSONB `car_applicability`/`characteristics`, 1.7M товаров, 478K заказов) —
  чтобы бенчи и демо гонялись на данных, похожих на боевые.
- **Чужеродность:** это автономный проект. Код helperium **не импортирует** его.
  Интеграция — только через PostgreSQL (data-service подключается к его БД как tenant).
- **Обновление:** перекопировать из исходного репозитория клиента,
  см. `demo/autoparts-store/README.foreign.md`.

### Локальный запуск полного demo-контура

Стандартный `./infra/scripts/dev.sh start` поднимает только Helperium и
встроенный `demo/web` на `:8080`. Чтобы в рамках ручной демки дополнительно
запустить независимый storefront на `:8000`, передай явный opt-in флаг:

```bash
./infra/scripts/dev.sh start --with-autoparts
```

Флаг вызывает `docker-compose up -d` только в `demo/autoparts-store`. Он не
запускается по умолчанию и не включается в `./infra/scripts/dev.sh stop`. В
этом явном режиме launcher также выполняет onboarding storefront PostgreSQL
как read-only tenant и включает HTML-встраивание виджета: storefront на
`:8000` загружает `/embed/embed.js` и вызывает host-published API на `:8081`.
