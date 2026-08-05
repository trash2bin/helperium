from django.contrib import admin
from catalog.models import Brand, Category, Product, Cart, CartItem, Order, SiteSettings


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('name', 'country', 'is_oem', 'ordering', 'product_count')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name',)
    list_filter = ('is_oem', 'country')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related()

    @admin.display(description='Товаров')
    def product_count(self, obj):
        return obj.products.count()


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'is_active', 'ordering', 'product_count')
    prepopulated_fields = {'slug': ('name',)}
    list_filter = ('is_active',)
    search_fields = ('name',)

    @admin.display(description='Товаров')
    def product_count(self, obj):
        return obj.products.count()


class ProductInline(admin.TabularInline):
    model = CartItem
    extra = 0
    readonly_fields = ('product', 'quantity', 'price')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'article', 'name', 'brand', 'category',
        'price', 'quantity', 'is_available', 'label', 'is_active'
    )
    list_filter = ('brand', 'category', 'is_available', 'is_active', 'label', 'is_popular', 'is_new')
    search_fields = ('name', 'article', 'oem_number', 'brand__name')
    prepopulated_fields = {'slug': ('name',)}
    autocomplete_fields = ('brand', 'category')
    readonly_fields = ('views_count', 'created_at', 'updated_at')
    list_editable = ('price', 'quantity', 'is_active', 'label')
    list_select_related = ('brand', 'category')

    fieldsets = (
        ('Основное', {
            'fields': (
                'article', 'name', 'slug', 'brand', 'category',
                'short_description', 'description'
            )
        }),
        ('Цены и наличие', {
            'fields': ('price', 'old_price', 'quantity', 'is_available', 'label'),
        }),
        ('Характеристики', {
            'fields': ('characteristics', 'car_applicability', 'weight_kg', 'dimensions', 'country_of_origin'),
            'classes': ('wide',),
        }),
        ('Дополнительно', {
            'fields': (
                'oem_number', 'supplier', 'warranty_months',
                'is_popular', 'is_new', 'is_bestseller', 'is_promo',
                'image',
            ),
        }),
        ('SEO', {
            'fields': ('seo_title', 'seo_description'),
            'classes': ('collapse',),
        }),
        ('Служебное', {
            'fields': ('ordering', 'is_active', 'views_count', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='Остаток')
    def stock(self, obj):
        return f'{obj.quantity} шт.' if obj.quantity > 0 else 'Нет'


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('session_key', 'created_at', 'item_count', 'total')
    readonly_fields = ('session_key', 'created_at')

    @admin.display(description='Позиций')
    def item_count(self, obj):
        return obj.total_items

    @admin.display(description='Сумма')
    def total(self, obj):
        return obj.total_price


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_number', 'last_name', 'first_name',
        'phone', 'total', 'status', 'delivery_method', 'created_at'
    )
    list_filter = ('status', 'delivery_method', 'payment_method', 'created_at')
    search_fields = ('order_number', 'phone', 'email', 'last_name', 'first_name')
    readonly_fields = (
        'order_number', 'items', 'subtotal',
        'delivery_cost', 'total', 'created_at', 'updated_at'
    )
    list_editable = ('status',)

    fieldsets = (
        ('Информация о заказе', {
            'fields': ('order_number', 'status', 'created_at', 'updated_at')
        }),
        ('Клиент', {
            'fields': ('last_name', 'first_name', 'patronymic', 'phone', 'email')
        }),
        ('Доставка и оплата', {
            'fields': ('city', 'address', 'delivery_method', 'payment_method', 'comment')
        }),
        ('Финансы', {
            'fields': ('subtotal', 'delivery_cost', 'total')
        }),
        ('Состав заказа', {
            'fields': ('items',),
            'classes': ('wide',),
        }),
    )


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ('site_name', 'phone', 'email', 'is_active')

    def has_add_permission(self, request):
        if SiteSettings.objects.exists():
            return False
        return True
