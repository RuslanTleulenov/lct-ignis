"""Загрузка тестовых данных из CSV или JSON — требование ТЗ, п. 2.1 (1).

Принимаются два вида файлов:

* **готовый снимок** `snapshot.json` в формате сервиса (его пишут
  `data/generate.py` и `data/import_beeline.py`);
* **простые таблицы** по минимальным полям ТЗ, п. 2.4 — заявки, инженеры и,
  по желанию, события. CSV с разделителем `;` или `,`, либо JSON со списками
  `jobs`, `engineers`, `events`.

Поля таблиц (лишние колонки игнорируются, регистр и пробелы в заголовках не
важны):

    заявка:   id, address | lat+lon, duration_min, tw_start, tw_end,
              priority (обычная|срочная), skill, transport (необязательно),
              customer, district, hard (да|нет)
    инженер:  id, name, address | lat+lon, shift_start, shift_end,
              skills (через | или ;), transport
    событие:  type (urgent|cancel|unavailable), time, id, comment
              + для срочной заявки — поля заявки

Навыки и транспорт пишутся как в справочнике ТЗ («Локальные работы»,
«Автомобиль») либо кодами (`local`, `car`). Адреса без координат геокодируются
через кеш (и через сеть, если она есть); что не нашлось — возвращается списком
ошибок, а не молча ставится в центр города.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import OrderedDict
from typing import Callable

from .models import TRANSPORT_LABEL, TransportMode

SKILLS = OrderedDict([
    ("local", "Локальные работы"),
    ("connect", "Работы на подключение и дозаказы"),
    ("emergency", "Аварийные работы"),
])
SKILL_ALIASES = {
    "local": "local", "локальные работы": "local", "локальные": "local",
    "connect": "connect", "работы на подключение и дозаказы": "connect",
    "подключение": "connect", "подключения и дозаказы": "connect",
    "emergency": "emergency", "аварийные работы": "emergency", "аварийные": "emergency",
}
TRANSPORT_ALIASES = {m.value: m for m in TransportMode}
TRANSPORT_ALIASES.update({label.lower(): m for m, label in TRANSPORT_LABEL.items()})
TRANSPORT_ALIASES.update({"авто": TransportMode.CAR, "машина": TransportMode.CAR,
                          "пешком": TransportMode.FOOT, "велосипед": TransportMode.BIKE,
                          "общественный": TransportMode.TRANSIT, "от": TransportMode.TRANSIT})
PRIORITY_ALIASES = {"normal": "normal", "обычная": "normal", "обычный": "normal", "": "normal",
                    "urgent": "urgent", "срочная": "urgent", "срочный": "urgent"}
#: Типы событий → как их понимает симулятор дня (solver/replan.py).
EVENT_ALIASES = {"urgent": "job_created", "new_job": "job_created",
                 "job_created": "job_created", "срочная заявка": "job_created",
                 "cancel": "job_cancelled", "job_cancelled": "job_cancelled",
                 "отмена": "job_cancelled", "отмена заявки": "job_cancelled",
                 "unavailable": "engineer_unavailable", "sick": "engineer_unavailable",
                 "engineer_unavailable": "engineer_unavailable",
                 "инженер недоступен": "engineer_unavailable",
                 "недоступность инженера": "engineer_unavailable"}

#: Синонимы заголовков: колонки называют как угодно, а читать надо одинаково.
COLUMNS = {
    "id": ("id", "заявка", "номер", "engineer_id", "инженер"),
    "name": ("name", "имя", "фио", "бригада"),
    "address": ("address", "адрес"),
    "lat": ("lat", "latitude", "широта"),
    "lon": ("lon", "lng", "longitude", "долгота"),
    "duration_min": ("duration_min", "duration", "длительность", "длительность_мин"),
    "tw_start": ("tw_start", "window_start", "начало", "окно_с", "с"),
    "tw_end": ("tw_end", "window_end", "окончание", "конец", "окно_по", "по"),
    "priority": ("priority", "приоритет"),
    "skill": ("skill", "навык", "required_skill"),
    "skills": ("skills", "навыки"),
    "transport": ("transport", "vehicle", "vehicle_type", "транспорт", "required_transport"),
    "customer": ("customer", "заказчик", "клиент", "абонент"),
    "district": ("district", "район"),
    "hard": ("hard", "tw_hard", "жёсткое", "жесткое"),
    "shift_start": ("shift_start", "смена_с", "начало_смены"),
    "shift_end": ("shift_end", "смена_по", "конец_смены"),
    "type": ("type", "тип", "event", "событие"),
    "time": ("time", "at", "время"),
    "comment": ("comment", "комментарий"),
}


class ImportError_(ValueError):
    """Ошибка с человеческим списком причин — уходит в интерфейс как есть."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# ------------------------------------------------------------------ чтение

def _norm_key(key: str) -> str:
    return re.sub(r"\s+", "_", key.strip().lower().lstrip("﻿"))


def read_table(text: str) -> list[dict]:
    """CSV с любым из привычных разделителей → строки с нормализованными ключами."""
    sample = text[:4096]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    rows = []
    for raw in csv.DictReader(io.StringIO(text), delimiter=delimiter):
        row = {_norm_key(k): (v or "").strip() for k, v in raw.items() if k}
        if any(row.values()):
            rows.append(row)
    return rows


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def field(row: dict, name: str, default: str = "") -> str:
    for alias in COLUMNS.get(name, (name,)):
        if alias in row and row[alias] != "":
            return row[alias]
    return default


# ------------------------------------------------------------------ разбор значений

def parse_hhmm(raw: str, what: str, problems: list[str]) -> str:
    raw = raw.strip()
    m = re.match(r"^(\d{1,2})[:.](\d{2})$", raw)
    if not m:
        # «17.08.2026 18:00» — берём время
        m2 = re.search(r"(\d{1,2}):(\d{2})\s*$", raw)
        if not m2:
            problems.append(f"{what}: время «{raw}» не в формате ЧЧ:ММ")
            return "00:00"
        m = m2
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_skill(raw: str, what: str, problems: list[str]) -> str:
    key = SKILL_ALIASES.get(raw.strip().lower())
    if key is None:
        problems.append(f"{what}: навык «{raw}» не из справочника "
                        f"({', '.join(SKILLS.values())})")
        return "local"
    return key


def parse_transport(raw: str, what: str, problems: list[str],
                    required: bool) -> TransportMode | None:
    raw = raw.strip().lower()
    if not raw:
        if required:
            problems.append(f"{what}: не указан тип транспорта")
            return TransportMode.CAR
        return None
    mode = TRANSPORT_ALIASES.get(raw)
    if mode is None:
        problems.append(f"{what}: транспорт «{raw}» не из справочника "
                        f"({', '.join(TRANSPORT_LABEL.values())})")
        return TransportMode.CAR if required else None
    return mode


def parse_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "да", "yes", "true", "hard", "жёсткое", "жесткое")


# ------------------------------------------------------------------ сборка снимка

Geocode = Callable[[str, str], tuple[float, float, str]]


def _coords(row: dict, what: str, problems: list[str],
            geocode: Geocode | None) -> tuple[float, float, str]:
    lat, lon = field(row, "lat"), field(row, "lon")
    if lat and lon:
        try:
            return float(lat.replace(",", ".")), float(lon.replace(",", ".")), "house"
        except ValueError:
            problems.append(f"{what}: координаты «{lat}, {lon}» не числа")
            return 0.0, 0.0, "none"
    address = field(row, "address")
    if not address:
        problems.append(f"{what}: нет ни координат, ни адреса")
        return 0.0, 0.0, "none"
    if geocode is None:
        problems.append(f"{what}: адрес «{address}» нечем геокодировать — укажите координаты")
        return 0.0, 0.0, "none"
    lat_f, lon_f, precision = geocode(address, field(row, "district"))
    if precision == "none":
        problems.append(f"{what}: адрес «{address}» не найден на карте — укажите координаты")
    return lat_f, lon_f, precision


def _job_from_row(row: dict, n: int, date: str, problems: list[str],
                  geocode: Geocode | None) -> dict:
    what = f"заявка {field(row, 'id') or n}"
    lat, lon, precision = _coords(row, what, problems, geocode)
    skill = parse_skill(field(row, "skill"), what, problems)
    try:
        duration = int(float(field(row, "duration_min", "45").replace(",", ".")))
    except ValueError:
        problems.append(f"{what}: длительность «{field(row, 'duration_min')}» не число")
        duration = 45
    tw_start = parse_hhmm(field(row, "tw_start", "09:00"), what, problems)
    tw_end = parse_hhmm(field(row, "tw_end", "18:00"), what, problems)
    if tw_end <= tw_start:
        problems.append(f"{what}: конец окна {tw_end} не позже начала {tw_start}")
    priority = PRIORITY_ALIASES.get(field(row, "priority").strip().lower())
    if priority is None:
        problems.append(f"{what}: приоритет «{field(row, 'priority')}» — нужен «обычная» или «срочная»")
        priority = "normal"
    transport = parse_transport(field(row, "transport"), what, problems, required=False)
    return {
        "id": field(row, "id") or f"JOB-{n:04d}",
        "external_id": field(row, "id") or f"JOB-{n:04d}",
        "customer": field(row, "customer") or f"Заявка {field(row, 'id') or n}",
        "district": field(row, "district") or "—",
        "address": field(row, "address") or f"{lat:.5f}, {lon:.5f}",
        "lat": lat, "lon": lon, "geo_precision": precision,
        "work_type_id": f"WT-{skill}", "specialization": skill, "min_level": 1,
        "complexity": 3, "duration_min": duration, "required_equipment": [],
        "tw_start": tw_start, "tw_end": tw_end,
        "tw_hard": parse_bool(field(row, "hard", "да")),
        "priority": priority, "sla_deadline": tw_end,
        "created_at": f"{date} 00:00", "known_at_day_start": True,
        "contact_phone": "",
        "required_transport": transport.value if transport else None,
    }


def snapshot_from_tables(jobs_rows: list[dict], engineers_rows: list[dict],
                         events_rows: list[dict] | None = None,
                         date: str = "2026-08-17", title: str = "Загруженный набор",
                         geocode: Geocode | None = None) -> dict:
    """Собрать snapshot.json из таблиц по минимальным полям ТЗ."""
    problems: list[str] = []
    if not jobs_rows:
        problems.append("в файле заявок нет ни одной строки")
    if not engineers_rows:
        problems.append("в файле инженеров нет ни одной строки")
    if problems:
        raise ImportError_(problems)

    # --- типы работ: один на навык, длительность в заявке своя
    work_types = [{"id": f"WT-{k}", "name": name, "specialization": k, "min_level": 1,
                   "base_duration_min": 45, "equipment": []} for k, name in SKILLS.items()]

    engineers = []
    for n, row in enumerate(engineers_rows, start=1):
        what = f"инженер {field(row, 'id') or field(row, 'name') or n}"
        lat, lon, _ = _coords(row, what, problems, geocode)
        skills_raw = re.split(r"[|;,/]+", field(row, "skills") or field(row, "skill"))
        skills = {parse_skill(s, what, problems): 1 for s in skills_raw if s.strip()}
        if not skills:
            problems.append(f"{what}: не указан ни один навык")
        if len(skills) > 3:
            problems.append(f"{what}: у инженера не больше трёх навыков (ТЗ, п. 2.4)")
        transport = parse_transport(field(row, "transport"), what, problems, required=True)
        shift_start = parse_hhmm(field(row, "shift_start", "09:00"), what, problems)
        shift_end = parse_hhmm(field(row, "shift_end", "18:00"), what, problems)
        engineers.append({
            "id": field(row, "id") or f"ENG-{n:02d}",
            "name": field(row, "name") or field(row, "id") or f"Инженер {n}",
            "skills": skills, "shift_start": shift_start, "shift_end": shift_end,
            "break_from": shift_start, "break_to": shift_end, "break_min": 0,
            "vehicle_type": transport.value,
            "home_lat": lat, "home_lon": lon,
            "home_address": field(row, "address") or "стартовая точка",
            "onboard_equipment": [], "max_overtime_min": 0,
        })

    jobs = [_job_from_row(row, n, date, problems, geocode)
            for n, row in enumerate(jobs_rows, start=1)]

    events = []
    for n, row in enumerate(events_rows or [], start=1):
        what = f"событие {n}"
        kind = EVENT_ALIASES.get(field(row, "type").strip().lower())
        if kind is None:
            problems.append(f"{what}: тип «{field(row, 'type')}» — нужен срочная заявка, "
                            f"отмена заявки или инженер недоступен")
            continue
        at = parse_hhmm(field(row, "time", "12:00"), what, problems)
        if kind == "job_created":
            # срочная заявка приходит по ходу дня: в строке полный набор полей
            job = _job_from_row(row, n, date, problems, geocode)
            job["id"] = field(row, "id") or f"JOB-U{n:02d}"
            job["external_id"] = job["id"]
            job["priority"] = "urgent"
            job["created_at"] = f"{date} {at}"
            job["known_at_day_start"] = False
            jobs.append(job)
            events.append({"at": at, "type": "job_created", "payload": job["id"],
                           "comment": field(row, "comment") or f"Срочная заявка {job['id']}"})
        else:
            events.append({"at": at, "type": kind, "payload": field(row, "id"),
                           "comment": field(row, "comment")
                           or ("Заявка отменена" if kind == "job_cancelled"
                               else "Инженер недоступен")})

    ids = [j["id"] for j in jobs]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        problems.append(f"повторяющиеся номера заявок: {', '.join(sorted(dup))}")
    known_eng = {e["id"] for e in engineers}
    for ev in events:
        if ev["type"] == "engineer_unavailable" and ev["payload"] not in known_eng:
            problems.append(f"событие «инженер недоступен»: нет инженера {ev['payload']}")
        if ev["type"] == "job_cancelled" and ev["payload"] not in ids:
            problems.append(f"событие «отмена»: нет заявки {ev['payload']}")
    if problems:
        raise ImportError_(problems)

    return {
        "meta": {"date": date, "title": title, "generated_by": "загрузка CSV/JSON",
                 "return_to_start": False, "assumptions": [
                     "Возврат в стартовую точку после последней заявки не планируется (ТЗ, п. 2.4).",
                     "Обеденный перерыв в загруженных данных не задан и не планируется.",
                 ]},
        "specializations": dict(SKILLS),
        "work_types": work_types,
        "equipment": [], "warehouses": [],
        "engineers": engineers, "jobs": jobs, "events": events,
    }


def snapshot_from_json(data: dict, geocode: Geocode | None = None) -> dict:
    """JSON: либо готовый снимок сервиса, либо {jobs, engineers, events}."""
    if "meta" in data and "work_types" in data:
        return data
    rows = lambda key: [{_norm_key(k): str(v) if v is not None else "" for k, v in r.items()}
                        for r in data.get(key, [])]
    return snapshot_from_tables(rows("jobs"), rows("engineers"), rows("events"),
                                date=str(data.get("date", "2026-08-17")),
                                title=str(data.get("title", "Загруженный набор")),
                                geocode=geocode)
