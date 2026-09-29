"""Импорт выгрузки «Билайн Бизнес» в формат сервиса.

    py -3.11 data/import_beeline.py            # все три территории
    py -3.11 data/import_beeline.py --online   # догеокодировать, чего нет в кеше

Что дал заказчик: по три территории (Восток, Юго-восток, Югоцентр) на один
день, 17.08.2026. Два файла на территорию:

* «заявки» — вход: номер, тип заявки (BK и HD), окно визита, район, адрес;
* «контрольное распределение» — как диспетчер реально раскидал те же заявки
  по бригадам и чем они закончились (выполнена, просрочена, отменена…).

Строки двух файлов идут в одном порядке — проверено по адресу и окну на всех
205 заявках, — поэтому к каждой заявке привязывается и её фактический
исполнитель. Это и есть базовый вариант «как было», с которым сравнивается
план.

Чего в выгрузке НЕТ и что принято вместо этого (все допущения в ASSUMPTIONS,
они же уходят в snapshot.json и показываются в интерфейсе):

* координат — адреса геокодируются через Nominatim (см. geocode.py), точность
  хранится в заявке;
* длительности работ — норматив по типу заявки HD, таблица DURATION_MIN;
* требуемого навыка — выводится из типа BK: подключения и дозаказы, локальные
  работы, аварийные работы — ровно справочник ТЗ;
* приоритета — «Глобальная проблема» считается срочной, остальные обычными;
* файла инженеров — бригады берутся из контрольного распределения; навыки
  бригады — по типам заявок, которые она в этот день выполняла;
* транспорта бригад — принят автомобиль (бригады покрывают районы целиком
  и возят оборудование); тип можно поменять в справочнике сервиса;
* смены — 09:30–22:00: окна визитов идут с 10:00 до 22:00;
* стартовой точки — офис территории, его адрес есть в выгрузке последней строкой;
* событий дня — отмены взяты из контрольного файла (статус «Отменена»), а
  срочная заявка и больничный добавлены как учебный сценарий и помечены.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    # Вывод использует символы вне кодовой страницы консоли (→): на cp1251
    # падает с UnicodeEncodeError. UTF-8 работает при любой локали.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
from geocode import Geocoder  # noqa: E402

RAW_DIR = Path(__file__).parent / "beeline" / "raw"
OUT_DIR = Path(__file__).parent / "beeline"
DAY = "2026-08-17"

TERRITORIES = OrderedDict([
    ("vostok", "Восток"),
    ("yugo-vostok", "Юго-восток"),
    ("yugocentr", "Югоцентр"),
])

#: Справочник навыков из ТЗ, п. 2.4.1.
SPECIALIZATIONS = OrderedDict([
    ("local", "Локальные работы"),
    ("connect", "Работы на подключение и дозаказы"),
    ("emergency", "Аварийные работы"),
])

#: Тип заявки BK → навык.
SKILL_BY_BK = {
    "Подключение": "connect",
    "Дозаказ": "connect",
    "Локальная заявка": "local",
    "Глобальная проблема": "emergency",
}

#: Норматив работ по типу заявки HD, минуты. В выгрузке длительности нет;
#: значения — разумная оценка для домашнего интернета и ТВ: подключение с
#: настройкой роутера дольше замены приставки, авария дольше диагностики.
DURATION_MIN = {
    "Конвергенция абонента": 60,
    "Заявка на подключение": 90,
    "Заказ подключения/Дозаказ оборудования": 60,
    "Дозаказ оборудования": 30,
    "Нет линка": 45,
    "Низкая скорость": 45,
    "Разрывы": 60,
    "Рост ошибок на порту": 45,
    "IP-адрес 169...": 30,
    "Переключение на Гбит/с": 45,
    "Работа с кабелем": 60,
    "Роутер. Замена техническим специалистом": 30,
    "Мониторинг": 30,
    "TVE/ENT. Другие ошибки": 40,
    "TVE/ENT. Замена приставки техником": 30,
    "ТВ. Замена приставки техником": 30,
    "Авария": 90,
    "Информация": 30,
}
DEFAULT_DURATION = 45

SHIFT = ("09:30", "22:00")
BREAK = ("13:00", "16:00", 45)
DAY_START, DAY_END = "09:30", "22:00"

ASSUMPTIONS = [
    "Координаты получены геокодированием адресов (Nominatim/OSM); точность указана у каждой заявки.",
    "Длительность работ — норматив по типу заявки HD (в выгрузке её нет).",
    "Требуемый навык выведен из типа заявки BK: подключение и дозаказ → «Работы на подключение и дозаказы», "
    "локальная заявка → «Локальные работы», глобальная проблема → «Аварийные работы».",
    "Приоритет: «Глобальная проблема» — срочная, остальные — обычные.",
    "Бригады взяты из контрольного распределения; навыки бригады — по типам заявок, которые она выполняла в этот день.",
    "Транспорт бригад в выгрузке не указан — принят автомобиль; меняется в справочнике.",
    "Смена бригад 09:30–22:00 с обедом 45 минут в окне 13:00–16:00; окна визитов заказчика идут с 10:00 до 22:00.",
    "Стартовая точка бригад — офис территории (адрес из выгрузки); возврат в офис после последней заявки не планируется (ТЗ, п. 2.4).",
    "Окно «0:01–23:59» означает «в любое время рабочего дня» и считается мягким.",
    "События дня: отмены — из контрольного файла; срочная заявка и больничный добавлены как учебный сценарий.",
]


# ------------------------------------------------------------------ чтение

def read_csv(path: Path) -> list[dict]:
    text = path.read_text(encoding="cp1251")
    return list(csv.DictReader(text.splitlines(), delimiter=";"))


def parse_dt(raw: str) -> str:
    """«17.08.2026 18:00» → «18:00»."""
    return datetime.strptime(raw.strip(), "%d.%m.%Y %H:%M").strftime("%H:%M")


def load_territory(title: str) -> tuple[list[dict], list[dict], str]:
    """Заявки, контрольное распределение и адрес офиса."""
    inputs = read_csv(RAW_DIR / f"{title} — заявки.csv")
    control = read_csv(RAW_DIR / f"{title} — контрольное распределение.csv")
    office = next((r["Тип заявки BK"] for r in inputs
                   if r["Заявка"].strip().lower() == "адрес офиса"), "")
    jobs = [r for r in inputs if r["Начало"]]
    if len(jobs) != len(control):
        raise SystemExit(f"{title}: {len(jobs)} заявок против {len(control)} строк контроля")
    for a, b in zip(jobs, control):
        if a["Начало"] != b["Начало"] or not b["Адрес"].startswith(a["Адрес"]):
            raise SystemExit(f"{title}: порядок строк разошёлся на {a['Заявка']}")
    return jobs, control, office


# ------------------------------------------------------------------ сборка

def build(key: str, title: str, gc: Geocoder) -> dict:
    rows, control, office_addr = load_territory(title)
    office = gc.lookup(office_addr)
    if office.precision == "none":
        raise SystemExit(f"{title}: не удалось геокодировать офис «{office_addr}»")

    # --- типы работ: по типу HD, навык по типу BK
    work_types: OrderedDict[str, dict] = OrderedDict()
    wt_id_by_name: dict[str, str] = {}
    for r in rows:
        hd, bk = r["Тип заявки HD"].strip(), r["Тип заявки BK"].strip()
        if hd not in wt_id_by_name:
            wt_id_by_name[hd] = f"WT-{len(wt_id_by_name) + 1:02d}"
        wt_id = wt_id_by_name[hd]
        if wt_id not in work_types:
            work_types[wt_id] = {
                "id": wt_id, "name": hd,
                "specialization": SKILL_BY_BK.get(bk, "local"),
                "min_level": 1,
                "base_duration_min": DURATION_MIN.get(hd, DEFAULT_DURATION),
                "equipment": [],
            }

    # --- бригады из контрольного распределения
    brigades: OrderedDict[str, Counter] = OrderedDict()
    for r in control:
        name = r["Бригада"].strip()
        if name:
            brigades.setdefault(name, Counter())[SKILL_BY_BK.get(r["Тип заявки BK"].strip(), "local")] += 1
    engineers = []
    eng_id_by_name: dict[str, str] = {}
    for i, (name, done) in enumerate(brigades.items(), start=1):
        eid = f"ENG-{i:02d}"
        eng_id_by_name[name] = eid
        engineers.append({
            "id": eid, "name": name,
            "skills": {spec: 1 for spec in SPECIALIZATIONS if done[spec]},
            "shift_start": SHIFT[0], "shift_end": SHIFT[1],
            "break_from": BREAK[0], "break_to": BREAK[1], "break_min": BREAK[2],
            "vehicle_type": "car",
            "home_lat": office.lat, "home_lon": office.lon,
            "home_address": f"Офис «{title}» — {office_addr}",
            "onboard_equipment": [],
            "max_overtime_min": 30,
            "control_jobs": sum(done.values()),
        })

    # --- заявки
    jobs = []
    precision = Counter()
    for n, (r, c) in enumerate(zip(rows, control), start=1):
        hd, bk = r["Тип заявки HD"].strip(), r["Тип заявки BK"].strip()
        wt = work_types[wt_id_by_name[hd]]
        geo = gc.lookup(r["Адрес"], r["Район"])
        precision[geo.precision] += 1
        tw_start, tw_end = parse_dt(r["Начало"]), parse_dt(r["Окончание"])
        any_time = tw_start <= "00:05" and tw_end >= "23:50"
        if any_time:
            tw_start, tw_end = DAY_START, DAY_END
        urgent = bk == "Глобальная проблема"
        if urgent:
            h, m = map(int, tw_start.split(":"))
            sla = min(h * 60 + m + 4 * 60, 22 * 60)
            sla = f"{sla // 60:02d}:{sla % 60:02d}"
        else:
            sla = tw_end
        jobs.append({
            "id": f"JOB-{n:04d}",
            "external_id": r["Заявка"].strip(),
            "customer": f"Абонент №{r['Заявка'].strip()}",
            "district": r["Район"].strip(),
            "address": r["Адрес"].strip(),
            "lat": geo.lat, "lon": geo.lon,
            "geo_precision": geo.precision,
            "work_type_id": wt["id"],
            "specialization": wt["specialization"],
            "min_level": 1,
            "complexity": 3,
            "duration_min": wt["base_duration_min"],
            "required_equipment": [],
            "tw_start": tw_start, "tw_end": tw_end,
            "tw_hard": not any_time,
            "priority": "urgent" if urgent else "normal",
            "sla_deadline": sla,
            "created_at": f"{DAY[:8]}16 18:00",       # накануне вечером
            "known_at_day_start": True,
            "contact_phone": "",
            "bk_type": bk,
            "connection": r.get("Подключение", "").strip(),
            "gigabit": r.get("Гигабитное подключение", "").strip() == "Да",
            # что было на самом деле — для сравнения с планом
            "control_engineer": eng_id_by_name.get(c["Бригада"].strip()),
            "control_status": c["Статус BK"].strip(),
        })

    # --- события дня: отмены из контроля + учебные срочная заявка и больничный
    events = []
    for j in jobs:
        if j["control_status"] == "Отменена":
            h, m = map(int, j["tw_start"].split(":"))
            at = max(h * 60 + m - 60, 9 * 60 + 40)
            events.append({
                "at": f"{at // 60:02d}:{at % 60:02d}", "type": "job_cancelled",
                "payload": j["id"],
                "comment": f"{j['customer']} отменил визит (статус в выгрузке: «Отменена»)",
            })
    busiest = max(engineers, key=lambda e: e["control_jobs"])
    events.append({
        "at": "09:40", "type": "engineer_unavailable", "payload": busiest["id"],
        "comment": f"{busiest['name']}: невыход на смену — заявки надо перераспределить "
                   f"(учебное событие)",
    })

    # Учебная срочная заявка: авария по адресу одного из отменённых визитов —
    # адрес настоящий, заявка выдуманная, и в комментарии это сказано.
    cancelled = [j for j in jobs if j["control_status"] == "Отменена"]
    if cancelled:
        src = cancelled[len(cancelled) // 2]
        emergency = next((wt for wt in work_types.values()
                          if wt["specialization"] == "emergency"), None)
        if emergency is None:
            emergency = {"id": f"WT-{len(work_types) + 1:02d}", "name": "Авария",
                         "specialization": "emergency", "min_level": 1,
                         "base_duration_min": DURATION_MIN["Авария"], "equipment": []}
            work_types[emergency["id"]] = emergency
        urgent = {
            **src,
            "id": f"JOB-{len(jobs) + 1:04d}", "external_id": "U-1",
            "customer": "Абонент №U-1 (учебная заявка)",
            "work_type_id": emergency["id"], "specialization": "emergency",
            "duration_min": emergency["base_duration_min"],
            "tw_start": "11:40", "tw_end": "15:40", "tw_hard": False,
            "priority": "urgent", "sla_deadline": "15:40",
            "created_at": f"{DAY} 11:40", "known_at_day_start": False,
            "control_engineer": None, "control_status": "",
        }
        jobs.append(urgent)
        events.append({
            "at": "11:40", "type": "job_created", "payload": urgent["id"],
            "comment": f"Срочная заявка: авария, {urgent['district']} — "
                       f"перепланировать день (учебное событие)",
        })
    events.sort(key=lambda e: e["at"])

    return {
        "meta": {
            "date": DAY,
            "title": f"Билайн Бизнес — {title}, 17.08.2026",
            "source": f"data/beeline/raw/{title} — заявки.csv",
            "generated_by": "data/import_beeline.py",
            "city": "Москва",
            "return_to_start": False,
            "assumptions": ASSUMPTIONS,
            "geocode_precision": dict(precision),
        },
        "specializations": dict(SPECIALIZATIONS),
        "work_types": list(work_types.values()),
        "equipment": [],
        "warehouses": [],
        "engineers": engineers,
        "jobs": jobs,
        "events": events,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--online", action="store_true",
                   help="догеокодировать адреса, которых нет в кеше")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    gc = Geocoder(online=args.online)
    for key, title in TERRITORIES.items():
        snap = build(key, title, gc)
        out = OUT_DIR / key / "snapshot.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        st = Counter(j["control_status"] for j in snap["jobs"])
        print(f"{title:12s} заявок {len(snap['jobs']):3d}, бригад {len(snap['engineers']):2d}, "
              f"типов работ {len(snap['work_types']):2d}, событий {len(snap['events'])}, "
              f"точность {snap['meta']['geocode_precision']}")
        print(f"{'':12s} в контроле: {dict(st)}")
        print(f"{'':12s} → {out}")
    gc.save()


if __name__ == "__main__":
    main()
