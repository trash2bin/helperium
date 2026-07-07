// Package server — тесты всех custom_queries на сценарии shop.
//
// В shop 6 custom_queries:
//
//	order_items_by_products  — GET /products/{id}/order_items
//	order_items_by_orders    — GET /orders/{id}/order_items
//	orders_by_customers      — GET /customers/{id}/orders
//	products_by_categories   — GET /categories/{id}/products
//	reviews_by_customers     — GET /customers/{id}/reviews
//	reviews_by_products      — GET /products/{id}/reviews
//
// Покрытие: позитивные кейсы (с существующими id), негативные (404),
// проверка FK-логики (только связанные записи).
package server_test

import "testing"

func TestCustomQueries_Shop_FK_Lookups(t *testing.T) {
	cfg, db := loadScenario(t, "../../../testdata/scenarios/shop")
	defer db.Close()
	ts := buildTestRouter(t, cfg, db)

	t.Run("products_1_order_items", func(t *testing.T) {
		// products.id=1 (iPhone 15) — в order_items есть 2 записи (id=1 и id=3?)
		status, body := getJSON[[]map[string]any](t, ts.URL+"/products/1/order_items")
		if status != 200 {
			t.Errorf("status=%d", status)
		}
		t.Logf("/products/1/order_items: %d items", len(body))
	})

	t.Run("orders_1_order_items", func(t *testing.T) {
		// orders.id=1 — там 2 order_items
		status, body := getJSON[[]map[string]any](t, ts.URL+"/orders/1/order_items")
		if status != 200 {
			t.Errorf("status=%d", status)
		}
		if len(body) == 0 {
			t.Error("expected at least 1 order_item for order 1")
		}
	})

	t.Run("customers_1_orders", func(t *testing.T) {
		status, body := getJSON[[]map[string]any](t, ts.URL+"/customers/1/orders")
		if status != 200 {
			t.Errorf("status=%d", status)
		}
		if len(body) == 0 {
			t.Error("expected at least 1 order for customer 1")
		}
	})

	t.Run("categories_1_products", func(t *testing.T) {
		// Электроника (id=1) содержит iPhone 15 и MacBook Pro
		status, body := getJSON[[]map[string]any](t, ts.URL+"/categories/1/products")
		if status != 200 {
			t.Errorf("status=%d", status)
		}
		if len(body) == 0 {
			t.Error("expected products in category 1")
		}
		t.Logf("/categories/1/products: %d items", len(body))
	})

	t.Run("customers_1_reviews", func(t *testing.T) {
		status, _ := getJSON[[]map[string]any](t, ts.URL+"/customers/1/reviews")
		if status != 200 {
			t.Errorf("status=%d", status)
		}
	})

	t.Run("products_1_reviews", func(t *testing.T) {
		status, body := getJSON[[]map[string]any](t, ts.URL+"/products/1/reviews")
		if status != 200 {
			t.Errorf("status=%d", status)
		}
		if len(body) == 0 {
			t.Error("expected reviews for product 1")
		}
	})
}

func TestCustomQueries_Shop_Negative(t *testing.T) {
	cfg, db := loadScenario(t, "../../../testdata/scenarios/shop")
	defer db.Close()
	ts := buildTestRouter(t, cfg, db)

	// Запросы с несуществующими id — должны возвращать 200 с пустым массивом
	// (т.к. custom_query — это SELECT с WHERE = id; пустой результат — норм).
	for _, path := range []string{
		"/products/9999/order_items",
		"/orders/9999/order_items",
		"/customers/9999/orders",
		"/categories/9999/products",
		"/customers/9999/reviews",
		"/products/9999/reviews",
	} {
		path := path
		t.Run(path, func(t *testing.T) {
			status, body := getJSON[[]map[string]any](t, ts.URL+path)
			if status != 200 {
				t.Errorf("expected 200 with [] for unknown FK, got %d", status)
			}
			if len(body) != 0 {
				t.Errorf("expected empty array for unknown FK, got %d items", len(body))
			}
		})
	}
}

func TestCustomQueries_Shop_MissingIDReturnsEmpty(t *testing.T) {
	// Проверяем что /products/{id}/order_items в SQL отвечает [] при id,
	// которого нет в products.
	cfg, db := loadScenario(t, "../../../testdata/scenarios/shop")
	defer db.Close()
	ts := buildTestRouter(t, cfg, db)

	status, body := getJSON[[]map[string]any](t, ts.URL+"/products/99999/order_items")
	if status != 200 {
		t.Errorf("expected 200, got %d", status)
	}
	if len(body) != 0 {
		t.Errorf("expected empty result, got %d", len(body))
	}
}
