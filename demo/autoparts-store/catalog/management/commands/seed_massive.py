from django.core.management.base import BaseCommand
from django.utils import timezone
from slugify import slugify
from catalog.models import Brand, Category, Product, Order, Cart, CartItem
from faker import Faker
import random
from decimal import Decimal
import json
from datetime import timedelta

fake = Faker('ru_RU')


PART_NAMES = [
    # Тормозная система
    'Тормозные колодки передние', 'Тормозные колодки задние',
    'Диск тормозной передний вентилируемый', 'Диск тормозной задний',
    'Диск тормозной передний перфорированный', 'Барабан тормозной',
    'Цилиндр тормозной рабочий', 'Цилиндр тормозной главный',
    'Суппорт тормозной передний левый', 'Суппорт тормозной передний правый',
    'Суппорт тормозной задний', 'Шланг тормозной передний',
    'Шланг тормозной задний', 'Трос ручника левый', 'Трос ручника правый',
    'Датчик износа колодок передний', 'Датчик износа колодок задний',
    'Комплект тормозных колодок', 'Комплект тормозных дисков',
    # Двигатель
    'Ремень ГРМ', 'Комплект ГРМ', 'Цепь ГРМ', 'Натяжитель цепи ГРМ',
    'Ролик натяжителя ремня ГРМ', 'Ролик направляющий ремня ГРМ',
    'Прокладка ГБЦ', 'Прокладка клапанной крышки', 'Прокладка поддона',
    'Сальник коленвала передний', 'Сальник коленвала задний',
    'Сальник распредвала', 'Помпа водяная', 'Водяной насос',
    'Масляный насос', 'Топливный насос', 'Форсунка топливная',
    'Свеча зажигания', 'Катушка зажигания', 'Масляный фильтр',
    'Воздушный фильтр', 'Салонный фильтр', 'Фильтр топливный',
    'Датчик температуры ОЖ', 'Датчик давления масла', 'Датчик коленвала',
    'Датчик распредвала', 'Датчик детонации', 'Датчик кислорода',
    'Ремень приводной поликлиновой', 'Ремень кондиционера',
    'Ремень генератора', 'Поддон двигателя', 'Клапанная крышка',
    'Маслозаливная горловина', 'Патрубок радиатора верхний',
    'Патрубок радиатора нижний', 'Патрубок печки',
    # Подвеска
    'Амортизатор передний левый', 'Амортизатор передний правый',
    'Амортизатор задний', 'Пружина передняя', 'Пружина задняя',
    'Рычаг подвески передний нижний', 'Рычаг подвески передний верхний',
    'Рычаг подвески задний', 'Сайлентблок рычага переднего',
    'Сайлентблок рычага заднего', 'Шаровая опора',
    'Стойка стабилизатора передняя', 'Стойка стабилизатора задняя',
    'Втулка стабилизатора передняя', 'Втулка стабилизатора задняя',
    'Опора амортизатора передняя', 'Опора амортизатора задняя',
    'Пыльник амортизатора', 'Отбойник амортизатора',
    'Подшипник ступицы передней', 'Подшипник ступицы задней',
    'Ступица передняя', 'Ступица задняя',
    # Рулевое управление
    'Наконечник рулевой левый', 'Наконечник рулевой правый',
    'Тяга рулевая левая', 'Тяга рулевая правая',
    'Рулевая рейка', 'Рулевой механизм', 'Крестовина рулевого вала',
    'Насос ГУР', 'Жидкость ГУР', 'Бачок ГУР',
    # Трансмиссия
    'Диск сцепления', 'Корзина сцепления', 'Выжимной подшипник',
    'ШРУС внутренний левый', 'ШРУС внутренний правый',
    'ШРУС наружный левый', 'ШРУС наружный правый',
    'Пыльник ШРУСа внутренний', 'Пыльник ШРУСа наружный',
    'Полуось левая', 'Полуось правая', 'Дифференциал',
    'Трансмиссионное масло', 'Фильтр АКПП',
    # Электрика
    'Аккумулятор 60Ah', 'Аккумулятор 75Ah', 'Аккумулятор 90Ah',
    'Генератор', 'Стартер', 'Втягивающее реле стартера',
    'Бендикс стартера', 'Датчик ABS передний левый', 'Датчик ABS передний правый',
    'Датчик ABS задний', 'Датчик парковки', 'Лямбда-зонд до катализатора',
    'Лямбда-зонд после катализатора', 'Катушка зажигания',
    'Высоковольтный провод свечной', 'Предохранитель',
    'Реле', 'Лампочка H4', 'Лампочка H7', 'Лампочка H1',
    'Лампочка габаритная', 'Лампочка поворота', 'Лампочка стоп-сигнала',
    # Охлаждение
    'Радиатор охлаждения', 'Радиатор печки', 'Радиатор кондиционера',
    'Вентилятор охлаждения', 'Термостат', 'Крышка радиатора',
    'Расширительный бачок', 'Антифриз G12', 'Антифриз G13',
    'Антифриз G11', 'Патрубок отопителя',
    # Выхлоп
    'Глушитель задний', 'Глушитель средний', 'Гофра выхлопной системы',
    'Резонатор', 'Катализатор', 'Лямбда-зонд', 'Приёмная труба',
    'Крепление глушителя', 'Прокладка выпускного коллектора',
    # Кузов
    'Бампер передний', 'Бампер задний', 'Решётка радиатора',
    'Крыло переднее левое', 'Крыло переднее правое',
    'Крыло заднее левое', 'Крыло заднее правое',
    'Капот', 'Зеркало левое', 'Зеркало правое',
    'Фара левая', 'Фара правая', 'Фонарь задний левый',
    'Фонарь задний правый', 'Противотуманная фара',
    'Щётка стеклоочистителя 60см', 'Щётка стеклоочистителя 55см',
    'Щётка стеклоочистителя 50см', 'Дверная ручка наружная',
    'Дверная ручка внутренняя', 'Стеклоподъёмник',
    # Масла
    'Моторное масло 5W-30 4л', 'Моторное масло 5W-40 4л',
    'Моторное масло 10W-40 4л', 'Моторное масло 0W-20 4л',
    'Моторное масло 5W-30 1л', 'Моторное масло 5W-40 1л',
    'Тормозная жидкость DOT4', 'Тормозная жидкость DOT5.1',
    'Трансмиссионное масло 75W-90', 'Трансмиссионное масло 80W-90',
    'Жидкость ГУР', 'Антифриз G12 5л', 'Антифриз G13 5л',
    'Смазка медная', 'Смазка литиевая WD-40', 'Присадка в масло',
    'Промывка двигателя 5 мин',
]

CAR_PARTS = {
    'BMW': ['E30', 'E34', 'E36', 'E39', 'E46', 'E53', 'E60', 'E83', 'E90', 'F01', 'F10',
            'F15', 'F20', 'F25', 'F30', 'F45', 'F80', 'G01', 'G05', 'G11', 'G20', 'G30'],
    'Mercedes-Benz': ['W124', 'W140', 'W163', 'W164', 'W166', 'W201', 'W202', 'W203', 'W204',
                      'W210', 'W211', 'W212', 'W213', 'W220', 'W221', 'W222', 'W463', 'W639'],
    'Audi': ['80 B3', '80 B4', '100 C3', 'A3 8L', 'A3 8P', 'A3 8V', 'A4 B5', 'A4 B6', 'A4 B8',
             'A4 B9', 'A5 8T', 'A6 C4', 'A6 C5', 'A6 C6', 'A6 C7', 'A6 C8', 'A8 D3', 'A8 D4',
             'Q3 8U', 'Q5 8R', 'Q7 4L', 'Q7 4M', 'TT 8N'],
    'Volkswagen': ['Passat B3', 'Passat B4', 'Passat B5', 'Passat B6', 'Passat B7', 'Passat B8',
                   'Golf III', 'Golf IV', 'Golf V', 'Golf VI', 'Golf VII', 'Golf VIII',
                   'Polo 6R', 'Polo 9N', 'Jetta III', 'Jetta IV', 'Jetta V', 'Jetta VI',
                   'Tiguan I', 'Tiguan II', 'Touareg 7L', 'Touareg 7P', 'Transporter T5',
                   'Transporter T6', 'Amarok', 'Caddy', 'Beetle A5'],
    'Toyota': ['Camry V30', 'Camry V40', 'Camry V50', 'Camry V70',
               'Corolla E100', 'Corolla E110', 'Corolla E120', 'Corolla E150', 'Corolla E180',
               'Land Cruiser 100', 'Land Cruiser 200', 'Land Cruiser Prado 120',
               'Land Cruiser Prado 150', 'RAV4 II', 'RAV4 III', 'RAV4 IV', 'RAV4 V',
               'Yaris XP9', 'Yaris XP13', 'Avensis T22', 'Avensis T25', 'Avensis T27',
               'Highlander II', 'Highlander III', 'C-HR'],
    'Honda': ['Accord 6', 'Accord 7', 'Accord 8', 'Accord 9',
              'Civic 4D', 'Civic 5D', 'Civic 8', 'Civic 9', 'Civic 10',
              'CR-V I', 'CR-V II', 'CR-V III', 'CR-V IV', 'CR-V V',
              'HR-V', 'Jazz', 'Pilot'],
    'Nissan': ['Almera G15', 'Almera Classic', 'Qashqai J10', 'Qashqai J11',
               'Teana J31', 'Teana J32', 'X-Trail T30', 'X-Trail T31', 'X-Trail T32',
               'Primera P10', 'Primera P11', 'Primera P12',
               'Pathfinder R51', 'Pathfinder R52', 'Navara D40',
               'Juke F15', 'Note', 'Murano Z50', 'Murano Z51'],
    'Hyundai': ['Solaris I', 'Solaris II', 'Elantra HD', 'Elantra MD', 'Elantra AD',
                'Sonata EF', 'Sonata YF', 'Sonata LF',
                'Tucson JM', 'Tucson IX', 'Tucson TL',
                'Santa Fe CM', 'Santa Fe DM', 'Santa Fe TM',
                'Creta GS', 'i30', 'i40', 'Genesis BH'],
    'Kia': ['Rio II', 'Rio III', 'Rio IV', 'Rio X',
            'Ceed ED', 'Ceed JD', 'Ceed CD', 'Ceed SW',
            'Sportage KM', 'Sportage SL', 'Sportage QL',
            'Sorento BL', 'Sorento XM', 'Sorento UM',
            'Optima TF', 'Optima JF', 'Cerato', 'Picanto',
            'Soul', 'Stinger', 'Mohave'],
    'Ford': ['Focus 1', 'Focus 2', 'Focus 3', 'Focus 4',
             'Mondeo 3', 'Mondeo 4', 'Mondeo 5',
             'Fusion', 'Kuga I', 'Kuga II', 'Kuga III',
             'Explorer III', 'Explorer IV', 'Explorer V',
             'Transit', 'Fiesta', 'Mustang VI', 'Escape'],
    'Opel': ['Astra F', 'Astra G', 'Astra H', 'Astra J', 'Astra K',
             'Vectra A', 'Vectra B', 'Vectra C',
             'Insignia A', 'Insignia B',
             'Corsa C', 'Corsa D', 'Corsa E',
             'Meriva A', 'Meriva B', 'Zafira A', 'Zafira B', 'Zafira C',
             'Antara', 'Mokka'],
    'Renault': ['Logan I', 'Logan II', 'Sandero I', 'Sandero II',
                'Duster I', 'Duster II', 'Koleos I', 'Koleos II',
                'Megane II', 'Megane III', 'Megane IV',
                'Scenic II', 'Scenic III', 'Laguna II', 'Laguna III',
                'Fluence', 'Arkana', 'Captur'],
    'Skoda': ['Octavia A4', 'Octavia A5', 'Octavia A7', 'Octavia A8',
              'Superb I', 'Superb II', 'Superb III',
              'Fabia I', 'Fabia II', 'Fabia III',
              'Rapid', 'Yeti', 'Kodiaq', 'Karoq', 'Scala'],
    'Mazda': ['3 BK', '3 BL', '3 BM', '3 BN',
              '6 GG', '6 GH', '6 GJ', '6 GL',
              'CX-5 KE', 'CX-5 KF', 'CX-7', 'CX-9',
              'MX-5 NB', 'MX-5 NC', 'MX-5 ND',
              '2', '5'],
    'Mitsubishi': ['Lancer 9', 'Lancer X', 'Outlander XL', 'Outlander III',
                   'Pajero 3', 'Pajero 4', 'ASX', 'ASX 2',
                   'Eclipse Cross', 'L200'],
    'Subaru': ['Impreza GDA', 'Impreza GE', 'Impreza GP',
               'Legacy BE', 'Legacy BP', 'Legacy BR',
               'Outback', 'Forester SH', 'Forester SJ', 'Forester SK',
               'Tribeca', 'XV'],
    'Volvo': ['S40 I', 'S40 II', 'S60 I', 'S60 II', 'S60 III',
              'S80 I', 'S80 II', 'XC60 I', 'XC60 II',
              'XC90 I', 'XC90 II', 'V40', 'V60', 'V70', 'XC70'],
    'Lexus': ['IS200', 'IS250', 'IS300', 'IS350',
              'ES300', 'ES350', 'GS300', 'GS450h',
              'RX300', 'RX350', 'RX450h',
              'NX200', 'NX300', 'LX570', 'LX600'],
    'Infiniti': ['FX35', 'FX37', 'FX45', 'FX50',
                 'G25', 'G35', 'G37', 'Q50',
                 'EX35', 'EX37', 'QX50', 'QX60', 'QX70', 'QX80'],
    'Peugeot': ['206', '207', '307', '308', '3008',
                '406', '407', '508', '5008', 'Partner'],
    'Citroen': ['C3', 'C4', 'C5', 'Picasso', 'Xsara',
                'Berlingo', 'Jumpy'],
    'Chevrolet': ['Aveo', 'Cruze', 'Lacetti', 'Aveo T250',
                  'Captiva', 'Tahoe', 'Trailblazer', 'Niva'],
    'Daihatsu': ['Terios', 'Sirion', 'Materia', 'Cuore'],
    'Fiat': ['Albea', 'Doblo', 'Ducato', 'Punto', 'Bravo', '500',
             'Tipo', 'Scudo', 'Ulysse'],
    'Land Rover': ['Range Rover L322', 'Range Rover L405',
                   'Range Rover Sport', 'Discovery 3', 'Discovery 4',
                   'Discovery 5', 'Freelander 2', 'Evoque', 'Velar'],
    'Jeep': ['Cherokee XJ', 'Cherokee KJ', 'Grand Cherokee WJ',
             'Grand Cherokee WK', 'Grand Cherokee WK2',
             'Wrangler JK', 'Wrangler JL', 'Compass', 'Patriot'],
    'Porsche': ['911 996', '911 997', '911 991', 'Cayenne 955',
                'Cayenne 958', 'Cayenne 9YA', 'Panamera 970',
                'Panamera 971', 'Macan', 'Cayman'],
}

CHARACTERISTICS_REGISTRY = [
    {'id': 0, 'matcher': lambda n: 'колодк' in n.lower(), 'chars': lambda: {
        'Материал': random.choice(['Керамика', 'Полуметаллические', 'Металлокерамика', 'Органика']),
        'Длина': f'{random.choice([87, 94, 105, 115, 126, 130])} мм',
        'Высота': f'{random.choice([42, 46, 52, 56, 60, 68])} мм',
        'Толщина': f'{random.choice([14, 16, 17, 18, 20])} мм',
        'Сторона': random.choice(['Передняя', 'Задняя']),
        'Производитель': 'OEM',
    }},
    {'id': 1, 'matcher': lambda n: 'диск' in n.lower() and 'тормозн' in n.lower(), 'chars': lambda: {
        'Диаметр': f'{random.choice([256, 268, 278, 286, 296, 312, 320, 330, 345, 360])} мм',
        'Толщина': f'{random.choice([22, 24, 26, 28, 30, 32, 34])} мм',
        'Тип': random.choice(['Вентилируемый', 'Перфорированный', 'С насечками']),
        'Количество отверстий': random.choice(['4', '5', '6']),
        'PCD': random.choice(['100', '112', '114.3', '120', '130']),
    }},
    {'id': 2, 'matcher': lambda n: 'фильтр' in n.lower(), 'chars': lambda: {
        'Тип': random.choice(['Масляный', 'Воздушный', 'Салонный', 'Топливный']),
        'Высота': f'{random.randint(120, 280)} мм',
        'Диаметр': f'{random.randint(55, 105)} мм',
        'Резьба': f'M{random.choice([18, 20, 22, 24])}x1.5',
        'Эффективность': '99.5%',
    }},
    {'id': 3, 'matcher': lambda n: 'свеч' in n.lower(), 'chars': lambda: {
        'Тип': random.choice(['Иридиевые', 'Платиновые', 'Медные', 'Yttrium']),
        'Зазор': f'{random.choice([0.8, 1.0, 1.1, 1.3])} мм',
        'Резьба': '14 мм',
        'Длина резьбы': f'{random.choice([19, 26.5, 28])} мм',
        'Ключ': f'{random.choice([14, 16, 20, 21])} мм',
        'Комплектация': f'{random.choice([1, 4, 6, 8])} шт',
    }},
    {'id': 4, 'matcher': lambda n: 'амортизат' in n.lower(), 'chars': lambda: {
        'Тип': random.choice(['Газомасляный', 'Газовый', 'Масляный']),
        'Сторона': random.choice(['Передняя', 'Задняя']),
        'Конструкция': random.choice(['Двухтрубный', 'Однотрубный']),
        'Ход штока': f'{random.randint(100, 200)} мм',
    }},
    {'id': 5, 'matcher': lambda n: 'датчик' in n.lower() or 'лямбда' in n.lower(), 'chars': lambda: {
        'Тип': random.choice(['Индуктивный', 'Холла', 'Резистивный', 'Кислородный']),
        'Напряжение': '12V',
        'Разъём': random.choice(['2-pin', '3-pin', '4-pin', '6-pin']),
        'Рабочая температура': f'-40..+{random.randint(120, 250)}°C',
    }},
    {'id': 6, 'matcher': lambda n: 'ремень' in n.lower() or 'цепь' in n.lower() or 'ролик' in n.lower(), 'chars': lambda: {
        'Тип': random.choice(['Зубчатый ремень', 'Поликлиновой ремень', 'Цепь']),
        'Ширина': f'{random.randint(17, 32)} мм',
        'Длина': f'{random.randint(1000, 2000)} мм',
        'Количество зубьев': random.choice([124, 128, 132, 136, 140, 146, 152, 158]),
    }},
    {'id': 7, 'matcher': lambda n: 'помпа' in n.lower() or 'насос' in n.lower() or 'водян' in n.lower(), 'chars': lambda: {
        'Тип': random.choice(['Помпа водяная', 'Масляный насос', 'Топливный насос']),
        'Привод': random.choice(['Ременной', 'Цепной', 'Электрический']),
        'Крыльчатка': random.choice(['Металл', 'Пластик', 'Композит']),
    }},
    {'id': 8, 'matcher': lambda n: 'радиатор' in n.lower(), 'chars': lambda: {
        'Тип': random.choice(['Охлаждения', 'Печки', 'Кондиционера', 'Масляный']),
        'Материал': random.choice(['Алюминий', 'Медь', 'Пластик+Алюминий']),
        'Размер': f'{random.randint(400, 900)}x{random.randint(300, 700)}x{random.randint(20, 60)} мм',
    }},
    {'id': 9, 'matcher': lambda n: 'шрус' in n.lower(), 'chars': lambda: {
        'Тип': random.choice(['Внутренний', 'Наружный']),
        'Сторона': random.choice(['Левая', 'Правая']),
        'Количество граней': random.choice(['6', '14', '18', '21']),
    }},
    {'id': 10, 'matcher': lambda n: 'масло' in n.lower() or 'антифриз' in n.lower() or 'жидк' in n.lower(), 'chars': lambda: {
        'Состав': random.choice(['Синтетическое', 'Полусинтетическое', 'Минеральное']),
        'Вязкость': lambda: random.choice(['5W-30', '5W-40', '10W-40', '0W-20', '0W-30', '0W-40']),
        'Объём': random.choice(['1 л', '4 л', '5 л', '20 л', '60 л']),
        'Допуски': random.choice(['API SN', 'API SP', 'ACEA C3', 'ACEA A3/B4', 'ILSAC GF-6']),
    }},
    {'id': 11, 'matcher': lambda n: 'аккумулятор' in n.lower(), 'chars': lambda: {
        'Ёмкость': f'{random.choice([45, 60, 62, 70, 75, 80, 90, 100, 110, 120])} Ah',
        'Пусковой ток': f'{random.choice([300, 400, 480, 520, 540, 580, 620, 680, 720, 760, 850, 900, 1000])} A',
        'Полярность': random.choice(['Прямая (+)', 'Обратная (-)']),
        'Тип': random.choice(['Кальциевый', 'EFB', 'AGM', 'Гибридный']),
    }},
    {'id': 12, 'matcher': lambda n: 'бампер' in n.lower() or 'крыло' in n.lower() or 'капот' in n.lower(), 'chars': lambda: {
        'Материал': random.choice(['Пластик', 'Металл', 'Карбон']),
        'Состояние': 'Новый',
        'Крепление': random.choice(['Штатное', 'Болтовое']),
    }},
    {'id': 13, 'matcher': lambda n: 'фара' in n.lower() or 'фонар' in n.lower() or 'лампа' in n.lower() or 'оптик' in n.lower(), 'chars': lambda: {
        'Тип лампы': random.choice(['Галоген', 'Ксенон', 'LED', 'Лампочка-цоколь']),
        'Цоколь': random.choice(['H1', 'H4', 'H7', 'H11', 'HB3', 'D1S']),
        'Мощность': f'{random.choice([35, 55, 60, 65, 80, 100])} Вт',
        'Световой поток': f'{random.choice([1000, 1200, 1500, 2000, 2600, 3200])} Lm',
    }},
]


def get_characteristics(name):
    for entry in CHARACTERISTICS_REGISTRY:
        if entry['matcher'](name):
            result = entry['chars']
            if callable(result):
                result = result()
            # resolve callable values
            return {k: (v() if callable(v) else v) for k, v in result.items()}
    return {'Качество': 'Аналог/OEM', 'Производитель': 'Неоригинал'}


def make_description(part_name, brand_name, car):
    desc_templates = [
        f'{part_name} {brand_name} — качественная замена оригинальной детали для {car}. '
        f'{fake.paragraph(nb_sentences=2)} Изготовлено из высококачественных материалов. '
        f'Рекомендуется замена каждые {random.randint(20, 100)} тыс. км.',

        f'Деталь {brand_name} предназначена для установки на {car}. '
        f'{part_name.lower()} — полностью соответствует спецификациям производителя. '
        f'Обеспечивает надёжную работу в любых условиях эксплуатации. '
        f'{fake.sentence(nb_words=8)}',

        f'Оригинальный аналог {part_name.lower()} {brand_name} для {car}. '
        f'{fake.paragraph(nb_sentences=1)} '
        f'Тестирование подтверждает полное соответствие заявленным характеристикам. '
        f'Гарантия {random.choice([6, 12, 24, 36])} месяцев.',

        f'{brand_name} {part_name.lower()} — отличный выбор для {car}. '
        f'{fake.sentence(nb_words=6)} '
        f'Установка не требует доработок, всё подходит "болт-он". '
        f'Поставляется в фирменной упаковке производителя.',
    ]
    return random.choice(desc_templates)


def make_short_description(part_name, brand_name, car):
    templates = [
        f'{part_name} {brand_name} для {car}. Качество OEM.',
        f'{part_name} {brand_name} — идеальная совместимость с {car}.',
        f'{part_name} {brand_name} для {car}. Высокое качество, низкая цена.',
        f'Оригинальный {part_name.lower()} {brand_name} для {car}.',
        f'{part_name} {brand_name}. Подходит для {car}. В наличии.',
        f'{brand_name} {part_name.lower()} для {car}. Гарантия {random.choice([6, 12])} мес.',
        f'{part_name} {brand_name}. Совместимость: {car}. Доставка 1 день.',
    ]
    return random.choice(templates)


MASSES = {
    'колодк': (0.8, 1.6),
    'диск': (4.5, 9.0),
    'фильтр': (0.1, 0.6),
    'свеч': (0.05, 0.08),
    'амортизат': (2.0, 5.0),
    'ремень': (0.2, 0.6),
    'ролик': (0.3, 0.5),
    'цепь': (0.3, 0.9),
    'помп': (0.6, 1.2),
    'насос': (0.8, 2.0),
    'шрус': (0.8, 1.8),
    'пыльник': (0.05, 0.15),
    'датчик': (0.05, 0.2),
    'лямбда': (0.1, 0.3),
    'радиатор': (1.5, 5.0),
    'шланг': (0.1, 0.4),
    'пружин': (1.5, 4.0),
    'рычаг': (1.5, 4.5),
    'шаров': (0.3, 0.8),
    'подшипник': (0.2, 0.8),
    'ступиц': (0.5, 1.5),
    'наконечник': (0.2, 0.5),
    'тяг': (0.3, 1.0),
    'стойк': (0.3, 0.8),
    'втулк': (0.05, 0.2),
    'масло': (0.9, 4.0),
    'жидк': (0.9, 4.0),
    'антифриз': (0.9, 5.2),
    'сцеплени': (1.0, 3.5),
    'кпп': (30, 60),
    'аккумулятор': (12, 25),
    'генератор': (5, 10),
    'стартер': (3, 6),
    'фары': (0.5, 2.5),
    'бампер': (3, 10),
    'радиатор': (1.5, 5.0),
    'термостат': (0.1, 0.3),
    'гофр': (0.3, 1.0),
    'глушител': (3, 8),
    'катализатор': (2, 5),
    'предохранитель': (0.01, 0.02),
    'реле': (0.03, 0.08),
    'проклад': (0.02, 0.2),
    'сальник': (0.02, 0.1),
}


def estimate_weight_kgs(name):
    for keyword, (lo, hi) in MASSES.items():
        if keyword in name.lower():
            return Decimal(str(random.uniform(lo, hi))).quantize(Decimal('0.01'))
    return Decimal(str(random.uniform(0.2, 3.0))).quantize(Decimal('0.01'))


def estimate_dimensions(name):
    lo = random.choice([50, 60, 70, 80, 100, 120, 150, 200])
    hi = random.choice([80, 100, 120, 150, 200, 250, 300, 400])
    dep = random.choice([20, 30, 40, 50, 60, 80, 100, 120, 150])
    return f'{lo}x{hi}x{dep} мм'


class Command(BaseCommand):
    help = 'Массовое наполнение БД — 500k+ товаров, 100k+ заказов'

    def add_arguments(self, parser):
        parser.add_argument('--products', type=int, default=500000,
                            help='Количество товаров (по умолчанию 500k)')
        parser.add_argument('--orders', type=int, default=150000,
                            help='Количество заказов (по умолчанию 150k)')
        parser.add_argument('--carts', type=int, default=20000,
                            help='Количество корзин (по умолчанию 20k)')

    def handle(self, *args, **options):
        num_products = options['products']
        num_orders = options['orders']
        num_carts = options['carts']

        self.stdout.write(f'Генерирую {num_products} товаров, {num_orders} заказов, {num_carts} корзин')
        start = timezone.now()

        # Проверка брендов и категорий
        brands = list(Brand.objects.all())
        categories = list(Category.objects.all())

        if len(brands) < 5 or len(categories) < 5:
            self.stdout.write(self.style.ERROR('Нужно сначала запустить seed_data'))
            return

        # ─── ТОВАРЫ ────────────────────────────────────────────
        self.stdout.write('  = Товары =')
        BATCH = 500

        all_car_makes = list(CAR_PARTS.keys())
        car_make_to_brand = {}
        for make in all_car_makes:
            existing = [b for b in brands if make.lower() in b.name.lower() or b.name.lower() in make.lower()]
            car_make_to_brand[make] = random.choice(existing) if existing else random.choice(brands)

        created_products = 0
        total_batches = (num_products + BATCH - 1) // BATCH

        for batch_idx in range(total_batches):
            batch_size = min(BATCH, num_products - created_products)
            products_batch = []

            for _ in range(batch_size):
                part_name = random.choice(PART_NAMES)

                # Выбираем марку авто и модель
                car_make = random.choice(all_car_makes)
                car_model = random.choice(CAR_PARTS[car_make])
                car = f'{car_make} {car_model}'

                brand = car_make_to_brand[car_make]

                # Случайная категория
                category = random.choice(categories)

                full_name = f'{part_name} {brand.name} для {car}'
                price_val = random.randint(200, 25000)
                old_price = price_val * random.choice([1, 1, 1, 1, 1, 0, 0, 0, 0]) or None
                if old_price:
                    old_price = price_val + random.randint(100, 5000)

                qty = random.randint(0, 100)
                if random.random() < 0.05:
                    qty = 0  # 5% товаров нет в наличии

                chars = get_characteristics(part_name)
                car_applicability = [car]
                if random.random() > 0.3:
                    extra_model = random.choice(CAR_PARTS[car_make])
                    if extra_model != car_model:
                        car_applicability.append(f'{car_make} {extra_model}')

                products_batch.append(Product(
                    article=f'AP-{2000000 + created_products + len(products_batch):07d}',
                    name=full_name,
                    slug=slugify(full_name + '-' + str(random.randint(10000, 999999))),
                    brand=brand,
                    category=category,
                    price=Decimal(str(price_val)),
                    old_price=Decimal(str(old_price)) if old_price else None,
                    quantity=qty,
                    is_available=qty > 0,
                    short_description=make_short_description(part_name, brand.name, car),
                    description=make_description(part_name, brand.name, car),
                    characteristics=chars,
                    car_applicability=car_applicability,
                    weight_kg=estimate_weight_kgs(part_name),
                    dimensions=estimate_dimensions(part_name),
                    label=random.choices(['none', 'hit', 'new', 'sale', 'promo'],
                                         weights=[0.65, 0.12, 0.08, 0.10, 0.05])[0],
                    is_popular=random.random() > 0.85,
                    is_new=random.random() > 0.90,
                    is_bestseller=random.random() > 0.97,
                    is_active=True,
                    is_promo=random.random() > 0.95,
                    warranty_months=random.choice([0, 6, 12, 12, 12, 24, 24, 36, 60]),
                    country_of_origin=brand.country,
                    supplier=brand.name + (' GmbH' if random.random() > 0.4 else ''),
                    views_count=random.randint(0, 5000),
                ))

            Product.objects.bulk_create(products_batch, batch_size=BATCH, ignore_conflicts=True)
            created_products += batch_size

            if (batch_idx + 1) % 200 == 0 or batch_idx == total_batches - 1:
                elapsed = (timezone.now() - start).total_seconds()
                self.stdout.write(f'    {created_products}/{num_products} ({elapsed:.0f}с)')

        self.stdout.write(f'  ✔ Товаров: {Product.objects.count()}')

        # ─── ЗАКАЗЫ ────────────────────────────────────────────
        self.stdout.write('  = Заказы =')
        BATCH_O = 500

        status_choices = ['new', 'confirmed', 'processing', 'shipped', 'delivered', 'cancelled']
        status_weights = [0.05, 0.05, 0.10, 0.10, 0.60, 0.10]  # 60% доставлены
        delivery_methods = ['courier', 'pickup', 'russian_post', 'courier', 'courier']  # чаще курьер
        payment_methods = ['cash', 'card', 'online', 'online', 'online']  # чаще онлайн
        cities = ['Москва', 'Санкт-Петербург', 'Казань', 'Новосибирск', 'Екатеринбург',
                  'Самара', 'Уфа', 'Краснодар', 'Воронеж', 'Пермь', 'Ростов-на-Дону',
                  'Омск', 'Челябинск', 'Нижний Новгород', 'Красноярск', 'Волгоград',
                  'Иркутск', 'Хабаровск', 'Владивосток', 'Саратов', 'Тюмень', 'Тольятти']

        all_products_list = list(Product.objects.filter(is_active=True)
                                 .values('id', 'article', 'price', 'name'))

        created_orders = 0
        total_order_batches = (num_orders + BATCH_O - 1) // BATCH_O

        for batch_idx in range(total_order_batches):
            batch_size = min(BATCH_O, num_orders - created_orders)
            orders_batch = []

            for i in range(batch_size):
                num_items = random.choices([1, 2, 3, 4, 5, 6], weights=[30, 25, 20, 10, 10, 5])[0]
                selected = random.sample(all_products_list, min(num_items, len(all_products_list)))

                order_items = []
                subtotal = 0
                for p in selected:
                    qty = random.choices([1, 2, 3], weights=[60, 30, 10])[0]
                    price = float(p['price'])
                    order_items.append({
                        'product_id': p['id'],
                        'article': p['article'],
                        'name': p['name'],
                        'quantity': qty,
                        'price': price,
                    })
                    subtotal += price * qty

                delivery = random.choice([0, 0, 0, 300, 500, 500, 700, 1000])
                days_ago = random.randint(0, 365)
                created = timezone.now() - timedelta(days=days_ago, hours=random.randint(0, 23))

                status = random.choices(status_choices, weights=status_weights)[0]
                status_updated = created + timedelta(hours=random.randint(1, 48))

                orders_batch.append(Order(
                    order_number=f'АП-{random.randint(1000000, 9999999)}',
                    first_name=fake.first_name(),
                    last_name=fake.last_name(),
                    patronymic=fake.middle_name(),
                    phone=f'+7{random.choice([903, 916, 925, 926, 985, 916, 910, 917])}{random.randint(1000000, 9999999)}',
                    email=f'{fake.first_name().lower()}.{fake.last_name().lower()}{random.randint(1, 99)}@gmail.com',
                    city=random.choice(cities),
                    address=f'ул. {fake.street_name()}, д. {random.randint(1, 150)}, кв. {random.randint(1, 300)}',
                    delivery_method=random.choice(delivery_methods),
                    payment_method=random.choice(payment_methods),
                    status=status,
                    items=json.dumps(order_items, ensure_ascii=False),
                    subtotal=Decimal(str(subtotal)),
                    delivery_cost=Decimal(str(delivery)),
                    total=Decimal(str(subtotal + delivery)),
                    created_at=created,
                    updated_at=status_updated,
                ))

            Order.objects.bulk_create(orders_batch, batch_size=500, ignore_conflicts=True)
            created_orders += batch_size

            if (batch_idx + 1) % 100 == 0 or batch_idx == total_order_batches - 1:
                elapsed = (timezone.now() - start).total_seconds()
                self.stdout.write(f'    {created_orders}/{num_orders} заказов ({elapsed:.0f}с)')

        self.stdout.write(f'  ✔ Заказов: {Order.objects.count()}')

        # ─── КОРЗИНЫ ───────────────────────────────────────────
        if num_carts > 0:
            self.stdout.write('  = Корзины =')
            BATCH_C = 500
            created_carts = 0
            total_cart_batches = (num_carts + BATCH_C - 1) // BATCH_C

            for batch_idx in range(total_cart_batches):
                batch_size = min(BATCH_C, num_carts - created_carts)
                carts_batch = []
                items_batch = []

                for _ in range(batch_size):
                    import hashlib
                    session_key = hashlib.md5(
                        f'{random.random()}{timezone.now()}{random.randint(0, 999999)}'.encode()
                    ).hexdigest()[:40]
                    carts_batch.append(Cart(session_key=session_key))

                Cart.objects.bulk_create(carts_batch, batch_size=500, ignore_conflicts=True)

                # Добавляем товары в соз��анные корзины
                saved_carts = list(Cart.objects.order_by('-id')[:batch_size])
                for cart in saved_carts:
                    num_items = random.randint(1, 4)
                    for _ in range(num_items):
                        p = random.choice(all_products_list)
                        items_batch.append(CartItem(
                            cart=cart,
                            product_id=p['id'],
                            quantity=random.randint(1, 4),
                            price=Decimal(str(p['price'])),
                        ))

                if items_batch:
                    CartItem.objects.bulk_create(items_batch, batch_size=1000, ignore_conflicts=True)

                created_carts += batch_size
                if (batch_idx + 1) % 40 == 0 or batch_idx == total_cart_batches - 1:
                    elapsed = (timezone.now() - start).total_seconds()
                    self.stdout.write(f'    {created_carts}/{num_carts} корзин ({elapsed:.0f}с)')

            self.stdout.write(f'  ✔ Корзин: {Cart.objects.count()}')

        # Итог
        elapsed = (timezone.now() - start).total_seconds()

        # Размер БД
        import subprocess
        try:
            result = subprocess.run(
                ['du', '-sh', '/var/lib/postgresql/data'],
                capture_output=True, text=True, timeout=5
            )
            pg_size = result.stdout.strip()
        except:
            pg_size = 'N/A'

        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Готово! Время: {elapsed:.0f} секунд'
        ))
        self.stdout.write(f'   Товаров: {Product.objects.count()}')
        self.stdout.write(f'   Заказов: {Order.objects.count()}')
        self.stdout.write(f'   Корзин: {Cart.objects.count()}')
        self.stdout.write(f'   Размер БД на диске: {pg_size}')
