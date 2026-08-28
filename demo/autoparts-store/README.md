# ⚙️ АвтоЗапчасти24 — Интернет-магазин автозапчастей

Добро пожаловать! Это тестовый сайт интернет-магазина автозапчастей, полностью готовый к использованию. Сайт уже запущен и работает.

## 🌐 Как зайти

Сайт открывается в браузере по адресу:

```
http://localhost:8000
```

Админка (управление товарами, заказами):

```
http://localhost:8000/admin/
```

---

## 🏪 Как выглядит сайт

Сайт сделан как настоящий интернет-магазин автозапчастей для небольшого бизнеса. Вот что на нём есть:

**Главная страница** — встречает покупателя большим баннером, показывает преимущества магазина (быстрая доставка, гарантия, лучшие цены, лёгкий возврат), список всех категорий запчастей, популярные товары и бренды.

**Каталог** — все товары с фильтрами по категориям и наличию на складе. Можно сортировать по цене, новизне, популярности. Есть пагинация (разбивка по страницам).

**Категории** — у каждой категории есть подкатегории. Например, заходишь в «Тормозную систему» — а там «Колодки тормозные», «Диски тормозные», «Суппорта» и т.д.

**Карточка товара** — подробная информация: цена (со скидкой если есть), описание, характеристики (например материал, размеры), список под какие автомобили подходит, гарантия, остаток на складе. Можно сразу добавить в корзину.

**Поиск** — ищет по названию, артикулу, OEM-номеру и названию бренда.

**Бренды** — страница со всеми производителями (Bosch, TRW, NGK, Brembo, Febi и ещё 50+).

**Корзина** — можно добавлять/удалять товары, менять количество, видеть итоговую сумму.

**Оформление заказа** — форма с фамилией, телефоном, адресом, выбором доставки (курьер, самовывоз, почта) и оплаты (наличные, карта, онлайн).

**О компании и Контакты** — страницы-визитки с информацией о магазине и реквизитами.

---

## 📦 Что в базе данных

База данных наполнена так, будто магазин проработал несколько лет. Вот сколько всего:

| Что | Сколько |
|---|---|
| **Производители (бренды)** | **61** — Bosch, TRW, NGK, Denso, Brembo, Febi, KYB, Sachs, SKF, Mann-Filter, Castrol, Mobil и ещё 40+ |
| **Категории** | **163** — тормозная система, двигатель, подвеска, электрика, кузов, трансмиссия, выхлоп, охлаждение, рулевое, масла, фильтры, ремни — и у каждой есть подкатегории |
| **Товары** | **1 705 000+** — самые разные запчасти на любой вкус |
| **Заказы** | **478 000+** — уже оформленные заказы с разными статусами |
| **Корзины** | **51 500** — брошенные корзины покупателей |

### Как устроены товары

У каждого товара есть:
- **Название** — например «Тормозные колодки передние Bosch для BMW E46»
- **Артикул** — уникальный номер в системе
- **Цена** — от 200 до 25 000 рублей, некоторые со скидкой
- **Остаток на складе** — сколько штук в наличии
- **Производитель** — Bosch, TRW, Febi и т.д.
- **Категория** — к какой части авто относится
- **Описание** — подробный текст про деталь
- **Характеристики** — размеры, материал, диаметр, тип и т.д.
- **Применимость** — под какие машины подходит (BMW E46, Mercedes W211, Toyota Camry и т.д.)
- **Гарантия** — от 6 до 60 месяцев
- **Страна производства** — Германия, Япония, США, Италия и др.
- **Метки** — «Хит», «Новинка», «Распродажа», «Акция»

Товары распределены по категориям логично. Например «Фильтр масляный Bosch для Audi A4» лежит в категории «Масляные фильтры», которая входит в «Фильтры».

### Какие есть заказы

Заказы реалистичные:
- Разные статусы: новые, подтверждённые, в обработке, отправленные, доставленные, отменённые
- Разные города: Москва, Питер, Казань, Новосибирск, Краснодар, Владивосток и другие
- В каждом заказе от 1 до 6 товаров
- Способ доставки: курьер (чаще всего), самовывоз или почта
- Оплата: онлайн (чаще всего), картой при получении или наличными

---

## 💻 Как это технически устроено (коротко)

Сайт работает в двух контейнерах:

1. **PostgreSQL 16** — база данных (хранит все товары, заказы, корзины)
2. **Django 5.0 + Python** — сам сайт (показывает страницы, обрабатывает корзину)

Оба контейнера лёгкие на Alpine Linux — минимальный размер, не жрут ресурсы.

---

## 🚀 Быстрый старт

Storefront всегда работает своей writer-ролью, но Helperium получает отдельный
PostgreSQL login с `SELECT`-only grants. Перед первым запуском задай пароль этой
отдельной роли; это **не** `STORE_DB_PASSWORD`:

```bash
# Зайти в папку storefront и создать его отдельное development environment.
cd /path/to/auto-parts-store
cp .env.dev.example .env
# Замени оба placeholder password значения в .env на уникальные локальные secrets.

# Миграции и seed выполняются, затем one-shot bootstrap создаёт или обновляет
# helperium_autoparts_ro и только после этого стартует storefront.
docker-compose up -d
```

Сайт появится на http://localhost:8000. Bootstrap не запускает Helperium core в
standalone режиме, но уже гарантирует, что отдельная роль данных не имеет прав
`INSERT`, `UPDATE` или `DELETE`.

Для native Helperium + storefront используй один явный запуск из корня проекта:

```bash
# Один раз: создай demo/autoparts-store/.env из .env.dev.example и задай
# STORE_DB_PASSWORD и HELPERIUM_AUTOPARTS_RO_PASSWORD.
# В root .env должен быть задан ADMIN_TOKEN для authenticated data-service API.
./scripts/dev.sh start --with-autoparts
```

Этот путь запускает data-service первым, затем idempotent bootstrap создаёт роль,
регистрирует tenant `autoparts` через authenticated admin API, генерирует его
manifest и только потом поднимает MCP/API. Direct chat получает
`DEFAULT_TENANT_ID=autoparts` только в этом explicit opt-in режиме.

Если нужно перезаполнить базу (сбросить всё и заново):

```bash
# Остановить и удалить контейнеры
docker-compose down

# Запустить заново (автоматически наполнит базу)
docker-compose up -d
```

Если нужно догенерировать ещё данных (уже работает):

```bash
docker-compose exec -T web python manage.py seed_massive --products 300000 --orders 50000
```

---

## 🧪 Для чего это всё

Этот проект сделан для тестирования — чтобы было на чём гонять свои инструменты, не трогая реальные боевые базы. База специально набита большим объёмом данных (почти 3 ГБ), чтобы было на чём проверять производительность.


---

## Public HTTPS demo

`docker-compose.public.yml` intentionally publishes only Caddy on ports `80` and
`443`. It connects to the existing Helperium core through the external
`helperium_bridge` network; it does not start, publish, or manage `mcp-gateway`
or `data-service`.

Before enabling the widget on a public domain, copy both
`.env.public.example` and `helperium-core.public.env.example` into their
respective deployment environments and replace every placeholder. In particular,
set `CORS_ALLOW_ORIGINS` to the exact `https://<DEMO_DOMAIN>` origin, keep
`MCP_REQUIRE_AUTH=true` with matching non-empty `MCP_API_KEY` and
`MCP_CLIENT_API_KEY`, and do not expose ports `8083` or `8084` through a proxy
or host mapping.

Note the intentional `HELPERIUM_WIDGET_ENABLED` default drift: this example
keeps the safe opt-in `false`, while `docker-compose.public.yml` defaults to
`true` because the public storefront ships with the widget on once its own
tenant bootstrap succeeds. Keep the example value until the public tenant,
agent and embed origins are actually configured.

The public Compose bootstrap is mandatory: after migrations and seed, it creates
or rotates `helperium_autoparts_ro`, grants only `CONNECT`, schema `USAGE` and
`SELECT` on the seven catalog tables, and registers/re-writes tenant `autoparts`
through the private `helperium_bridge` network. Set
`HELPERIUM_AUTOPARTS_RO_PASSWORD`, `HELPERIUM_DATA_SERVICE_URL` and
`HELPERIUM_DATA_ADMIN_TOKEN` in `.env.public`; the latter must equal the core
`ADMIN_TOKEN`. The storefront does not start unless this one-shot bootstrap
succeeds. PostgreSQL remains unpublished in public Compose.
