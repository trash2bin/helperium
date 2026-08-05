from django.db import models
from slugify import slugify
from django.core.exceptions import ValidationError


class SiteSettings(models.Model):
    """Настройки магазина (синглтон)"""
    site_name = models.CharField('Название магазина', max_length=200, default='АвтоЗапчасти')
    phone = models.CharField('Телефон', max_length=20, default='+7 (495) 123-45-67')
    email = models.EmailField('Email', default='info@autoparts.ru')
    address = models.TextField('Адрес', default='г. Москва, ул. Автозаводская, д. 1')
    work_hours = models.CharField('Режим работы', max_length=200, default='Пн-Пт: 9:00–19:00, Сб: 10:00–17:00')
    delivery_info = models.TextField('Информация о доставке', blank=True, default='')
    about_text = models.TextField('Текст о компании', blank=True, default='')
    telegram = models.URLField('Telegram', blank=True, default='')
    whatsapp = models.CharField('WhatsApp', max_length=20, blank=True, default='')
    is_active = models.BooleanField('Активно', default=True)

    class Meta:
        verbose_name = 'Настройки сайта'
        verbose_name_plural = 'Настройки сайта'

    def __str__(self):
        return self.site_name

    def save(self, *args, **kwargs):
        self.pk = 1  # синглтон
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Brand(models.Model):
    """Производитель запчастей"""
    name = models.CharField('Название', max_length=200)
    slug = models.SlugField('URL', max_length=200, unique=True)
    country = models.CharField('Страна', max_length=100, blank=True, default='')
    logo = models.ImageField('Логотип', upload_to='brands/', blank=True, null=True)
    description = models.TextField('Описание', blank=True, default='')
    founded_year = models.IntegerField('Год основания', null=True, blank=True)
    website = models.URLField('Веб-сайт', blank=True, default='')
    is_oem = models.BooleanField('OEM производитель', default=False)
    ordering = models.IntegerField('Сортировка', default=0)
    created_at = models.DateTimeField('Дата добавления', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления', auto_now=True)

    class Meta:
        verbose_name = 'Производитель'
        verbose_name_plural = 'Производители'
        ordering = ['ordering', 'name']

    def __str__(self):
        return self.name


class Category(models.Model):
    """Категория запчастей"""
    name = models.CharField('Название', max_length=200)
    slug = models.SlugField('URL', max_length=200, unique=True)
    description = models.TextField('Описание', blank=True, default='')
    image = models.ImageField('Изображение', upload_to='categories/', blank=True, null=True)
    parent = models.ForeignKey(
        'self', verbose_name='Родительская категория',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='children'
    )
    icon = models.CharField('Иконка', max_length=50, blank=True, default='')
    ordering = models.IntegerField('Сортировка', default=0)
    is_active = models.BooleanField('Активна', default=True)
    created_at = models.DateTimeField('Дата добавления', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления', auto_now=True)

    class Meta:
        verbose_name = 'Категория'
        verbose_name_plural = 'Категории'
        ordering = ['ordering', 'name']

    def __str__(self):
        return self.name


class Product(models.Model):
    """Товар — автозапчасть"""

    class LabelChoices(models.TextChoices):
        NONE = 'none', 'Нет'
        HIT = 'hit', 'Хит'
        NEW = 'new', 'Новинка'
        SALE = 'sale', 'Распродажа'
        PROMO = 'promo', 'Акция'

    article = models.CharField('Артикул', max_length=50, unique=True)
    name = models.CharField('Название', max_length=300)
    slug = models.SlugField('URL', max_length=300, unique=True)
    brand = models.ForeignKey(
        Brand, verbose_name='Производитель',
        on_delete=models.CASCADE, related_name='products'
    )
    category = models.ForeignKey(
        Category, verbose_name='Категория',
        on_delete=models.CASCADE, related_name='products'
    )
    oem_number = models.CharField('OEM номер', max_length=100, blank=True, default='')
    price = models.DecimalField('Цена', max_digits=10, decimal_places=2)
    old_price = models.DecimalField('Старая цена', max_digits=10, decimal_places=2, null=True, blank=True)
    quantity = models.PositiveIntegerField('Количество на складе', default=0)
    is_available = models.BooleanField('В наличии', default=False)
    description = models.TextField('Описание', blank=True, default='')
    short_description = models.CharField('Краткое описание', max_length=300, blank=True, default='')
    characteristics = models.JSONField('Характеристики', default=dict, blank=True)
    car_applicability = models.JSONField('Применимость к авто', default=list, blank=True)
    weight_kg = models.DecimalField('Вес (кг)', max_digits=8, decimal_places=2, null=True, blank=True)
    dimensions = models.CharField('Размеры упаковки', max_length=100, blank=True, default='')
    image = models.ImageField('Изображение', upload_to='products/', blank=True, null=True)
    image_extra = models.JSONField('Дополнительные изображения', null=True, blank=True)
    is_popular = models.BooleanField('Популярный', default=False)
    is_new = models.BooleanField('Новинка', default=False)
    is_bestseller = models.BooleanField('Лидер продаж', default=False)
    is_promo = models.BooleanField('Акционный', default=False)
    label = models.CharField('Метка', max_length=50, choices=LabelChoices.choices, default=LabelChoices.NONE)
    warranty_months = models.PositiveIntegerField('Гарантия (мес.)', default=12)
    supplier = models.CharField('Поставщик', max_length=200, blank=True, default='')
    country_of_origin = models.CharField('Страна происхождения', max_length=100, blank=True, default='')
    seo_title = models.CharField('SEO заголовок', max_length=200, blank=True, default='')
    seo_description = models.TextField('SEO описание', blank=True, default='')
    views_count = models.PositiveIntegerField('Просмотры', default=0)
    ordering = models.IntegerField('Сортировка', default=0)
    is_active = models.BooleanField('Активен', default=True)
    created_at = models.DateTimeField('Дата добавления', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления', auto_now=True)

    class Meta:
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'
        ordering = ['ordering', '-created_at']
        indexes = [
            models.Index(fields=['article']),
            models.Index(fields=['brand', 'category']),
            models.Index(fields=['is_active', 'is_available']),
        ]

    def __str__(self):
        return f'{self.brand.name} {self.name} ({self.article})'

    def save(self, *args, **kwargs):
        self.is_available = self.quantity > 0
        if not self.slug:
            self.slug = slugify(f'{self.brand.name}-{self.name}')
        super().save(*args, **kwargs)


class Cart(models.Model):
    """Корзина (сессионная)"""
    session_key = models.CharField('Ключ сессии', max_length=40, unique=True)
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)

    class Meta:
        verbose_name = 'Корзина'
        verbose_name_plural = 'Корзины'

    def __str__(self):
        return f'Корзина {self.session_key[:10]}...'

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def total_price(self):
        return sum(item.total for item in self.items.all())


class CartItem(models.Model):
    """Позиция в корзине"""
    cart = models.ForeignKey(
        Cart, verbose_name='Корзина',
        on_delete=models.CASCADE, related_name='items'
    )
    product = models.ForeignKey(
        Product, verbose_name='Товар',
        on_delete=models.CASCADE
    )
    quantity = models.PositiveIntegerField('Количество', default=1)
    price = models.DecimalField('Цена', max_digits=10, decimal_places=2)
    created_at = models.DateTimeField('Дата добавления', auto_now_add=True)

    class Meta:
        verbose_name = 'Позиция корзины'
        verbose_name_plural = 'Позиции корзины'

    def __str__(self):
        return f'{self.product.name} x {self.quantity}'

    @property
    def total(self):
        return self.price * self.quantity


class Order(models.Model):

    class StatusChoices(models.TextChoices):
        NEW = 'new', 'Новый'
        CONFIRMED = 'confirmed', 'Подтверждён'
        PROCESSING = 'processing', 'В обработке'
        SHIPPED = 'shipped', 'Отправлен'
        DELIVERED = 'delivered', 'Доставлен'
        CANCELLED = 'cancelled', 'Отменён'

    class DeliveryChoices(models.TextChoices):
        PICKUP = 'pickup', 'Самовывоз'
        COURIER = 'courier', 'Доставка курьером'
        POST = 'russian_post', 'Почта России'

    class PaymentChoices(models.TextChoices):
        CASH = 'cash', 'Наличные'
        CARD = 'card', 'Картой при получении'
        ONLINE = 'online', 'Онлайн-оплата'

    order_number = models.CharField('Номер заказа', max_length=20, unique=True, blank=True)
    first_name = models.CharField('Имя', max_length=100)
    last_name = models.CharField('Фамилия', max_length=100)
    patronymic = models.CharField('Отчество', max_length=100, blank=True, default='')
    phone = models.CharField('Телефон', max_length=20)
    email = models.EmailField('Email')
    city = models.CharField('Город', max_length=100, default='Москва')
    address = models.TextField('Адрес доставки')
    comment = models.TextField('Комментарий к заказу', blank=True, default='')
    delivery_method = models.CharField('Способ доставки', max_length=20, choices=DeliveryChoices.choices, default=DeliveryChoices.COURIER)
    payment_method = models.CharField('Способ оплаты', max_length=20, choices=PaymentChoices.choices, default=PaymentChoices.CASH)
    status = models.CharField('Статус', max_length=20, choices=StatusChoices.choices, default=StatusChoices.NEW)
    items = models.JSONField('Состав заказа', default=list, blank=True)
    subtotal = models.DecimalField('Сумма', max_digits=12, decimal_places=2, default=0)
    delivery_cost = models.DecimalField('Стоимость доставки', max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField('Итого', max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления', auto_now=True)

    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
        ordering = ['-created_at']

    def __str__(self):
        return f'Заказ {self.order_number}'

    def get_status_display_ru(self):
        return dict(self.StatusChoices.choices).get(self.status, self.status)

    @property
    def full_name(self):
        return f'{self.last_name} {self.first_name} {self.patronymic}'.strip()

    def save(self, *args, **kwargs):
        if not self.order_number:
            import random
            import string
            prefix = 'АП-'
            last_order = Order.objects.all().order_by('-id').first()
            next_num = (last_order.id + 1) if last_order else 1
            self.order_number = f'{prefix}{next_num:06d}'
        super().save(*args, **kwargs)
