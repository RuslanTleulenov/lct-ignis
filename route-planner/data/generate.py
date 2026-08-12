"""Генератор синтетического датасета для сервиса планирования маршрутов инженеров.

Организаторы ЛЦТ ресурсы не выдали, поэтому датасет генерируем сами. Схема
намеренно «каноническая» — если реальные данные всё-таки появятся, их можно
подложить, не трогая солвер.

Только стандартная библиотека: запускается любым Python 3.10+ без установки
зависимостей.

Запуск:
    python generate.py                          # 140 заявок, 14 инженеров
    python generate.py --jobs 300 --engineers 25
    python generate.py --seed 42 --date 2026-08-12 --out seed/

На выходе в каталоге --out:
    work_types.csv      типы работ + матрица «работа → оборудование»
    equipment.csv       оборудование, количество, где хранится
    warehouses.csv      склады
    engineers.csv       инженеры: навыки, смена, транспорт, оборудование на руках
    jobs.csv            заявки: адрес, координаты, окно, приоритет, SLA
    events.csv          события дня для симулятора перепланирования
    snapshot.json       всё то же одним файлом — его читает backend
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import date as Date, datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------
# Справочник: специализации
# --------------------------------------------------------------------------

SPECIALIZATIONS = {
    "network": "Сети и СКС",
    "power": "Электропитание",
    "hvac": "Вентиляция и кондиционирование",
    "security": "Системы безопасности",
    "it": "Рабочие места и оргтехника",
}

# --------------------------------------------------------------------------
# Справочник: оборудование
#   bulky=True — габаритное, физически не увезти пешком/на метро, нужен авто.
#              Это единственное жёсткое «нет» по оборудованию: пеший инженер
#              такую заявку взять не может ни при каких условиях.
#   stock      — где лежит резерв:
#                  "all"  — стандартный инструмент, есть на каждом складе;
#                  "WH-*" — редкий прибор, хранится централизованно в одном месте.
#              Если нужного инструмента нет на руках, инженер заезжает за ним
#              утром на склад. Поэтому оборудование — это не тупик, а стоимость
#              (крюк в маршруте), и такой компромисс солверу интересно разрешать.
#   units      — сколько экземпляров всего. Для редких приборов это дефицитный
#              ресурс, за который заявки конкурируют.
#
# Редкие приборы сгруппированы по специализациям (оптика — на Северном, силовые
# измерения — на Южном, СКУД — на Восточном). Это не косметика: иначе комплект
# для одной работы оказывался бы разбросан по двум складам, а мотаться за день
# по двум складам инженер не будет.
# --------------------------------------------------------------------------

EQUIPMENT = [
    # id,                название,                              bulky, units, склад
    ("EQ-OTDR",   "Оптический рефлектометр OTDR",                False, 2,  "WH-N"),
    ("EQ-SPLICE", "Сварочный аппарат для оптоволокна",           False, 2,  "WH-N"),
    ("EQ-CRIMP",  "Обжимной инструмент RJ-45",                   False, 10, "all"),
    ("EQ-LANTEST","Кабельный тестер",                            False, 8,  "all"),
    ("EQ-LAPTOP", "Ноутбук с ПО настройки",                      False, 20, "all"),
    ("EQ-MULTI",  "Мультиметр",                                  False, 12, "all"),
    ("EQ-MEGGER", "Мегаомметр",                                  False, 2,  "WH-S"),
    ("EQ-THERMAL","Тепловизор",                                  False, 2,  "WH-S"),
    ("EQ-PPE",    "Комплект СИЗ для работ под напряжением",       False, 8,  "all"),
    ("EQ-TROLLEY","Гидравлическая тележка",                      True,  5,  "all"),
    ("EQ-GAUGE",  "Манометрический коллектор",                   False, 7,  "all"),
    ("EQ-VACUUM", "Вакуумный насос",                             True,  5,  "all"),
    ("EQ-FREON",  "Баллон с хладагентом",                        True,  6,  "all"),
    ("EQ-DRILL",  "Перфоратор с набором буров",                  False, 10, "all"),
    ("EQ-LADDER", "Стремянка 3 м",                               True,  7,  "all"),
    ("EQ-PROG",   "Программатор СКУД",                           False, 2,  "WH-E"),
    ("EQ-SCREW",  "Набор отвёрток и бит",                        False, 20, "all"),
]

# --------------------------------------------------------------------------
# Справочник: типы работ = матрица совместимости «работа → оборудование»
#   (id, название, специализация, мин. уровень, базовая длительность, оборудование)
# --------------------------------------------------------------------------

WORK_TYPES = [
    ("WT-01", "Диагностика обрыва оптической линии", "network",  3,  75,
     ["EQ-OTDR", "EQ-SPLICE"]),
    ("WT-02", "Монтаж СКС: розетки и патч-панель",   "network",  2, 105,
     ["EQ-CRIMP", "EQ-LANTEST", "EQ-DRILL"]),
    ("WT-03", "Настройка коммутатора / маршрутизатора", "network", 3, 45,
     ["EQ-LAPTOP"]),
    ("WT-04", "Замена источника бесперебойного питания", "power", 2,  70,
     ["EQ-MULTI", "EQ-TROLLEY"]),
    ("WT-05", "Ремонт вводного электрощита",         "power",    4, 135,
     ["EQ-MULTI", "EQ-MEGGER", "EQ-PPE"]),
    ("WT-06", "Плановое ТО электрощитовой",          "power",    3,  90,
     ["EQ-MULTI", "EQ-THERMAL"]),
    ("WT-07", "Техобслуживание кондиционера",        "hvac",     2,  60,
     ["EQ-GAUGE", "EQ-VACUUM"]),
    ("WT-08", "Дозаправка системы хладагентом",      "hvac",     3,  70,
     ["EQ-GAUGE", "EQ-FREON"]),
    ("WT-09", "Монтаж камеры видеонаблюдения",       "security", 2,  75,
     ["EQ-DRILL", "EQ-LADDER", "EQ-LAPTOP"]),
    ("WT-10", "Настройка контроллера СКУД",          "security", 3,  85,
     ["EQ-LAPTOP", "EQ-PROG"]),
    ("WT-11", "Замена и настройка рабочего места",   "it",       1,  40,
     ["EQ-LAPTOP", "EQ-SCREW"]),
    ("WT-12", "Аварийное восстановление канала связи", "network", 4, 100,
     ["EQ-OTDR", "EQ-SPLICE", "EQ-LAPTOP"]),
]

# --------------------------------------------------------------------------
# Склады
# --------------------------------------------------------------------------

WAREHOUSES = [
    ("WH-N", "Склад «Северный»",  55.8567, 37.5410, "Дмитровское ш., 87",   "07:00", "20:00"),
    ("WH-S", "Склад «Южный»",     55.6212, 37.6081, "Варшавское ш., 148",   "07:00", "20:00"),
    ("WH-E", "Склад «Восточный»", 55.7862, 37.7981, "Щёлковское ш., 100",   "08:00", "19:00"),
]

# --------------------------------------------------------------------------
# География: районы Москвы.
#   (название, lat, lon, вес плотности заявок, разброс в км, улицы)
# Разброс + вес дают правдоподобную картину: в центре густо, на окраинах редко.
# --------------------------------------------------------------------------

DISTRICTS = [
    ("Тверской",       55.7658, 37.6058, 10, 1.2, ["1-я Тверская-Ямская ул.", "Оружейный пер.", "ул. Долгоруковская"]),
    ("Басманный",      55.7690, 37.6700,  9, 1.5, ["ул. Бакунинская", "Спартаковская пл.", "Старая Басманная ул."]),
    ("Замоскворечье",  55.7361, 37.6289,  8, 1.3, ["ул. Большая Ордынка", "Пятницкая ул.", "Кожевническая ул."]),
    ("Пресненский",    55.7614, 37.5539,  9, 1.6, ["Пресненская наб.", "ул. 1905 года", "Шмитовский пр-д"]),
    ("Хамовники",      55.7304, 37.5749,  7, 1.5, ["Комсомольский пр-т", "ул. Усачёва", "Фрунзенская наб."]),
    ("Арбат",          55.7494, 37.5905,  5, 0.9, ["ул. Новый Арбат", "Смоленская пл.", "Денежный пер."]),
    ("Сокол",          55.8046, 37.5145,  6, 1.6, ["Ленинградский пр-т", "ул. Алабяна", "Чапаевский пер."]),
    ("Хорошёвский",    55.7783, 37.5205,  6, 1.7, ["Хорошёвское ш.", "ул. Куусинена", "Полины Осипенко ул."]),
    ("Останкинский",   55.8214, 37.6122,  6, 1.8, ["ул. Академика Королёва", "Звёздный б-р", "Новомосковская ул."]),
    ("Марьина Роща",   55.7936, 37.6118,  5, 1.4, ["Шереметьевская ул.", "ул. Двинцев", "Сущёвский Вал"]),
    ("Сокольники",     55.7894, 37.6790,  5, 1.6, ["Русаковская ул.", "Стромынка ул.", "Богородское ш."]),
    ("Измайлово",      55.7887, 37.7860,  6, 2.2, ["Измайловский б-р", "Первомайская ул.", "Сиреневый б-р"]),
    ("Гольяново",      55.8140, 37.8100,  4, 2.0, ["ул. Хабаровская", "Уссурийская ул.", "Курганская ул."]),
    ("Отрадное",       55.8630, 37.6030,  5, 2.1, ["Алтуфьевское ш.", "ул. Декабристов", "Хачатуряна ул."]),
    ("Медведково",     55.8870, 37.6560,  4, 2.0, ["Заревый пр-д", "ул. Полярная", "Осташковская ул."]),
    ("Митино",         55.8460, 37.3620,  4, 2.2, ["Пятницкое ш.", "ул. Барышиха", "Дубравная ул."]),
    ("Строгино",       55.8030, 37.4020,  4, 2.0, ["Строгинский б-р", "ул. Кулакова", "Таллинская ул."]),
    ("Кунцево",        55.7300, 37.4100,  5, 2.1, ["Рублёвское ш.", "ул. Молдавская", "Ярцевская ул."]),
    ("Тропарёво",      55.6503, 37.4880,  5, 2.0, ["Ленинский пр-т", "ул. Академика Анохина", "Никулинская ул."]),
    ("Ясенево",        55.6045, 37.5330,  4, 2.0, ["Новоясеневский пр-т", "ул. Паустовского", "Соловьиный пр-д"]),
    ("Чертаново",      55.6244, 37.6060,  6, 2.3, ["Чертановская ул.", "Балаклавский пр-т", "Кировоградская ул."]),
    ("Царицыно",       55.6156, 37.6684,  5, 2.0, ["Каспийская ул.", "ул. Бехтерева", "Веселая ул."]),
    ("Люблино",        55.6764, 37.7606,  6, 2.1, ["Люблинская ул.", "ул. Краснодарская", "Совхозная ул."]),
    ("Печатники",      55.6905, 37.7280,  4, 1.8, ["ул. Гурьянова", "Шоссейная ул.", "Полбина ул."]),
]

# --------------------------------------------------------------------------
# Люди и организации
# --------------------------------------------------------------------------

SURNAMES = [
    "Иванов", "Петров", "Смирнов", "Кузнецов", "Соколов", "Попов", "Лебедев",
    "Козлов", "Новиков", "Морозов", "Волков", "Алексеев", "Егоров", "Никитин",
    "Захаров", "Орлов", "Тимофеев", "Фомин", "Гусев", "Романов", "Богданов",
    "Киселёв", "Макаров", "Андреев", "Ковалёв", "Ильин", "Зайцев", "Медведев",
]
NAMES_M = [
    "Алексей", "Дмитрий", "Сергей", "Андрей", "Максим", "Игорь", "Николай",
    "Павел", "Виктор", "Роман", "Артём", "Кирилл", "Олег", "Денис",
]
NAMES_F = ["Анна", "Мария", "Ольга", "Елена", "Ирина", "Наталья", "Татьяна"]

CUSTOMERS = [
    "БЦ «Меркурий»", "ТЦ «Северный»", "Поликлиника №34", "Школа №1210",
    "Отель «Заря»", "Логистический центр «Восток»", "Банк «Развитие», офис",
    "Ресторан «Веранда»", "Фитнес-клуб «Атлант»", "Аптека «Здоровье»",
    "Технопарк «Прогресс»", "Гипермаркет «Волна»", "Автосалон «Мотор»",
    "Детский сад №78", "Библиотека им. Чехова", "Типография «Оттиск»",
    "Складской комплекс «Тайга»", "Клиника «Медлайн»", "Коворкинг «Этаж»",
    "Кинотеатр «Экран»",
]

# --------------------------------------------------------------------------
# Геометрия
# --------------------------------------------------------------------------

EARTH_R_KM = 6371.0


def offset_km(lat: float, lon: float, dx_km: float, dy_km: float) -> tuple[float, float]:
    """Сдвинуть точку на dx км на восток и dy км на север."""
    dlat = dy_km / 111.32
    dlon = dx_km / (111.32 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_R_KM * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------
# Генерация
# --------------------------------------------------------------------------

def pick_district(rnd: random.Random):
    weights = [d[3] for d in DISTRICTS]
    return rnd.choices(DISTRICTS, weights=weights, k=1)[0]


def random_point_in(rnd: random.Random, district) -> tuple[float, float, str]:
    """Точка внутри района + правдоподобный адрес."""
    _, lat, lon, _, spread, streets = district
    dx = rnd.gauss(0, spread * 0.6)
    dy = rnd.gauss(0, spread * 0.6)
    # обрезаем хвосты гауссианы, чтобы точки не улетали за город
    dx = max(-spread * 1.8, min(spread * 1.8, dx))
    dy = max(-spread * 1.8, min(spread * 1.8, dy))
    plat, plon = offset_km(lat, lon, dx, dy)
    street = rnd.choice(streets)
    house = rnd.randint(1, 68)
    korp = f", к. {rnd.randint(1, 4)}" if rnd.random() < 0.25 else ""
    return round(plat, 6), round(plon, 6), f"{street}, д. {house}{korp}"


def full_name(rnd: random.Random) -> str:
    if rnd.random() < 0.2:
        return f"{rnd.choice(SURNAMES)}а {rnd.choice(NAMES_F)}"
    return f"{rnd.choice(SURNAMES)} {rnd.choice(NAMES_M)}"


def gen_engineers(rnd: random.Random, n: int) -> list[dict]:
    """Инженеры: навыки, смена, транспорт, оборудование на руках.

    Состав подбирается так, чтобы по каждой специализации были и «дорогие»
    высокие уровни (их мало — вокруг них и возникает конкуренция за ресурс),
    и массовые 2-3 уровня.
    """
    # Целевой профиль команды: специализация → уровни.
    # Состав подобран под спрос из gen_jobs: на каждую критичную специализацию
    # минимум ДВА старших. С одним старшим на 9 ч работ при смене 8 ч заявки
    # сыпались бы из-за перекоса штата, а не из-за маршрутов — и демо врало бы.
    plan: list[tuple[str, int]] = []
    for spec, levels in {
        "network":  [4, 4, 3, 3, 2],
        "power":    [4, 4, 3, 2],
        "hvac":     [3, 3, 2],
        "security": [3, 3, 2],
        "it":       [2, 2, 1],
    }.items():
        plan += [(spec, lv) for lv in levels]
    while len(plan) < n:                       # добираем середняками
        spec = rnd.choice(list(SPECIALIZATIONS))
        plan.append((spec, rnd.choice([2, 3])))
    if n < len(plan):
        # урезаем с хвоста уровней, а не случайно: старших теряем последними
        plan.sort(key=lambda p: -p[1])
        plan = plan[:n]
    rnd.shuffle(plan)

    # Базовый набор инструмента, который инженер возит с собой
    onboard_by_spec = {
        "network":  ["EQ-CRIMP", "EQ-LANTEST", "EQ-LAPTOP", "EQ-SCREW"],
        "power":    ["EQ-MULTI", "EQ-PPE", "EQ-SCREW"],
        "hvac":     ["EQ-GAUGE", "EQ-SCREW"],
        "security": ["EQ-DRILL", "EQ-LAPTOP", "EQ-SCREW"],
        "it":       ["EQ-LAPTOP", "EQ-SCREW"],
    }
    shifts = [("08:00", "17:00"), ("09:00", "18:00"), ("10:00", "19:00")]

    engineers = []
    for i, (spec, level) in enumerate(plan, start=1):
        district = pick_district(rnd)
        hlat, hlon, haddr = random_point_in(rnd, district)

        skills = {spec: level}
        # у ~40% есть вторая, более слабая специализация — это даёт солверу
        # пространство для манёвра и делает объяснения интереснее
        if rnd.random() < 0.4:
            second = rnd.choice([s for s in SPECIALIZATIONS if s != spec])
            skills[second] = max(1, level - rnd.choice([1, 2]))

        vehicle = rnd.choice(["car", "car", "van"])

        onboard = set()
        for s in skills:
            onboard.update(onboard_by_spec[s])
        # у части инженеров редкий инструмент уже на руках — тоже повод для выбора
        if spec == "network" and level >= 4 and rnd.random() < 0.6:
            onboard.add("EQ-OTDR")
        if spec == "power" and level >= 4 and rnd.random() < 0.5:
            onboard.add("EQ-MEGGER")

        shift_start, shift_end = rnd.choices(shifts, weights=[5, 6, 2], k=1)[0]
        engineers.append({
            "id": f"ENG-{i:02d}",
            "name": full_name(rnd),
            "skills": skills,
            "shift_start": shift_start,
            "shift_end": shift_end,
            "break_from": "12:00",
            "break_to": "15:00",
            "break_min": 45,
            "vehicle_type": vehicle,
            "home_lat": hlat,
            "home_lon": hlon,
            "home_address": f"{district[0]}, {haddr}",
            "onboard_equipment": sorted(onboard),
            "max_overtime_min": 60,
        })

    # Пешие инженеры (пешком + общественный транспорт). Габаритное оборудование
    # им недоступно, и это видно в «почему не назначено» — ценный случай для
    # демонстрации. Но выбираем их только среди тех, все специализации которых
    # обходятся без габарита: монтажнику камер нужна стремянка, электрику —
    # тележка, климатчику — баллон с хладагентом. Пеший специалист в этих ролях
    # не взял бы ни одной профильной заявки, и нехватка людей в отчёте выглядела
    # бы как дефицит штата, хотя это ошибка комплектования.
    walkable = [e for e in engineers if set(e["skills"]) <= {"it", "network"}]
    for e in walkable[:max(1, len(engineers) // 6)]:
        e["vehicle_type"] = "walk_transit"

    return engineers


def gen_jobs(rnd: random.Random, n: int, day: Date) -> list[dict]:
    wt_by_id = {w[0]: w for w in WORK_TYPES}
    day_start = datetime.combine(day, datetime.min.time()).replace(hour=8)
    day_end = day_start.replace(hour=19)

    jobs = []
    for i in range(1, n + 1):
        district = pick_district(rnd)
        lat, lon, addr = random_point_in(rnd, district)
        wt = rnd.choices(
            WORK_TYPES,
            # аварийные и тяжёлые работы редки — иначе датасет нереалистичен
            weights=[5, 7, 12, 7, 3, 5, 14, 6, 8, 6, 20, 3],
            k=1,
        )[0]
        wt_id, _, spec, min_level, base_min, equipment = wt

        complexity = rnd.choices([1, 2, 3, 4, 5], weights=[15, 30, 30, 18, 7], k=1)[0]
        # «сложность работ» из названия кейса: растягивает номинальную длительность
        duration = int(round(base_min * (0.85 + 0.15 * complexity) / 5.0)) * 5

        # Временное окно: у 70% заявок оно есть, остальные — «в течение дня».
        # Окно обязано вмещать саму работу с запасом: жёсткое окно 08:00-10:00
        # под двухчасовой ремонт щита физически невыполнимо, и такая заявка
        # отвалилась бы не из-за маршрута, а из-за противоречия в данных.
        if rnd.random() < 0.70:
            min_width = max(2, math.ceil((duration + 30) / 60))
            width = rnd.choice([w for w in (2, 2, 3, 3, 4) if w >= min_width]
                               or [min_width])
            if width > 11:
                tw_start, tw_end, tw_hard = day_start, day_end, False
            else:
                start_h = rnd.randint(8, 19 - width)
                tw_start = day_start.replace(hour=start_h)
                tw_end = tw_start + timedelta(hours=width)
                # треть окон жёсткие: клиент физически доступен только в это время
                tw_hard = rnd.random() < 0.35
        else:
            tw_start, tw_end, tw_hard = day_start, day_end, False

        priority = rnd.choices(["P1", "P2", "P3", "P4"],
                               weights=[8, 15, 50, 27], k=1)[0]

        if priority == "P1":
            # Аварию не планируют накануне — она возникает по ходу дня. Часть
            # успевает до развода смены, остальные прилетают в течение дня и
            # запускают перепланирование.
            created = day_start + timedelta(minutes=rnd.randint(-60, 480))
            tw_start = max(tw_start, created)
            tw_end = day_end
            tw_hard = False
        elif rnd.random() < 0.20:
            created = day_start + timedelta(minutes=rnd.randint(30, 450))
        else:
            created = day_start - timedelta(hours=14)   # накануне вечером

        # SLA отсчитывается от начала рабочего дня, а не от момента заведения:
        # заявка, оформленная вчера в 18:00, не обязана быть закрыта к 02:00.
        sla_base = max(created, day_start)
        if priority == "P1":
            sla = min(sla_base + timedelta(hours=4), day_end)
        elif priority == "P2":
            sla = min(sla_base + timedelta(hours=8), day_end)
        else:
            sla = tw_end

        # Оборудование берётся строго из матрицы «работа → оборудование».
        # Случайных добавок здесь нет намеренно: матрица совместимости — это
        # заявленный артефакт кейса, и заявка, требующая инструмент вне неё,
        # противоречила бы справочнику (ТО кондиционера с обжимкой RJ-45).
        needs = list(equipment)

        jobs.append({
            "id": f"JOB-{i:04d}",
            "external_id": f"ЗН-{day.strftime('%y%m%d')}-{i:04d}",
            "customer": rnd.choice(CUSTOMERS),
            "district": district[0],
            "address": f"{district[0]}, {addr}",
            "lat": lat,
            "lon": lon,
            "work_type_id": wt_id,
            "specialization": spec,
            "min_level": min_level,
            "complexity": complexity,
            "duration_min": duration,
            "required_equipment": needs,
            "tw_start": tw_start.strftime("%H:%M"),
            "tw_end": tw_end.strftime("%H:%M"),
            "tw_hard": tw_hard,
            "priority": priority,
            "sla_deadline": sla.strftime("%H:%M"),
            "created_at": created.strftime("%Y-%m-%d %H:%M"),
            "known_at_day_start": created <= day_start,
            "status": "new",
            "contact_phone": f"+7 9{rnd.randint(10, 99)} {rnd.randint(100, 999)}-"
                             f"{rnd.randint(10, 99)}-{rnd.randint(10, 99)}",
        })
    return jobs


def gen_events(rnd: random.Random, jobs: list[dict], engineers: list[dict],
               day: Date) -> list[dict]:
    """События рабочего дня для симулятора перепланирования.

    Это сценарная часть датасета: именно она показывает жюри, что план не
    статичная картинка, а живой процесс.
    """
    events: list[dict] = []

    # 1. Заявки, поступившие после начала дня
    for j in jobs:
        if not j["known_at_day_start"]:
            events.append({
                "at": j["created_at"][-5:],
                "type": "job_created",
                "payload": j["id"],
                "comment": f"Новая заявка {j['priority']}: {j['customer']}",
            })

    # 2. Визиты, которые затянулись
    planned = [j for j in jobs if j["known_at_day_start"]]
    for j in rnd.sample(planned, k=min(4, len(planned))):
        events.append({
            "at": f"{rnd.randint(10, 15):02d}:{rnd.choice(['00', '15', '30', '45'])}",
            "type": "job_overrun",
            "payload": f"{j['id']}:+{rnd.choice([20, 30, 45])}",
            "comment": f"{j['id']}: работы затянулись",
        })

    # 3. Отмена клиентом
    for j in rnd.sample(planned, k=min(2, len(planned))):
        events.append({
            "at": f"{rnd.randint(9, 14):02d}:{rnd.choice(['00', '20', '40'])}",
            "type": "job_cancelled",
            "payload": j["id"],
            "comment": f"{j['customer']} отменил визит",
        })

    # 4. Инженер выбыл: болезнь и поломка авто — самые болезненные сценарии
    sick = rnd.choice(engineers)
    events.append({
        "at": "08:10",
        "type": "engineer_unavailable",
        "payload": sick["id"],
        "comment": f"{sick['name']} на больничном — заявки надо перераспределить",
    })
    with_car = [e for e in engineers
                if e["vehicle_type"] != "walk_transit" and e["id"] != sick["id"]]
    if with_car:
        broken = rnd.choice(with_car)
        events.append({
            "at": "13:20",
            "type": "vehicle_breakdown",
            "payload": broken["id"],
            "comment": f"{broken['name']}: сломался автомобиль, дальше пешком",
        })

    events.sort(key=lambda e: e["at"])
    return events


# --------------------------------------------------------------------------
# Запись
# --------------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    # utf-8-sig — чтобы Excel открывал кириллицу без плясок с бубном
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


def join(v) -> str:
    return "|".join(v) if isinstance(v, (list, tuple)) else v


# --------------------------------------------------------------------------
# Самопроверка
# --------------------------------------------------------------------------

def validate(engineers: list[dict], jobs: list[dict]) -> list[str]:
    """Проверить, что датасет вообще решаем, и вернуть список предупреждений.

    Считаем только ЖЁСТКИЕ ограничения, без времени и географии: квалификация и
    «габаритное оборудование требует авто». Если у заявки ноль кандидатов, её не
    назначит никакой солвер — и виноват будет датасет, а не алгоритм. Такое надо
    ловить здесь, а не на защите.
    """
    equip_bulky = {e[0]: e[2] for e in EQUIPMENT}
    warnings: list[str] = []
    candidates: list[int] = []
    orphans: list[str] = []

    for j in jobs:
        n_ok = 0
        needs_car = any(equip_bulky[q] for q in j["required_equipment"])
        for e in engineers:
            if e["skills"].get(j["specialization"], 0) < j["min_level"]:
                continue
            if needs_car and e["vehicle_type"] == "walk_transit":
                continue
            n_ok += 1
        candidates.append(n_ok)
        if n_ok == 0:
            orphans.append(f"{j['id']} ({j['work_type_id']}, {j['specialization']} "
                           f"ур.{j['min_level']}{', нужен авто' if needs_car else ''})")

    if orphans:
        warnings.append(f"заявок без единого исполнителя: {len(orphans)} → "
                        + ", ".join(orphans[:5])
                        + (" …" if len(orphans) > 5 else ""))

    # Спрос и ёмкость в разрезе «специализация / требуемый уровень»
    print("\n  спрос по квалификации (часов работ → сколько инженеров подходит):")
    demand: dict[tuple[str, int], float] = {}
    for j in jobs:
        key = (j["specialization"], j["min_level"])
        demand[key] = demand.get(key, 0) + j["duration_min"] / 60

    for (spec, lvl), hours in sorted(demand.items()):
        fit = [e for e in engineers if e["skills"].get(spec, 0) >= lvl]
        # ~5.8 ч чистой работы на инженера за смену: 8.25 ч минус дорога
        capacity = len(fit) * 5.8
        flag = "" if hours <= capacity else "  ← дефицит"
        print(f"    {spec:9s} ур.{lvl}   {hours:5.1f} ч  "
              f"/ {len(fit):2d} чел. ≈ {capacity:5.1f} ч{flag}")
        if hours > capacity:
            warnings.append(
                f"{spec} ур.{lvl}: {hours:.1f} ч работ на {len(fit)} чел. "
                f"(≈{capacity:.1f} ч) — часть заявок отвалится из-за нехватки "
                f"квалификации, а не из-за маршрутов")

    # Экземпляров не может быть меньше, чем инженеров, которые этот инструмент
    # уже возят с собой: иначе справочник противоречит сам себе.
    for eid, name, _bulky, units, _stock in EQUIPMENT:
        carried = sum(1 for e in engineers if eid in e["onboard_equipment"])
        if carried > units:
            warnings.append(f"{eid} ({name}): на руках у {carried} инженеров, "
                            f"а всего экземпляров {units}")

    lonely = sum(1 for c in candidates if c == 1)
    print(f"\n  кандидатов на заявку: мин {min(candidates)}, "
          f"среднее {sum(candidates) / len(candidates):.1f}, макс {max(candidates)}")
    if lonely:
        print(f"    из них с единственным возможным исполнителем: {lonely}")

    return warnings


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--jobs", type=int, default=70, help="сколько заявок (по умолч. 70)")
    p.add_argument("--engineers", type=int, default=18, help="сколько инженеров (по умолч. 18)")
    p.add_argument("--date", default="2026-08-12", help="дата плана, YYYY-MM-DD")
    p.add_argument("--seed", type=int, default=20260811, help="зерно ГПСЧ")
    p.add_argument("--out", default="seed", help="каталог вывода")
    args = p.parse_args()

    rnd = random.Random(args.seed)
    day = Date.fromisoformat(args.date)
    out = Path(__file__).parent / args.out
    out.mkdir(parents=True, exist_ok=True)

    engineers = gen_engineers(rnd, args.engineers)
    jobs = gen_jobs(rnd, args.jobs, day)
    events = gen_events(rnd, jobs, engineers, day)

    # --- справочники ---
    write_csv(out / "work_types.csv", [
        {"id": i, "name": n, "specialization": s, "min_level": lv,
         "base_duration_min": d, "equipment": join(eq)}
        for i, n, s, lv, d, eq in WORK_TYPES
    ], ["id", "name", "specialization", "min_level", "base_duration_min", "equipment"])

    write_csv(out / "equipment.csv", [
        {"id": i, "name": n, "bulky": int(b), "units": u, "stock": st}
        for i, n, b, u, st in EQUIPMENT
    ], ["id", "name", "bulky", "units", "stock"])

    write_csv(out / "warehouses.csv", [
        {"id": i, "name": n, "lat": la, "lon": lo, "address": a,
         "open_from": f, "open_to": t}
        for i, n, la, lo, a, f, t in WAREHOUSES
    ], ["id", "name", "lat", "lon", "address", "open_from", "open_to"])

    # --- инженеры ---
    write_csv(out / "engineers.csv", [
        {**e,
         "skills": ";".join(f"{k}:{v}" for k, v in e["skills"].items()),
         "onboard_equipment": join(e["onboard_equipment"])}
        for e in engineers
    ], ["id", "name", "skills", "shift_start", "shift_end", "break_from", "break_to",
        "break_min", "vehicle_type", "home_lat", "home_lon", "home_address",
        "onboard_equipment", "max_overtime_min"])

    # --- заявки ---
    write_csv(out / "jobs.csv", [
        {**j,
         "required_equipment": join(j["required_equipment"]),
         "tw_hard": int(j["tw_hard"]),
         "known_at_day_start": int(j["known_at_day_start"])}
        for j in jobs
    ], ["id", "external_id", "customer", "district", "address", "lat", "lon",
        "work_type_id", "specialization", "min_level", "complexity", "duration_min",
        "required_equipment", "tw_start", "tw_end", "tw_hard", "priority",
        "sla_deadline", "created_at", "known_at_day_start", "status", "contact_phone"])

    # --- события дня ---
    write_csv(out / "events.csv", events, ["at", "type", "payload", "comment"])

    # --- единый снимок для backend ---
    snapshot = {
        "meta": {
            "date": args.date,
            "seed": args.seed,
            "generated_by": "data/generate.py",
            "city": "Москва",
        },
        "specializations": SPECIALIZATIONS,
        "work_types": [
            {"id": i, "name": n, "specialization": s, "min_level": lv,
             "base_duration_min": d, "equipment": eq}
            for i, n, s, lv, d, eq in WORK_TYPES
        ],
        "equipment": [
            {"id": i, "name": n, "bulky": b, "units": u, "stock": st}
            for i, n, b, u, st in EQUIPMENT
        ],
        "warehouses": [
            {"id": i, "name": n, "lat": la, "lon": lo, "address": a,
             "open_from": f, "open_to": t}
            for i, n, la, lo, a, f, t in WAREHOUSES
        ],
        "engineers": engineers,
        "jobs": jobs,
        "events": events,
    }
    (out / "snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- сводка, чтобы сразу видеть, что датасет вменяемый ---
    # Главная проверка — баланс спроса и ёмкости. Если работ вдвое больше, чем
    # смен, солвер отбросит половину заявок и демо будет выглядеть провалом.
    # Целимся в лёгкий перегруз (105-115%): тогда несколько заявок честно
    # не влезает — и это как раз материал для экрана «почему не назначено».
    known = sum(1 for j in jobs if j["known_at_day_start"])
    work_h = sum(j["duration_min"] for j in jobs) / 60
    shift_h = sum(
        (int(e["shift_end"][:2]) - int(e["shift_start"][:2])) - e["break_min"] / 60
        for e in engineers)
    travel_share = 0.30                      # эмпирическая доля дороги в дне
    demand_h = work_h / (1 - travel_share)   # работы вместе с дорогой
    load = demand_h / shift_h * 100
    # Оценка грубая: она не видит временных окон и совпадения квалификаций,
    # поэтому фактическая доля назначенных заявок выходит ниже расчётной
    # загрузки примерно на 10-15 п.п. Ориентир 90-100 % подобран так, чтобы
    # солвер закрывал ~90 % заявок и несколько штук честно не влезало.

    centroid = (sum(j["lat"] for j in jobs) / len(jobs),
                sum(j["lon"] for j in jobs) / len(jobs))
    spread = sum(haversine_km((j["lat"], j["lon"]), centroid) for j in jobs) / len(jobs)
    no_car = sum(1 for e in engineers if e["vehicle_type"] == "walk_transit")

    print(f"Датасет записан в {out}")
    print(f"  инженеров ........... {len(engineers)} (без авто: {no_car})")
    print(f"  заявок .............. {len(jobs)} "
          f"(известны с утра: {known}, приходят днём: {len(jobs) - known})")
    print(f"  приоритет P1/P2 ..... "
          f"{sum(1 for j in jobs if j['priority'] == 'P1')}/"
          f"{sum(1 for j in jobs if j['priority'] == 'P2')}")
    print(f"  жёстких окон ........ {sum(1 for j in jobs if j['tw_hard'])}")
    print(f"  событий дня ......... {len(events)}")
    print(f"  трудоёмкость ........ {work_h:.0f} ч работ + ~{demand_h - work_h:.0f} ч "
          f"дороги на {shift_h:.0f} ч смен")
    print(f"  загрузка ............ {load:.0f} %  "
          f"({'ок' if 90 <= load <= 105 else 'подкрутить --jobs/--engineers'})")
    print(f"  на инженера ......... {len(jobs) / len(engineers):.1f} заявки/день")
    print(f"  средний радиус ...... {spread:.1f} км от центра масс заявок")

    warnings = validate(engineers, jobs)
    if warnings:
        print("\n  ⚠ предупреждения:")
        for w in warnings:
            print(f"    — {w}")
        print("\n  Подкрутите --engineers / --jobs / --seed либо состав команды "
              "в gen_engineers().")
    else:
        print("\n  ✓ датасет решаем: у каждой заявки есть хотя бы один исполнитель, "
              "дефицита квалификации нет")


if __name__ == "__main__":
    main()
