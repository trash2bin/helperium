from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.db.models import Q, Count, F
from django.contrib import messages
from catalog.models import Product, Category, Brand, Cart, CartItem, Order


def index(request):
    """Главная страница"""
    popular = Product.objects.filter(is_active=True, is_available=True, is_popular=True).select_related('brand')[:8]
    if not popular:
        popular = Product.objects.filter(is_active=True, is_available=True).select_related('brand')[:8]
    brands = Brand.objects.annotate(product_count=Count('products', filter=Q(products__is_active=True)))[:12]
    return render(request, 'catalog/index.html', {
        'popular_products': popular,
        'brands': brands,
    })


def catalog(request):
    """Каталог товаров с фильтрами"""
    products = Product.objects.filter(is_active=True).select_related('brand', 'category')
    categories = Category.objects.filter(is_active=True, parent__isnull=True)

    selected_categories = request.GET.getlist('category')
    if selected_categories:
        products = products.filter(category__slug__in=selected_categories)

    if request.GET.get('in_stock'):
        products = products.filter(is_available=True)

    if request.GET.get('popular'):
        products = products.filter(is_popular=True)

    q = request.GET.get('q', '').strip()
    if q:
        products = products.filter(
            Q(name__icontains=q) |
            Q(article__icontains=q) |
            Q(oem_number__icontains=q) |
            Q(brand__name__icontains=q)
        )

    sort = request.GET.get('sort', '-created_at')
    allowed_sorts = ['price', '-price', 'name', '-name', '-created_at', '-views_count']
    if sort in allowed_sorts:
        products = products.order_by(sort)

    paginator = Paginator(products, 24)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    return render(request, 'catalog/catalog.html', {
        'products': page_obj.object_list,
        'page_obj': page_obj,
        'categories': categories,
        'selected_categories': selected_categories,
    })


def category_detail(request, category_slug):
    """Страница категории с подкатегориями"""
    category = get_object_or_404(Category, slug=category_slug, is_active=True)
    subcategories = Category.objects.filter(parent=category, is_active=True)

    cat_ids = [category.id] + list(subcategories.values_list('id', flat=True))
    products = Product.objects.filter(
        category_id__in=cat_ids, is_active=True
    ).select_related('brand', 'category')

    if request.GET.get('in_stock'):
        products = products.filter(is_available=True)

    paginator = Paginator(products, 24)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    return render(request, 'catalog/category_detail.html', {
        'category': category,
        'subcategories': subcategories,
        'products': page_obj.object_list,
        'page_obj': page_obj,
    })


def product_detail(request, slug):
    """Страница товара"""
    product = get_object_or_404(
        Product.objects.select_related('brand', 'category'),
        slug=slug, is_active=True
    )
    Product.objects.filter(pk=product.pk).update(views_count=F('views_count') + 1)
    product.refresh_from_db()

    similar = Product.objects.filter(
        category=product.category, is_active=True, is_available=True
    ).exclude(pk=product.pk).select_related('brand')[:4]

    if request.method == 'POST':
        quantity = int(request.POST.get('quantity', 1))
        return add_to_cart_handler(request, product, quantity)

    return render(request, 'catalog/product_detail.html', {
        'product': product,
        'similar': similar,
    })


def search(request):
    """Поиск"""
    query = request.GET.get('q', '').strip()
    products = Product.objects.none()

    if query:
        products = Product.objects.filter(is_active=True).select_related('brand', 'category')
        products = products.filter(
            Q(name__icontains=query) |
            Q(article__icontains=query) |
            Q(oem_number__icontains=query) |
            Q(brand__name__icontains=query)
        )

    paginator = Paginator(products, 24)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    return render(request, 'catalog/search.html', {
        'query': query,
        'products': page_obj.object_list,
        'page_obj': page_obj,
    })


def brands_list(request):
    """Список брендов"""
    brands = Brand.objects.annotate(
        product_count=Count('products', filter=Q(products__is_active=True))
    ).order_by('name')
    return render(request, 'catalog/brands_list.html', {'brands': brands})


def brand_detail(request, slug):
    """Страница бренда"""
    brand = get_object_or_404(Brand, slug=slug)
    products = Product.objects.filter(brand=brand, is_active=True).select_related('brand', 'category')

    paginator = Paginator(products, 24)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    return render(request, 'catalog/brand_detail.html', {
        'brand': brand,
        'products': page_obj.object_list,
        'page_obj': page_obj,
    })


def about(request):
    """О компании"""
    return render(request, 'catalog/about.html')


def contacts(request):
    """Контакты"""
    return render(request, 'catalog/contacts.html')


# ─── Корзина ────────────────────────────────────────────────────────

def _get_or_create_cart(request):
    """Получить или создать корзину для сессии"""
    if not request.session.session_key:
        request.session.create()
    cart, _ = Cart.objects.get_or_create(session_key=request.session.session_key)
    return cart


def add_to_cart_handler(request, product, quantity=1):
    """Общая логика добавления в корзину"""
    cart = _get_or_create_cart(request)
    cart_item, created = CartItem.objects.get_or_create(
        cart=cart,
        product=product,
        defaults={'quantity': quantity, 'price': product.price}
    )
    if not created:
        cart_item.quantity += quantity
        cart_item.save()
    messages.success(request, f'«{product.name}» добавлен в корзину')


def add_to_cart(request, product_id):
    """Добавление в корзину (GET/POST)"""
    product = get_object_or_404(Product, id=product_id, is_active=True)
    if request.method == 'POST':
        qty = int(request.POST.get('quantity', 1))
    else:
        qty = int(request.GET.get('quantity', 1))
    add_to_cart_handler(request, product, qty)
    return redirect(request.META.get('HTTP_REFERER', 'catalog'))


def cart_view(request):
    """Просмотр корзины"""
    if not request.session.session_key:
        return render(request, 'catalog/cart.html', {'items': [], 'cart': None})

    try:
        cart = Cart.objects.prefetch_related('items__product__brand').get(
            session_key=request.session.session_key
        )
        items = cart.items.all()
    except Cart.DoesNotExist:
        cart = None
        items = []

    return render(request, 'catalog/cart.html', {
        'cart': cart,
        'items': items,
    })


def cart_update(request, item_id):
    """Обновить количество"""
    item = get_object_or_404(CartItem, id=item_id)
    if request.method == 'POST':
        qty = int(request.POST.get('quantity', 1))
        if qty <= 0:
            item.delete()
            messages.success(request, 'Товар удалён из корзины')
        else:
            item.quantity = qty
            item.save()
    return redirect('cart_view')


def cart_remove(request, item_id):
    """Удалить из корзины"""
    item = get_object_or_404(CartItem, id=item_id)
    name = item.product.name
    item.delete()
    messages.success(request, f'«{name}» удалён из корзины')
    return redirect('cart_view')


def checkout(request):
    """Оформление заказа"""
    if not request.session.session_key:
        return redirect('catalog')

    try:
        cart = Cart.objects.prefetch_related('items__product__brand').get(
            session_key=request.session.session_key
        )
        items = cart.items.all()
        if not items:
            messages.warning(request, 'Корзина пуста')
            return redirect('catalog')
    except Cart.DoesNotExist:
        return redirect('catalog')

    if request.method == 'POST':
        order = Order(
            first_name=request.POST.get('first_name', ''),
            last_name=request.POST.get('last_name', ''),
            patronymic=request.POST.get('patronymic', ''),
            phone=request.POST.get('phone', ''),
            email=request.POST.get('email', ''),
            city=request.POST.get('city', 'Москва'),
            address=request.POST.get('address', ''),
            comment=request.POST.get('comment', ''),
            delivery_method=request.POST.get('delivery_method', 'courier'),
            payment_method=request.POST.get('payment_method', 'cash'),
        )

        order_items = []
        for item in items:
            order_items.append({
                'product_id': item.product.id,
                'article': item.product.article,
                'name': str(item.product),
                'quantity': item.quantity,
                'price': float(item.price),
            })
            Product.objects.filter(pk=item.product.pk).update(
                quantity=F('quantity') - item.quantity
            )

        order.items = order_items
        order.subtotal = sum(i['price'] * i['quantity'] for i in order_items)
        order.delivery_cost = 500 if order.delivery_method != 'pickup' else 0
        order.total = order.subtotal + order.delivery_cost
        order.save()

        cart.items.all().delete()

        messages.success(
            request,
            f'Заказ №{order.order_number} оформлен! Мы свяжемся с вами в ближайшее время.'
        )
        return redirect('index')

    return render(request, 'catalog/checkout.html', {
        'cart': cart,
        'items': items,
    })
