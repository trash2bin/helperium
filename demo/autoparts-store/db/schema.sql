-- ============================================================================
-- AutoParts Store — фиксированная DDL-схема (PostgreSQL 16)
-- ============================================================================
-- Сгенерировано из catalog/migrations/0001_initial.py (Django 5.0.14, 2026-07-12).
-- Назначение: поднять чистую PG-базу БЕЗ Django (для data-service / бенча),
-- и версионировать схему как "чужого" проекта (demo/autoparts-store).
-- Не редактировать вручную — обновлять из миграций исходника.
-- ============================================================================

-- Бренды (производители запчастей) -----------------------------------------
CREATE TABLE catalog_brand (
    id           BIGSERIAL PRIMARY KEY,
    name         VARCHAR(200) NOT NULL,
    slug         VARCHAR(200) NOT NULL UNIQUE,
    country      VARCHAR(100) NOT NULL DEFAULT '',
    logo         VARCHAR(100) NULL,
    description  TEXT NOT NULL DEFAULT '',
    founded_year INTEGER NULL,
    website      VARCHAR(200) NOT NULL DEFAULT '',
    is_oem       BOOLEAN NOT NULL DEFAULT FALSE,
    ordering     INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL
);

-- Категории (дерево: parent_id → self) --------------------------------------
CREATE TABLE catalog_category (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    slug        VARCHAR(200) NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    image       VARCHAR(100) NULL,
    parent_id   BIGINT NULL REFERENCES catalog_category(id) ON DELETE SET NULL,
    icon        VARCHAR(50) NOT NULL DEFAULT '',
    ordering    INTEGER NOT NULL DEFAULT 0,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX catalog_category_parent_id_idx ON catalog_category(parent_id);

-- Товары (автозапчасти) -----------------------------------------------------
CREATE TABLE catalog_product (
    id                  BIGSERIAL PRIMARY KEY,
    article             VARCHAR(50) NOT NULL UNIQUE,
    name                VARCHAR(300) NOT NULL,
    slug                VARCHAR(300) NOT NULL UNIQUE,
    brand_id            BIGINT NOT NULL REFERENCES catalog_brand(id) ON DELETE CASCADE,
    category_id         BIGINT NOT NULL REFERENCES catalog_category(id) ON DELETE CASCADE,
    oem_number          VARCHAR(100) NOT NULL DEFAULT '',
    price               NUMERIC(10,2) NOT NULL,
    old_price           NUMERIC(10,2) NULL,
    quantity            INTEGER NOT NULL DEFAULT 0,
    is_available        BOOLEAN NOT NULL DEFAULT FALSE,
    description         TEXT NOT NULL DEFAULT '',
    short_description   VARCHAR(300) NOT NULL DEFAULT '',
    characteristics     JSONB NOT NULL DEFAULT '{}',
    car_applicability   JSONB NOT NULL DEFAULT '[]',
    weight_kg           NUMERIC(8,2) NULL,
    dimensions          VARCHAR(100) NOT NULL DEFAULT '',
    image               VARCHAR(100) NULL,
    image_extra         JSONB NULL,
    is_popular          BOOLEAN NOT NULL DEFAULT FALSE,
    is_new              BOOLEAN NOT NULL DEFAULT FALSE,
    is_bestseller       BOOLEAN NOT NULL DEFAULT FALSE,
    is_promo            BOOLEAN NOT NULL DEFAULT FALSE,
    label               VARCHAR(50) NOT NULL DEFAULT 'none',
    warranty_months     INTEGER NOT NULL DEFAULT 12,
    supplier            VARCHAR(200) NOT NULL DEFAULT '',
    country_of_origin   VARCHAR(100) NOT NULL DEFAULT '',
    seo_title           VARCHAR(200) NOT NULL DEFAULT '',
    seo_description     TEXT NOT NULL DEFAULT '',
    views_count         INTEGER NOT NULL DEFAULT 0,
    ordering            INTEGER NOT NULL DEFAULT 0,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL
);
CREATE INDEX catalog_product_article_idx ON catalog_product(article);
CREATE INDEX catalog_product_brand_category_idx ON catalog_product(brand_id, category_id);
CREATE INDEX catalog_product_active_avail_idx ON catalog_product(is_active, is_available);

-- Корзины (сессионные) ------------------------------------------------------
CREATE TABLE catalog_cart (
    id           BIGSERIAL PRIMARY KEY,
    session_key  VARCHAR(40) NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL
);

-- Позиции корзины -----------------------------------------------------------
CREATE TABLE catalog_cartitem (
    id         BIGSERIAL PRIMARY KEY,
    cart_id    BIGINT NOT NULL REFERENCES catalog_cart(id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL REFERENCES catalog_product(id) ON DELETE CASCADE,
    quantity   INTEGER NOT NULL DEFAULT 1,
    price      NUMERIC(10,2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX catalog_cartitem_cart_id_idx ON catalog_cartitem(cart_id);

-- Заказы --------------------------------------------------------------------
CREATE TABLE catalog_order (
    id              BIGSERIAL PRIMARY KEY,
    order_number    VARCHAR(20) NOT NULL UNIQUE,
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    patronymic      VARCHAR(100) NOT NULL DEFAULT '',
    phone           VARCHAR(20) NOT NULL,
    email           VARCHAR(254) NOT NULL,
    city            VARCHAR(100) NOT NULL DEFAULT 'Москва',
    address         TEXT NOT NULL,
    comment         TEXT NOT NULL DEFAULT '',
    delivery_method VARCHAR(20) NOT NULL DEFAULT 'courier',
    payment_method  VARCHAR(20) NOT NULL DEFAULT 'cash',
    status          VARCHAR(20) NOT NULL DEFAULT 'new',
    items           JSONB NOT NULL DEFAULT '[]',
    subtotal        NUMERIC(12,2) NOT NULL DEFAULT 0,
    delivery_cost   NUMERIC(10,2) NOT NULL DEFAULT 0,
    total           NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX catalog_order_created_at_idx ON catalog_order(created_at DESC);

-- Настройки сайта (синглтон pk=1) -------------------------------------------
CREATE TABLE catalog_sitesettings (
    id            BIGSERIAL PRIMARY KEY,
    site_name     VARCHAR(200) NOT NULL DEFAULT 'АвтоЗапчасти',
    phone         VARCHAR(20) NOT NULL DEFAULT '+7 (495) 123-45-67',
    email         VARCHAR(254) NOT NULL DEFAULT 'info@autoparts.ru',
    address       TEXT NOT NULL DEFAULT '',
    work_hours    VARCHAR(200) NOT NULL DEFAULT '',
    delivery_info TEXT NOT NULL DEFAULT '',
    about_text    TEXT NOT NULL DEFAULT '',
    telegram      VARCHAR(200) NOT NULL DEFAULT '',
    whatsapp      VARCHAR(20) NOT NULL DEFAULT '',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE
);

-- Справка по enum-значениям (Django choices) --------------------------------
-- catalog_product.label:       none | hit | new | sale | promo
-- catalog_order.delivery_method: pickup | courier | russian_post
-- catalog_order.payment_method:  cash | card | online
-- catalog_order.status:          new | confirmed | processing | shipped | delivered | cancelled
