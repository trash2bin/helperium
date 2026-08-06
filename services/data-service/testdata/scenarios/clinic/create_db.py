"""
Create a medical clinic database — realistic clinic with doctors, patients, appointments.

Usage:
    python create_db.py                          # → ./data.db
    SHOP_DB=/path/to/data.db python create_db.py  # → custom path
"""

import os
import sqlite3
from pathlib import Path

DB = Path(os.environ.get("SHOP_DB", Path(__file__).resolve().parent / "data.db"))
DB.unlink(missing_ok=True)
DB.parent.mkdir(parents=True, exist_ok=True)

db = sqlite3.connect(str(DB))

db.executescript("""
    CREATE TABLE departments (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        location TEXT
    );

    CREATE TABLE doctors (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        specialization TEXT,
        department_id INTEGER,
        experience INTEGER,
        phone TEXT,
        rating REAL DEFAULT 4.0,
        FOREIGN KEY (department_id) REFERENCES departments(id)
    );

    CREATE TABLE patients (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT,
        birth_date TEXT,
        insurance_policy TEXT UNIQUE,
        city TEXT
    );

    CREATE TABLE appointments (
        id INTEGER PRIMARY KEY,
        doctor_id INTEGER NOT NULL,
        patient_id INTEGER NOT NULL,
        appointment_date TEXT NOT NULL,
        status TEXT DEFAULT 'scheduled',
        reason TEXT,
        diagnosis TEXT,
        notes TEXT,
        FOREIGN KEY (doctor_id) REFERENCES doctors(id),
        FOREIGN KEY (patient_id) REFERENCES patients(id)
    );

    CREATE TABLE prescriptions (
        id INTEGER PRIMARY KEY,
        appointment_id INTEGER NOT NULL,
        medication TEXT NOT NULL,
        dosage TEXT,
        duration TEXT,
        FOREIGN KEY (appointment_id) REFERENCES appointments(id)
    );

    -- ======== DATA ========

    -- Departments
    INSERT INTO departments VALUES (1, 'Терапия', '2 этаж, крыло А');
    INSERT INTO departments VALUES (2, 'Хирургия', '3 этаж, крыло Б');
    INSERT INTO departments VALUES (3, 'Кардиология', '2 этаж, крыло Б');
    INSERT INTO departments VALUES (4, 'Неврология', '4 этаж, крыло А');
    INSERT INTO departments VALUES (5, 'Офтальмология', '4 этаж, крыло Б');

    -- Doctors
    INSERT INTO doctors VALUES (1, 'Петрова Анна Сергеевна', 'Терапевт', 1, 12, '+7(495)111-11-11', 4.8);
    INSERT INTO doctors VALUES (2, 'Смирнов Иван Александрович', 'Кардиолог', 3, 20, '+7(495)222-22-22', 4.9);
    INSERT INTO doctors VALUES (3, 'Кузнецов Дмитрий Олегович', 'Хирург', 2, 15, '+7(495)333-33-33', 4.7);
    INSERT INTO doctors VALUES (4, 'Белова Екатерина Андреевна', 'Невролог', 4, 8, '+7(495)444-44-44', 4.6);
    INSERT INTO doctors VALUES (5, 'Морозов Алексей Игоревич', 'Офтальмолог', 5, 10, '+7(495)555-55-55', 4.5);
    INSERT INTO doctors VALUES (6, 'Иванова Мария Владимировна', 'Терапевт', 1, 5, '+7(495)666-66-66', 4.3);
    INSERT INTO doctors VALUES (7, 'Соколов Павел Геннадьевич', 'Кардиолог', 3, 7, '+7(495)777-77-77', 4.4);
    INSERT INTO doctors VALUES (8, 'Козлова Ольга Дмитриевна', 'Хирург', 2, 18, '+7(495)888-88-88', 4.9);
    INSERT INTO doctors VALUES (9, 'Новиков Артём Борисович', 'Невролог', 4, 3, '+7(495)999-99-99', 4.2);
    INSERT INTO doctors VALUES (10, 'Волкова Наталья Игоревна', 'Офтальмолог', 5, 14, '+7(495)000-00-00', 4.7);

    -- Patients
    INSERT INTO patients VALUES (1, 'Алексей Викторович Кузнецов', '+7(916)123-45-67', '1985-03-15', 'POL-2025-000001', 'Москва');
    INSERT INTO patients VALUES (2, 'Ольга Игоревна Фёдорова', '+7(903)234-56-78', '1990-07-22', 'POL-2025-000002', 'Москва');
    INSERT INTO patients VALUES (3, 'Михаил Дмитриевич Соболев', '+7(925)345-67-89', '1978-11-05', 'POL-2025-000003', 'СПб');
    INSERT INTO patients VALUES (4, 'Татьяна Владимировна Громова', '+7(916)456-78-90', '2000-01-30', 'POL-2025-000004', 'Казань');
    INSERT INTO patients VALUES (5, 'Иван Петрович Медведев', '+7(985)567-89-01', '1955-09-12', 'POL-2025-000005', 'Москва');
    INSERT INTO patients VALUES (6, 'Екатерина Андреевна Лисицына', '+7(926)678-90-12', '1995-05-18', 'POL-2025-000006', 'Екатеринбург');
    INSERT INTO patients VALUES (7, 'Денис Олегович Морозов', '+7(968)789-01-23', '2002-12-25', 'POL-2025-000007', 'Новосибирск');
    INSERT INTO patients VALUES (8, 'Наталья Павловна Виноградова', '+7(977)890-12-34', '1982-04-08', 'POL-2025-000008', 'СПб');
    INSERT INTO patients VALUES (9, 'Артём Сергеевич Попов', '+7(901)901-23-45', '1970-08-20', 'POL-2025-000009', 'Москва');
    INSERT INTO patients VALUES (10, 'Светлана Алексеевна Крылова', '+7(926)012-34-56', '1998-06-14', 'POL-2025-000010', 'Казань');
    INSERT INTO patients VALUES (11, 'Григорий Викторович Фёдоров', '+7(985)111-22-33', '1988-02-28', 'POL-2025-000011', 'СПб');
    INSERT INTO patients VALUES (12, 'Лариса Юрьевна Белова', '+7(916)222-33-44', '1965-10-03', 'POL-2025-000012', 'Москва');
    INSERT INTO patients VALUES (13, 'Владимир Ильич Крылов', '+7(968)333-44-55', '1975-07-15', 'POL-2025-000013', 'Краснодар');
    INSERT INTO patients VALUES (14, 'Алёна Дмитриевна Панина', '+7(977)444-55-66', '2001-04-22', 'POL-2025-000014', 'Самара');
    INSERT INTO patients VALUES (15, 'Виктор Николаевич Зайцев', '+7(901)555-66-77', '1960-01-11', 'POL-2025-000015', 'Москва');
    INSERT INTO patients VALUES (16, 'Зоя Игоревна Семёнова', '+7(926)666-77-88', '1992-09-05', 'POL-2025-000016', 'СПб');
    INSERT INTO patients VALUES (17, 'Роман Борисович Тимофеев', '+7(985)777-88-99', '1980-12-30', 'POL-2025-000017', 'Воронеж');
    INSERT INTO patients VALUES (18, 'Инна Валерьевна Ковалёва', '+7(916)888-99-00', '1993-03-19', 'POL-2025-000018', 'Москва');
    INSERT INTO patients VALUES (19, 'Даниил Андреевич Сорокин', '+7(968)999-00-11', '2003-08-14', 'POL-2025-000019', 'Челябинск');
    INSERT INTO patients VALUES (20, 'Вера Павловна Романова', '+7(977)000-11-22', '1958-06-06', 'POL-2025-000020', 'Москва');

    -- Appointments
    INSERT INTO appointments VALUES (1, 1, 1, '2025-01-15 09:00', 'completed', 'Головные боли, головокружение', 'Остеохондроз шейного отдела позвоночника', 'Назначено МРТ шейного отдела');
    INSERT INTO appointments VALUES (2, 2, 5, '2025-01-15 10:00', 'completed', 'Давление скачет, тошнота', 'Артериальная гипертензия 1 степени', 'Назначено суточное мониторирование АД');
    INSERT INTO appointments VALUES (3, 3, 3, '2025-01-16 11:00', 'completed', 'Травма колена при падении', 'Растяжение связок коленного сустава', 'Наложена фиксирующая повязка');
    INSERT INTO appointments VALUES (4, 4, 2, '2025-01-16 14:00', 'completed', 'Болит спина после тренировки', 'Мышечно-тонический синдром', 'Рекомендован покой и НПВС');
    INSERT INTO appointments VALUES (5, 5, 8, '2025-01-17 09:30', 'completed', 'Плохо вижу вдаль', 'Миопия средней степени', 'Выписаны очки');
    INSERT INTO appointments VALUES (6, 10, 8, '2025-01-17 10:00', 'completed', 'Проверка зрения для получения прав', 'Острота зрения 1.0 оба глаза', 'Здоров');
    INSERT INTO appointments VALUES (7, 1, 4, '2025-01-20 08:00', 'completed', 'Насморк 2 недели не проходит', 'Гайморит двухсторонний', 'Назначен антибиотик');
    INSERT INTO appointments VALUES (8, 6, 4, '2025-01-20 08:30', 'completed', 'Повторный прием после лечения гайморита', 'Выздоровление, пазухи чистые', '');
    INSERT INTO appointments VALUES (9, 2, 12, '2025-01-20 11:00', 'completed', 'Плановый осмотр, жалоб нет', 'Ишемическая болезнь сердца, стабильная стенокардия ФК II', 'Коррекция терапии');
    INSERT INTO appointments VALUES (10, 7, 12, '2025-01-20 11:30', 'completed', 'Результаты холтера', 'ЖЭС 500/сут, значимых нарушений нет', 'Увеличена доза бета-блокатора');
    INSERT INTO appointments VALUES (11, 1, 9, '2025-01-22 09:00', 'completed', 'Общая слабость, быстрая утомляемость', 'Железодефицитная анемия легкой степени', 'Назначены препараты железа');
    INSERT INTO appointments VALUES (12, 3, 9, '2025-01-22 10:00', 'completed', 'Направление на ФГДС', 'Поверхностный гастрит, H. pylori обнаружен', 'Эрадикационная терапия');
    INSERT INTO appointments VALUES (13, 8, 11, '2025-01-25 12:00', 'completed', 'Направление на удаление аппендикса', 'Острый аппендицит, катаральная форма', 'Аппендэктомия, лапароскопия');
    INSERT INTO appointments VALUES (14, 8, 11, '2025-01-28 10:00', 'completed', 'Снятие швов после аппендэктомии', 'Послеоперационный рубец без признаков воспаления', 'Швы сняты, заживление первичным натяжением');
    INSERT INTO appointments VALUES (15, 4, 7, '2025-01-25 14:00', 'completed', 'Головные боли напряжения', 'Цефалгия напряжения, эпизодическая форма', 'Триггерные точки в воротниковой зоне');
    INSERT INTO appointments VALUES (16, 9, 7, '2025-01-25 14:30', 'completed', 'Повторный прием, головные боли не проходят', 'Мигрень с аурой', 'Назначены триптаны');
    INSERT INTO appointments VALUES (17, 1, 13, '2025-02-01 09:00', 'completed', 'Кашель сухой 5 дней, температура 37.5', 'ОРВИ, острый трахеит', 'Симптоматическое лечение');
    INSERT INTO appointments VALUES (18, 1, 13, '2025-02-08 10:00', 'completed', 'Кашель не проходит, стал влажный', 'Поствирусный кашель, исключить коклюш', 'Анализ на коклюш');
    INSERT INTO appointments VALUES (19, 5, 15, '2025-02-01 10:00', 'completed', 'Проверка зрения, ухудшение за последний год', 'Миопия высокой степени, пресбиопия начальная', 'Новые очки + рекомендации');
    INSERT INTO appointments VALUES (20, 5, 15, '2025-02-01 10:15', 'completed', 'Катаракта на правом глазу', 'Начальная катаракта правого глаза', 'Наблюдение, повтор через 6 мес');
    INSERT INTO appointments VALUES (21, 10, 20, '2025-02-03 11:00', 'completed', 'Плановый осмотр глазного дна', 'Ангиопатия сетчатки по гипертоническому типу', 'Консультация кардиолога');
    INSERT INTO appointments VALUES (22, 2, 15, '2025-02-03 12:00', 'completed', 'Консультация по результатам ЭКГ', 'Гипертрофия левого желудочка, диастолическая дисфункция', 'Коррекция терапии');
    INSERT INTO appointments VALUES (23, 6, 16, '2025-02-05 09:00', 'completed', 'Болит горло, налёт на миндалинах', 'Острый тонзиллит (ангина)', 'Антибиотик, полоскания');
    INSERT INTO appointments VALUES (24, 6, 18, '2025-02-05 10:00', 'completed', 'Температура 38, ломит тело', 'Грипп A, средней тяжести', 'Противовирусное, симптоматическое');
    INSERT INTO appointments VALUES (25, 1, 18, '2025-02-07 09:00', 'completed', 'Температура спала, но сильный кашель', 'Поствирусная пневмония (подозрение)', 'Рентгенография грудной клетки');
    INSERT INTO appointments VALUES (26, 1, 10, '2025-02-10 10:00', 'completed', 'Плановый медосмотр', 'Здоров', 'Рекомендовано обследование раз в год');
    INSERT INTO appointments VALUES (27, 3, 14, '2025-02-10 11:00', 'completed', 'Травма запястья, упала на руку', 'Перелом лучевой кости без смещения', 'Гипсовая иммобилизация');
    INSERT INTO appointments VALUES (28, 3, 14, '2025-02-17 11:00', 'scheduled', 'Контроль после перелома', NULL, NULL);
    INSERT INTO appointments VALUES (29, 2, 1, '2025-02-12 10:00', 'completed', 'Плановое обследование после перенесенного COVID', 'Миокардит (подозрение), назначена МРТ сердца', NULL);
    INSERT INTO appointments VALUES (30, 2, 1, '2025-02-19 10:00', 'scheduled', 'Результаты МРТ сердца', NULL, NULL);
    INSERT INTO appointments VALUES (31, 4, 8, '2025-02-13 14:00', 'completed', 'Бессонница, тревожность, апатия', 'Депрессивный эпизод средней тяжести', NULL);
    INSERT INTO appointments VALUES (32, 4, 8, '2025-02-27 14:00', 'scheduled', 'Психотерапия, контроль', NULL, NULL);
    INSERT INTO appointments VALUES (33, 7, 5, '2025-02-14 11:00', 'completed', 'Одышка при ходьбе, отеки ног', 'Хроническая сердечная недостаточность 2А', 'Диуретики, коррекция терапии');
    INSERT INTO appointments VALUES (34, 7, 5, '2025-02-28 11:00', 'scheduled', 'Контроль веса и диуреза', NULL, NULL);
    INSERT INTO appointments VALUES (35, 1, 6, '2025-02-15 09:00', 'completed', 'Сезонная аллергия', 'Поллиноз, обострение', NULL);
    INSERT INTO appointments VALUES (36, 5, 6, '2025-02-15 09:30', 'completed', 'Аллергический конъюнктивит', 'Аллергический конъюнктивит, обострение', 'Противоаллергические капли');
    INSERT INTO appointments VALUES (37, 10, 3, '2025-02-18 10:00', 'completed', 'Глаза болят, красные после работы за компьютером', 'Синдром сухого глаза', 'Увлажняющие капли, режим работы');
    INSERT INTO appointments VALUES (38, 6, 17, '2025-02-20 09:00', 'completed', 'Боль в правом боку, тошнота', 'Острый холецистит (подозрение)', 'УЗИ брюшной полости');
    INSERT INTO appointments VALUES (39, 6, 17, '2025-02-21 10:00', 'completed', 'Результаты УЗИ', 'Желчнокаменная болезнь, конкременты 5мм', NULL);
    INSERT INTO appointments VALUES (40, 3, 17, '2025-02-24 11:00', 'scheduled', 'Консультация хирурга по удалению желчного', NULL, NULL);
    INSERT INTO appointments VALUES (41, 3, 10, '2025-02-25 12:00', 'scheduled', 'Плановое удаление родинки', NULL, NULL);
    INSERT INTO appointments VALUES (42, 10, 19, '2025-02-26 09:00', 'scheduled', 'Проверка зрения, компьютерная коррекция', NULL, NULL);

    -- Prescriptions
    INSERT INTO prescriptions VALUES (1, 1, 'Мельдоний 500мг', '1 капс 2 раза в день', '1 месяц');
    INSERT INTO prescriptions VALUES (2, 2, 'Лизиноприл 10мг', '1 таб утром', 'постоянно');
    INSERT INTO prescriptions VALUES (3, 2, 'Конкор 2.5мг', '1 таб утром', 'постоянно');
    INSERT INTO prescriptions VALUES (4, 3, 'Диклофенак гель 5%', 'Наружно 2 раза в день', '10 дней');
    INSERT INTO prescriptions VALUES (5, 4, 'Ибупрофен 400мг', '1 таб при боли, до 3 раз в день', '5 дней');
    INSERT INTO prescriptions VALUES (6, 5, 'Атропин 0.5%', 'По 1 капле 1 раз в день', 'постоянно');
    INSERT INTO prescriptions VALUES (7, 7, 'Амоксициллин 500мг', '1 капс 3 раза в день', '7 дней');
    INSERT INTO prescriptions VALUES (8, 7, 'Ацетилцистеин 200мг', '1 пакет 2 раза в день', '10 дней');
    INSERT INTO prescriptions VALUES (9, 9, 'Нитроглицерин 500мкг', '1 таб под язык при боли', 'по необходимости');
    INSERT INTO prescriptions VALUES (10, 9, 'Аспирин кардио 100мг', '1 таб вечером', 'постоянно');
    INSERT INTO prescriptions VALUES (11, 11, 'Феррум Лек 100мг', '1 таб 2 раза в день', '3 месяца');
    INSERT INTO prescriptions VALUES (12, 12, 'Омепразол 20мг', 'утром до еды', '14 дней');
    INSERT INTO prescriptions VALUES (13, 12, 'Амоксициллин 1000мг', '2 раза в день', '10 дней (эрадикация)');
    INSERT INTO prescriptions VALUES (14, 12, 'Кларитромицин 500мг', '2 раза в день', '10 дней (эрадикация)');
    INSERT INTO prescriptions VALUES (15, 15, 'Дротаверин 40мг', 'при боли до 3 раз в день', '5 дней');
    INSERT INTO prescriptions VALUES (16, 16, 'Суматриптан 50мг', 'при начале приступа', 'не более 2 таб/сут');
    INSERT INTO prescriptions VALUES (17, 17, 'Парацетамол 500мг', 'при температуре выше 38', 'не более 4 таб/сут');
    INSERT INTO prescriptions VALUES (18, 18, 'Коделак Бронхо', '1 таб 3 раза в день', '7 дней');
    INSERT INTO prescriptions VALUES (19, 23, 'Амоксициллин 500мг', '1 капс 3 раза в день', '7 дней');
    INSERT INTO prescriptions VALUES (20, 23, 'Мирамистин спрей', 'орошение горла 3 раза в день', '7 дней');
    INSERT INTO prescriptions VALUES (21, 24, 'Осельтамивир 75мг', '1 капс 2 раза в день', '5 дней');
    INSERT INTO prescriptions VALUES (22, 24, 'Ибупрофен 400мг', '1 таб при температуре', 'по необходимости');
    INSERT INTO prescriptions VALUES (23, 33, 'Фуросемид 40мг', '1 таб утром', '7 дней');
    INSERT INTO prescriptions VALUES (24, 33, 'Верошпирон 50мг', '1 таб утром', '14 дней');
    INSERT INTO prescriptions VALUES (25, 33, 'Эналаприл 5мг', '1 таб 2 раза в день', 'постоянно');
""")

db.commit()

# Verify
print(f"✅ Created clinic database: {DB} ({DB.stat().st_size} bytes)")
for table in ['departments', 'doctors', 'patients', 'appointments', 'prescriptions']:
    cnt = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"   {table}: {cnt}")

# Print summary
print("\nDoctors by specialization:")
cur = db.execute("SELECT specialization, COUNT(*) FROM doctors GROUP BY specialization")
for r in cur:
    print(f"  {r[0]}: {r[1]}")

print("\nAppointments by status:")
cur = db.execute("SELECT status, COUNT(*) FROM appointments GROUP BY status")
for r in cur:
    print(f"  {r[0]}: {r[1]}")

print("\nAppointments by diagnosis:")
cur = db.execute("SELECT diagnosis FROM appointments WHERE diagnosis IS NOT NULL AND diagnosis != ''")
diagnoses = [r[0] for r in cur.fetchall()]
for d in diagnoses:
    print(f"  - {d}")
