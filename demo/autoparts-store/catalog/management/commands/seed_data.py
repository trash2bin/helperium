from django.core.management.base import BaseCommand
from slugify import slugify
from catalog.models import Brand, Category, Product, SiteSettings, Order
from faker import Faker
import random
from decimal import Decimal
import json

fake = Faker('ru_RU')


class Command(BaseCommand):
    help = 'Наполняет БД тестовыми данными автозапчастей'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Удалить существующий каталог и пересоздать seed-данные',
        )

    def handle(self, *args, **options):
        self.stdout.write('Заполняем базу данных...')

        # Idempotent: если каталог уже заполнен — пропускаем сидирование
        # (в проде volume сохраняется между рестартами, не надо затирать 1.7M товаров)
        if Product.objects.exists() and not options.get('force', False):
            self.stdout.write(self.style.SUCCESS('Каталог уже содержит данные — сидирование пропущено (используйте --force для пересоздания)'))
            return

        # Иначе — чистая база, делаем полный сид
        Product.objects.all().delete()
        Brand.objects.all().delete()
        Category.objects.all().delete()
        Order.objects.all().delete()
        SiteSettings.objects.all().delete()

        # 1. Настройки сайта
        SiteSettings.objects.create(
            site_name='АвтоЗапчасти24',
            phone='+7 (495) 555-39-39',
            email='info@autozap24.ru',
            address='г. Москва, ул. Автомобильная, д. 42, стр. 3',
            work_hours='Пн-Пт: 9:00–20:00, Сб: 10:00–18:00, Вс: 10:00–16:00',
            delivery_info='Доставка по Москве — день в день. По России — от 2 дней.',
            about_text='АвтоЗапчасти24 — крупный интернет-магазин автозапчастей с собственным складом в Москве. Работаем с 2015 года. Более 10 000 наименований в наличии.',
            telegram='https://t.me/autozap24',
            whatsapp='+74955553939',
        )

        # 2. Бренды (реальные производители автозапчастей)
        brands_data = [
            ('Bosch', 'Германия', True, 1886, True),
            ('Febi', 'Германия', False, 1876, False),
            ('Lemförder', 'Германия', True, 1947, True),
            ('TRW', 'США', True, 1901, True),
            ('NGK', 'Япония', True, 1936, True),
            ('Denso', 'Япония', True, 1949, True),
            ('Mann-Filter', 'Германия', True, 1941, True),
            ('Mahle', 'Германия', True, 1920, True),
            ('ContiTech', 'Германия', False, 1871, False),
            ('SKF', 'Швеция', True, 1907, True),
            ('Mobil', 'США', True, 1866, True),
            ('Castrol', 'Великобритания', True, 1899, True),
            ('Liqui Moly', 'Германия', True, 1957, False),
            ('Valeo', 'Франция', True, 1923, True),
            ('Hella', 'Германия', True, 1899, True),
            ('Sachs', 'Германия', True, 1895, True),
            ('KYB', 'Япония', True, 1919, True),
            ('Monroe', 'США', True, 1916, True),
            ('Gates', 'США', True, 1911, True),
            ('Dayco', 'США', True, 1905, True),
            ('Meyle', 'Германия', False, 1955, False),
            ('Brembo', 'Италия', True, 1961, True),
            ('Textar', 'Германия', True, 1913, True),
            ('Jurid', 'Германия', True, 1919, True),
            ('Stellox', 'Германия', False, 2000, False),
            ('Starline', 'Россия', False, 1998, False),
            ('TSUZUKI', 'Россия', False, 2010, False),
            ('CTR', 'Корея', False, 2000, False),
            ('XYZ', 'Япония', False, 2000, False),
            ('Elring', 'Германия', True, 1879, True),
        ]

        brands = {}
        for name, country, oem, founded, is_oem in brands_data:
            brand = Brand.objects.create(
                name=name,
                slug=slugify(name),
                country=country,
                is_oem=is_oem,
                founded_year=founded,
                description=f'{name} — {"OEM" if is_oem else "известный"} производитель автозапчастей из {country}. Основан в {founded} году.',
                website=f'https://www.{name.lower().replace(" ","")}.com' if random.random() > 0.3 else '',
            )
            brands[name] = brand

        self.stdout.write(f'  ✔ Создано {len(brands_data)} брендов')

        # 3. Категории (реальные категории автозапчастей с подкатегориями)
        categories_hierarchy = [
            {
                'name': 'Тормозная система', 'icon': '🛑',
                'children': [
                    'Колодки тормозные', 'Диски тормозные', 'Барабаны тормозные',
                    'Суппорта', 'Цилиндры тормозные', 'Шланги тормозные',
                    'Трос ручника', 'Комплекты тормозных колодок', 'Датчики износа',
                ]
            },
            {
                'name': 'Двигатель', 'icon': '⚙️',
                'children': [
                    'Поршневая группа', 'ГРМ (ремни и цепи)', 'Натяжители и ролики',
                    'Прокладки и сальники', 'Масляные насосы', 'Водяные помпы',
                    'Форсунки', 'Свечи зажигания', 'Катушки зажигания',
                    'Масляные фильтры', 'Воздушные фильтры', 'Датчики двигателя',
                    'Ремни приводные', 'Поддоны двигателя',
                ]
            },
            {
                'name': 'Подвеска', 'icon': '🔩',
                'children': [
                    'Амортизаторы', 'Пружины', 'Рычаги подвески', 'Сайлентблоки',
                    'Шаровые опоры', 'Стабилизаторы', 'Втулки стабилизатора',
                    'Стойки стабилизатора', 'Опоры амортизаторов',
                    'Пыльники и отбойники',
                ]
            },
            {
                'name': 'Электрика', 'icon': '⚡',
                'children': [
                    'Аккумуляторы', 'Генераторы', 'Стартеры', 'Фары и оптика',
                    'Лампочки', 'Датчики', 'Предохранители', 'Реле',
                    'Жгуты проводов', 'Коммутаторы', 'Катушки зажигания',
                ]
            },
            {
                'name': 'Кузов и внешние детали', 'icon': '🚗',
                'children': [
                    'Бампера', 'Решётки радиатора', 'Крылья', 'Капоты',
                    'Зеркала', 'Стёкла', 'Фары', 'Фонари',
                    'Молдинги', 'Дверные ручки', 'Щётки стеклоочистителя',
                ]
            },
            {
                'name': 'Трансмиссия', 'icon': '🔧',
                'children': [
                    'Сцепление', 'Диски сцепления', 'Корзины сцепления',
                    'Выжимные подшипники', 'КПП (коробки передач)', 'ШРУСы',
                    'Пыльники ШРУСа', 'Полуоси', 'Дифференциалы',
                    'Трансмиссионное масло',
                ]
            },
            {
                'name': 'Выхлопная система', 'icon': '💨',
                'children': [
                    'Глушители', 'Катализаторы', 'Приёмные трубы',
                    'Резонаторы', 'Гофры', 'Крепления выхлопной системы',
                    'Лямбда-зонды',
                ]
            },
            {
                'name': 'Система охлаждения', 'icon': '❄️',
                'children': [
                    'Радиаторы', 'Вентиляторы', 'Термостаты', 'Помпы (водяные)',
                    'Патрубки', 'Расширительные бачки', 'Крышки радиатора',
                    'Антифризы и охлаждающие жидкости',
                ]
            },
            {
                'name': 'Рулевое управление', 'icon': '🔁',
                'children': [
                    'Наконечники рулевые', 'Тяги рулевые', 'Рулевые рейки',
                    'Крестовины руля', 'Насосы ГУР', 'Жидкость ГУР',
                    'Рулевые колонки',
                ]
            },
            {
                'name': 'Масла и технические жидкости', 'icon': '🛢️',
                'children': [
                    'Моторные масла', 'Трансмиссионные масла', 'Антифризы',
                    'Тормозные жидкости', 'Масла ГУР', 'Смазки',
                    'Промывки', 'Присадки',
                ]
            },
            {
                'name': 'Фильтры', 'icon': '💧',
                'children': [
                    'Масляные фильтры', 'Воздушные фильтры', 'Салонные фильтры',
                    'Топливные фильтры', 'Фильтры АКПП',
                ]
            },
            {
                'name': 'Ремни и приводы', 'icon': '⛓️',
                'children': [
                    'Ремни ГРМ', 'Цепи ГРМ', 'Поликлиновые ремни',
                    'Ремни кондиционера', 'Ремни генератора',
                ]
            },
        ]

        categories = {}
        for parent_data in categories_hierarchy:
            parent = Category.objects.create(
                name=parent_data['name'],
                slug=slugify(parent_data['name']),
                icon=parent_data['icon'],
                description=f'Категория {parent_data["name"]} — широкий ассортимент запчастей для ремонта и обслуживания.',
                is_active=True,
            )
            categories[parent.name] = parent
            for child_name in parent_data['children']:
                child_slug_base = slugify(f'{parent_data["name"]}-{child_name}')
                child = Category.objects.create(
                    name=child_name,
                    slug=child_slug_base,
                    parent=parent,
                    icon='🔹',
                    description=f'{child_name} — подкатегория {parent_data["name"]}',
                    is_active=True,
                )
                categories[child_name] = child

        self.stdout.write(f'  ✔ Создано {Category.objects.count()} категорий')

        # 4. Товары — более 200 продуманных запчастей
        # Данные для генерации товаров
        car_models = {
            'BMW': ['E46', 'E39', 'E60', 'E90', 'F10', 'F30', 'G20', 'X3 E83', 'X5 E53', 'X5 E70'],
            'Mercedes-Benz': ['W124', 'W202', 'W210', 'W211', 'W203', 'W204', 'W212', 'W166', 'W463'],
            'Audi': ['A4 B5', 'A4 B6', 'A4 B8', 'A6 C5', 'A6 C6', 'A6 C7', 'Q5', 'Q7', 'A3 8P', 'A3 8V'],
            'Volkswagen': ['Passat B3', 'Passat B4', 'Passat B5', 'Passat B6', 'Golf IV', 'Golf V', 'Golf VI', 'Tiguan', 'Touareg'],
            'Toyota': ['Camry V30', 'Camry V40', 'Camry V50', 'Corolla E120', 'Corolla E150', 'Land Cruiser 100', 'Land Cruiser 200', 'RAV4'],
            'Honda': ['Accord 6', 'Accord 7', 'Accord 8', 'Civic 4D', 'CR-V'],
            'Nissan': ['Almera G15', 'Teana J31', 'Teana J32', 'X-Trail T30', 'Qashqai J10'],
            'Mitsubishi': ['Lancer X', 'Outlander XL', 'Pajero 4', 'ASX'],
            'Subaru': ['Impreza GDA', 'Legacy BE', 'Outback', 'Forester'],
            'Mazda': ['3 BK', '6 GG', '6 GH', 'CX-5', 'MX-5'],
            'Ford': ['Focus 2', 'Focus 3', 'Mondeo 4', 'Kuga', 'Explorer'],
            'Opel': ['Astra G', 'Astra H', 'Astra J', 'Vectra B', 'Vectra C', 'Insignia'],
            'Renault': ['Logan', 'Sandero', 'Duster', 'Koleos', 'Megan 2', 'Megan 3'],
            'Peugeot': ['206', '307', '308', '407', '3008'],
            'Citroen': ['C4', 'C5', 'Picasso'],
            'Hyundai': ['Solaris', 'Elantra', 'Tucson', 'Santa Fe', 'Sonata'],
            'Kia': ['Rio', 'Ceed', 'Sportage', 'Sorento', 'Optima'],
            'Skoda': ['Octavia A5', 'Octavia A7', 'Superb', 'Rapid', 'Fabia'],
            'Volvo': ['S40', 'S60', 'S80', 'XC60', 'XC90'],
            'Lexus': ['IS250', 'ES350', 'RX300', 'NX200'],
            'Infiniti': ['FX35', 'FX37', 'G25', 'G35', 'Q50'],
        }

        # Конкретные товары
        items = []
        _art = 1000  # глобальный счётчик артикулов

        def nxt(prefix):
            nonlocal _art
            _art += 1
            return f'{prefix}-{_art:05d}'

        # Тормозные колодки
        pad_brands = [('TRW', 'GDB'), ('Bosch', '0 986'), ('Textar', 'T'), ('Jurid', ''), ('Brembo', 'P')]
        for prefix, _ in pad_brands:
            for i, (car, price, label) in enumerate([
                ('BMW E46', 2800, 'hit'), ('Mercedes W210', 3200, 'new'), ('Audi A4 B8', 2500, 'none'),
                ('Toyota Camry V40', 2200, 'sale'), ('VW Passat B5', 2100, 'none'), ('Ford Focus 2', 1800, 'hit'),
                ('Hyundai Solaris', 1600, 'new'), ('Kia Rio', 1500, 'sale'), ('Skoda Octavia A7', 2400, 'none'),
                ('Renault Duster', 1700, 'hit'), ('Nissan Qashqai', 2300, 'none'), ('Mitsubishi Lancer X', 1900, 'none'),
            ]):
                brand_name = prefix
                if brand_name not in brands:
                    continue
                brand_obj = brands[brand_name]
                article = nxt('BRK')
                items.append({
                    'article': article,
                    'name': f'Колодки тормозные передние для {car}',
                    'brand': brand_obj,
                    'category': categories['Колодки тормозные'],
                    'price': price,
                    'quantity': random.randint(3, 50),
                    'short_description': f'Тормозные колодки {brand_name} для {car}. Высокое качество, низкий износ.',
                    'description': f'Оригинальные тормозные колодки {brand_name} для автомобиля {car}. Изготовлены из высококачественных материалов. Обеспечивают эффективное торможение в любых условиях. Ресурс — до 50 000 км.',
                    'characteristics': {
                        'Материал': 'Керамика/Металл',
                        'Длина': '116 мм',
                        'Высота': '53 мм',
                        'Толщина': '17 мм',
                    },
                    'car_applicability': [car, car + ' (до 2010)', car + ' (рестайлинг)'],
                    'is_popular': label in ('hit',),
                    'is_new': label == 'new',
                    'label': label,
                    'warranty_months': 12,
                    'country_of_origin': 'Германия',
                    'supplier': f'{brand_name} GmbH',
                    'weight_kg': Decimal(f'{random.uniform(0.8, 1.5):.2f}'),
                })

        # Тормозные диски
        rotor_brands = [('TRW', 3500), ('Brembo', 4500), ('Bosch', 3000), ('Febi', 2500)]
        for brand_name, base_price in rotor_brands:
            if brand_name not in brands:
                continue
            brand_obj = brands[brand_name]
            for i, car in enumerate(['BMW E46', 'Mercedes W210', 'Audi A4 B8', 'Toyota Camry', 'VW Passat', 'Ford Focus']):
                items.append({
                    'article': nxt('DISK'),
                    'name': f'Диск тормозной передний для {car} Перфорированный',
                    'brand': brand_obj,
                    'category': categories['Диски тормозные'],
                    'price': base_price + random.randint(-200, 500),
                    'quantity': random.randint(2, 30),
                    'short_description': f'Тормозной диск {brand_name} для {car}. Вентилируемый, перфорированный.',
                    'description': f'Высококачественный тормозной диск {brand_name}. Вентилируемый, с перфорацией для лучшего отвода тепла. Снижает эффект fade при интенсивном торможении.',
                    'characteristics': {
                        'Диаметр': '296 мм',
                        'Толщина': '28 мм',
                        'Минимальная толщина': '26 мм',
                        'Тип': 'Вентилируемый',
                    },
                    'car_applicability': [car],
                    'is_popular': True,
                    'old_price': None,
                    'label': 'hit' if random.random() > 0.7 else 'none',
                    'warranty_months': 12,
                    'country_of_origin': brand_obj.country,
                    'weight_kg': Decimal(f'{random.uniform(4.5, 7.0):.2f}'),
                })

        # Амортизаторы
        shock_brands = [('KYB', 5500), ('Sachs', 6000), ('Monroe', 4500), ('Febi', 4000)]
        for brand_name, base_price in shock_brands:
            if brand_name not in brands:
                continue
            brand_obj = brands[brand_name]
            for i, (car, car_list) in enumerate(list(car_models.items())[:6]):
                model = random.choice(car_list)
                items.append({
                    'article': nxt('SHP'),
                    'name': f'Амортизатор передний {car} {model}',
                    'brand': brand_obj,
                    'category': categories['Амортизаторы'],
                    'price': base_price + random.randint(0, 1500),
                    'quantity': random.randint(2, 20),
                    'short_description': f'Амортизатор передний {brand_name} для {car} {model}. Газомасляный.',
                    'description': f'Амортизатор передний {brand_name} для {car} {model}. Двухтрубный газомасляный. Обеспечивает комфортное управление и устойчивость на дороге.',
                    'characteristics': {
                        'Тип': 'Газомасляный',
                        'Сторона': 'Передняя',
                        'Конструкция': 'Двухтрубный',
                        'Ход штока': f'{random.randint(100, 180)} мм',
                    },
                    'car_applicability': [f'{car} {model}'],
                    'is_popular': True,
                    'old_price': Decimal(base_price + 2000) if random.random() > 0.5 else None,
                    'label': 'sale' if random.random() > 0.7 else ('promo' if random.random() > 0.8 else 'none'),
                    'warranty_months': 24,
                    'country_of_origin': brand_obj.country,
                    'weight_kg': Decimal(f'{random.uniform(2.0, 3.5):.2f}'),
                })

        # Фильтры
        filter_brands = [('Mann-Filter', 'MANN'), ('Mahle', 'MAHLE'), ('Bosch', 'BOSCH'), ('Febi', 'FEBI')]
        filter_types = [
            ('Масляный фильтр', 'Моторный фильтр', categories['Масляные фильтры']),
            ('Воздушный фильтр', 'Фильтр воздушный', categories['Воздушные фильтры']),
            ('Салонный фильтр', 'Фильтр салонный угольный', categories['Салонные фильтры']),
        ]
        for brand_name, _ in filter_brands:
            if brand_name not in brands:
                continue
            brand_obj = brands[brand_name]
            for i, (ftype, desc, cat) in enumerate(filter_types):
                for j, car in enumerate(['BMW E46', 'Audi A4', 'VW Golf', 'Toyota Camry', 'Mercedes W211', 'Ford Focus', 'Skoda Octavia', 'Hyundai Solaris']):
                    items.append({
                        'article': nxt('FLT'),
                        'name': f'{desc} {brand_name} для {car}',
                        'brand': brand_obj,
                        'category': cat,
                        'price': random.randint(400, 1500),
                        'quantity': random.randint(10, 100),
                        'short_description': f'{ftype} {brand_name} для {car}. Оригинальное качество.',
                        'description': f'{ftype} {brand_name} — оригинальное качество. Обеспечивает эффективную очистку. Рекомендован производителем {car}. Заменять каждые 15 000 км.',
                        'characteristics': {
                            'Высота': f'{random.randint(150, 280)} мм',
                            'Диаметр': f'{random.randint(60, 100)} мм',
                            'Резьба': f'M{random.randint(18, 24)}x1.5',
                        },
                        'car_applicability': [car],
                        'is_popular': True,
                        'is_new': random.random() > 0.8,
                        'label': 'hit' if random.random() > 0.85 else ('new' if random.random() > 0.7 else 'none'),
                        'warranty_months': 6,
                        'country_of_origin': 'Германия',
                        'weight_kg': Decimal(f'{random.uniform(0.15, 0.5):.2f}'),
                    })

        # Свечи зажигания NGK, Denso, Bosch
        plug_brands = [('NGK', 800), ('Denso', 900), ('Bosch', 750)]
        for brand_name, base_price in plug_brands:
            if brand_name not in brands:
                continue
            brand_obj = brands[brand_name]
            for i, car in enumerate(['BMW E46', 'Audi A4', 'VW Passat', 'Toyota Camry',
                                     'Mercedes W203', 'Ford Focus', 'Mazda 6', 'Hyundai Solaris',
                                     'Nissan Teana', 'Kia Sportage', 'Skoda Octavia', 'Subaru Impreza']):
                items.append({
                    'article': nxt('SPK'),
                    'name': f'Свеча зажигания {brand_name} для {car} (4 шт)',
                    'brand': brand_obj,
                    'category': categories['Свечи зажигания'],
                    'price': base_price * 4 + random.randint(0, 200),
                    'quantity': random.randint(10, 60),
                    'short_description': f'Комплект свечей зажигания {brand_name} для {car}. Иридиевые.',
                    'description': f'Иридиевые свечи зажигания {brand_name} в комплекте 4 шт. Долгий срок службы — до 100 000 км. Стабильная работа двигателя.',
                    'characteristics': {
                        'Тип': 'Иридиевые',
                        'Зазор': '1.1 мм',
                        'Резьба': '14 мм',
                        'Ключ': '16 мм',
                    },
                    'car_applicability': [car],
                    'is_popular': True,
                    'label': 'none',
                    'warranty_months': 12,
                    'country_of_origin': 'Япония',
                    'weight_kg': Decimal('0.2'),
                })

        # Масла
        oil_brands = [('Mobil', 3500), ('Castrol', 3200), ('Liqui Moly', 4000)]
        for brand_name, base_price in oil_brands:
            if brand_name not in brands:
                continue
            brand_obj = brands[brand_name]
            for i, (oil_name, viscosity) in enumerate([
                ('Моторное масло 5W-30', '5W-30'),
                ('Моторное масло 5W-40', '5W-40'),
                ('Моторное масло 10W-40', '10W-40'),
                ('Моторное масло 0W-20', '0W-20'),
            ]):
                items.append({
                    'article': nxt('OIL'),
                    'name': f'{oil_name} {brand_name} 4л',
                    'brand': brand_obj,
                    'category': categories['Моторные масла'],
                    'price': base_price + random.randint(-300, 500),
                    'quantity': random.randint(5, 40),
                    'short_description': f'{oil_name} {brand_name} 4 литра. Синтетическое.',
                    'description': f'Полностью синтетическое моторное масло {brand_name} {viscosity}. Объём 4 литра. Подходит для бензиновых и дизельных двигателей. Обеспечивает отличную защиту и чистоту двигателя.',
                    'characteristics': {
                        'Вязкость': viscosity,
                        'Состав': 'Синтетическое',
                        'Объём': '4 л',
                        'Допуски': 'API SN, ACEA A3/B4',
                    },
                    'car_applicability': ['Универсальное'],
                    'is_popular': True,
                    'label': 'sale' if i == 0 else ('promo' if i == 2 else 'none'),
                    'old_price': Decimal(base_price + 500) if random.random() > 0.5 else None,
                    'warranty_months': 0,
                    'country_of_origin': brand_obj.country,
                    'weight_kg': Decimal('3.8'),
                })

        # Дополнительные товары: ШРУСы, рулевые наконечники, сайлентблоки, ремни ГРМ
        extra_parts = [
            # ШРУСы
            {
                'name': 'ШРУС внутренний',
                'cat': 'ШРУСы',
                'price_range': (2500, 4500),
                'brand_bias': ['Febi', 'SKF', 'CTR', 'GKN'],
            },
            {
                'name': 'Пыльник ШРУСа',
                'cat': 'Пыльники ШРУСа',
                'price_range': (300, 800),
                'brand_bias': ['Febi', 'Meyle', 'Elring'],
            },
            {
                'name': 'Наконечник рулевой',
                'cat': 'Наконечники рулевые',
                'price_range': (800, 2500),
                'brand_bias': ['Lemförder', 'TRW', 'Febi', 'Meyle'],
            },
            {
                'name': 'Сайлентблок рычага',
                'cat': 'Сайлентблоки',
                'price_range': (400, 1500),
                'brand_bias': ['Lemförder', 'Febi', 'Meyle'],
            },
            {
                'name': 'Ремень ГРМ',
                'cat': 'Ремни ГРМ',
                'price_range': (1500, 4000),
                'brand_bias': ['ContiTech', 'Gates', 'Dayco'],
            },
            {
                'name': 'Ролик натяжителя ремня',
                'cat': 'Натяжители и ролики',
                'price_range': (1200, 3500),
                'brand_bias': ['SKF', 'NTN', 'INA', 'Febi'],
            },
            {
                'name': 'Помпа водяная',
                'cat': 'Помпы (водяные)',
                'price_range': (2000, 5000),
                'brand_bias': ['Bosch', 'Valeo', 'SKF', 'Hepu'],
            },
            {
                'name': 'Термостат',
                'cat': 'Термостаты',
                'price_range': (800, 2500),
                'brand_bias': ['Bosch', 'Mahle', 'Vernet', 'Wahler'],
            },
            {
                'name': 'Лямбда-зонд',
                'cat': 'Лямбда-зонды',
                'price_range': (2500, 6000),
                'brand_bias': ['Bosch', 'Denso', 'NGK', 'Febi'],
            },
            {
                'name': 'Датчик ABS',
                'cat': 'Датчики',
                'price_range': (1500, 3500),
                'brand_bias': ['Bosch', 'TRW', 'Febi', 'Hella'],
            },
        ]

        for ep in extra_parts:
            cat_name = ep['cat']
            if cat_name not in categories:
                continue
            cat = categories[cat_name]
            for br_name in ep['brand_bias']:
                if br_name not in brands:
                    continue
                brand_obj = brands[br_name]
                for j, (car_mark, models_list) in enumerate(list(car_models.items())[:5]):
                    car = f'{car_mark} {random.choice(models_list)}'
                    price = random.randint(*ep['price_range'])
                    items.append({
                        'article': nxt('EXT'),
                        'name': f'{ep["name"]} {br_name} для {car}',
                        'brand': brand_obj,
                        'category': cat,
                        'price': price,
                        'quantity': random.randint(1, 25),
                        'short_description': f'{ep["name"]} {br_name} для {car}. Качественный аналог.',
                        'description': f'{ep["name"]} {br_name}. Подходит для {car}. Высокое качество, точное соответствие оригинальным размерам. Гарантия {random.choice([12, 24, 36])} месяцев.',
                        'characteristics': {
                            'Производитель': br_name,
                            'Применимость': car,
                            'Качество': 'Оригинал/Аналог',
                        },
                        'car_applicability': [car],
                        'is_popular': random.random() > 0.7,
                        'is_new': random.random() > 0.85,
                        'label': random.choices(['none', 'hit', 'new', 'sale'], weights=[0.6, 0.15, 0.1, 0.15])[0],
                        'old_price': Decimal(price * random.choice([1.15, 1.2, 1.25])) if random.random() > 0.6 else None,
                        'warranty_months': random.choice([6, 12, 24]),
                        'country_of_origin': brand_obj.country,
                        'weight_kg': Decimal(f'{random.uniform(0.3, 2.0):.2f}'),
                    })

        # Создаём товары
        for item_data in items:
            Product.objects.create(**item_data)

        self.stdout.write(f'  ✔ Создано {Product.objects.count()} товаров')

        # 5. Несколько исторических заказов (для реалистичности)
        statuses = ['delivered', 'delivered', 'delivered', 'shipped', 'confirmed', 'new']
        for i in range(6):
            products = list(Product.objects.all())
            selected = random.sample(products, min(3, len(products)))

            order_items = []
            subtotal = 0
            for p in selected:
                qty = random.randint(1, 3)
                price = float(p.price)
                order_items.append({
                    'product_id': p.id,
                    'article': p.article,
                    'name': str(p),
                    'quantity': qty,
                    'price': price,
                })
                subtotal += price * qty

            delivery = 500 if i % 3 != 0 else 0
            Order.objects.create(
                order_number=f'АП-{100000+i+1}',
                first_name=fake.first_name(),
                last_name=fake.last_name(),
                patronymic=fake.middle_name(),
                phone=f'+7 (9{random.randint(00, 99)}) {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}',
                email=fake.email(),
                city=random.choice(['Москва', 'Санкт-Петербург', 'Казань', 'Новосибирск', 'Екатеринбург']),
                address=fake.address(),
                delivery_method=random.choice(['courier', 'pickup', 'russian_post']),
                payment_method=random.choice(['cash', 'card', 'online']),
                status=random.choice(statuses),
                items=order_items,
                subtotal=Decimal(str(subtotal)),
                delivery_cost=Decimal(str(delivery)),
                total=Decimal(str(subtotal + delivery)),
            )

        self.stdout.write(f'  ✔ Создано {Order.objects.count()} заказов')
        self.stdout.write(self.style.SUCCESS('✅ База данных успешно заполнена!'))
