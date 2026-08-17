from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from catalog import views


def healthcheck(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", healthcheck, name="healthcheck"),
    path("", views.index, name="index"),
    path("catalog/", views.catalog, name="catalog"),
    path("catalog/<slug:category_slug>/", views.category_detail, name="category_detail"),
    path("product/<slug:slug>/", views.product_detail, name="product_detail"),
    path("brands/", views.brands_list, name="brands_list"),
    path("brands/<slug:slug>/", views.brand_detail, name="brand_detail"),
    path("search/", views.search, name="search"),
    path("about/", views.about, name="about"),
    path("contacts/", views.contacts, name="contacts"),
    path("cart/", views.cart_view, name="cart_view"),
    path("cart/add/<int:product_id>/", views.add_to_cart, name="add_to_cart"),
    path("cart/update/<int:item_id>/", views.cart_update, name="cart_update"),
    path("cart/remove/<int:item_id>/", views.cart_remove, name="cart_remove"),
    path("checkout/", views.checkout, name="checkout"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    if "debug_toolbar" in settings.INSTALLED_APPS:
        urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
