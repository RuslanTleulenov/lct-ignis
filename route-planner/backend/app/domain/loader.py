"""Чтение датасета из snapshot.json, который делает data/generate.py.

Формат намеренно простой, чтобы реальные данные заказчика можно было привести
к нему конвертером, не трогая ни солвер, ни API.
"""

from __future__ import annotations

import json
from datetime import date as Date, datetime
from pathlib import Path

from .models import (
    Dataset,
    DayEvent,
    Engineer,
    Equipment,
    Job,
    Priority,
    TransportMode,
    Warehouse,
    WorkType,
    hhmm_to_min,
    min_to_hhmm,
)


#: Старые снимки писали приоритет как P1–P4 и транспорт как van/walk_transit.
#: Формат ТЗ другой, но старые файлы должны читаться — переводим на лету.
LEGACY_PRIORITY = {"P1": "urgent", "P2": "urgent", "P3": "normal", "P4": "normal"}
LEGACY_TRANSPORT = {"van": "car", "walk_transit": "transit"}


def parse_priority(raw: str) -> Priority:
    return Priority(LEGACY_PRIORITY.get(raw, raw))


def parse_transport(raw: str | None) -> TransportMode | None:
    if raw in (None, ""):
        return None
    return TransportMode(LEGACY_TRANSPORT.get(raw, raw))


def _created_at_min(raw: str, plan_day: Date) -> int:
    """«2026-08-11 18:00» -> минуты от полуночи дня плана (может быть отрицательным).

    Заявки, заведённые накануне вечером, получают отрицательное значение — так
    их естественно отличать от тех, что прилетают в течение дня.
    """
    dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
    delta_days = (dt.date() - plan_day).days
    return delta_days * 24 * 60 + dt.hour * 60 + dt.minute


def engineer_to_raw(eng: Engineer) -> dict:
    """Обратное преобразование — для записи справочника в snapshot.json.

    Формат обязан совпадать с тем, что читает `load_dataset`, иначе
    отредактированный справочник перестанет загружаться.
    """
    return {
        "id": eng.id,
        "name": eng.name,
        "skills": dict(eng.skills),
        "shift_start": min_to_hhmm(eng.shift_start),
        "shift_end": min_to_hhmm(eng.shift_end),
        "break_from": min_to_hhmm(eng.break_from),
        "break_to": min_to_hhmm(eng.break_to),
        "break_min": eng.break_min,
        "vehicle_type": eng.vehicle_type.value,
        "home_lat": eng.home_lat,
        "home_lon": eng.home_lon,
        "home_address": eng.home_address,
        "onboard_equipment": sorted(eng.onboard_equipment),
        "max_overtime_min": eng.max_overtime_min,
    }


def save_engineers(path: str | Path, engineers: list[Engineer]) -> None:
    """Переписать в снимке только раздел инженеров, не трогая остальное.

    Читаем сырой JSON и подменяем одну ветку: так заявки, справочники и
    события гарантированно остаются нетронутыми.
    """
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["engineers"] = [engineer_to_raw(e) for e in engineers]
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)          # атомарная замена: снимок не окажется битым


def load_dataset(path: str | Path) -> Dataset:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    plan_day = Date.fromisoformat(raw["meta"]["date"])

    work_types = {
        w["id"]: WorkType(
            id=w["id"],
            name=w["name"],
            specialization=w["specialization"],
            min_level=w["min_level"],
            base_duration_min=w["base_duration_min"],
            equipment=tuple(w["equipment"]),
        )
        for w in raw["work_types"]
    }

    equipment = {
        e["id"]: Equipment(
            id=e["id"],
            name=e["name"],
            bulky=bool(e["bulky"]),
            units=e["units"],
            stock=e["stock"],
        )
        for e in raw["equipment"]
    }

    warehouses = {
        w["id"]: Warehouse(
            id=w["id"],
            name=w["name"],
            lat=w["lat"],
            lon=w["lon"],
            address=w["address"],
            open_from=hhmm_to_min(w["open_from"]),
            open_to=hhmm_to_min(w["open_to"]),
        )
        for w in raw["warehouses"]
    }

    engineers = [
        Engineer(
            id=e["id"],
            name=e["name"],
            skills=dict(e["skills"]),
            shift_start=hhmm_to_min(e["shift_start"]),
            shift_end=hhmm_to_min(e["shift_end"]),
            break_from=hhmm_to_min(e["break_from"]),
            break_to=hhmm_to_min(e["break_to"]),
            break_min=e["break_min"],
            vehicle_type=parse_transport(e["vehicle_type"]),
            home_lat=e["home_lat"],
            home_lon=e["home_lon"],
            home_address=e["home_address"],
            onboard_equipment=set(e["onboard_equipment"]),
            max_overtime_min=e["max_overtime_min"],
        )
        for e in raw["engineers"]
    ]

    jobs = [
        Job(
            id=j["id"],
            external_id=j["external_id"],
            customer=j["customer"],
            district=j["district"],
            address=j["address"],
            lat=j["lat"],
            lon=j["lon"],
            work_type_id=j["work_type_id"],
            specialization=j["specialization"],
            min_level=j["min_level"],
            complexity=j["complexity"],
            duration_min=j["duration_min"],
            required_equipment=tuple(j["required_equipment"]),
            tw_start=hhmm_to_min(j["tw_start"]),
            tw_end=hhmm_to_min(j["tw_end"]),
            tw_hard=bool(j["tw_hard"]),
            priority=parse_priority(j["priority"]),
            sla_deadline=hhmm_to_min(j["sla_deadline"]),
            created_at_min=_created_at_min(j["created_at"], plan_day),
            known_at_day_start=bool(j["known_at_day_start"]),
            status=j.get("status", "new"),
            contact_phone=j.get("contact_phone", ""),
            required_transport=parse_transport(j.get("required_transport")),
            geo_precision=j.get("geo_precision", "house"),
            control_engineer=j.get("control_engineer") or None,
            control_status=j.get("control_status", ""),
        )
        for j in raw["jobs"]
    ]

    events = [
        DayEvent(at=hhmm_to_min(e["at"]), type=e["type"],
                 payload=e["payload"], comment=e["comment"])
        for e in raw.get("events", [])
    ]

    return Dataset(
        date=raw["meta"]["date"],
        specializations=raw["specializations"],
        work_types=work_types,
        equipment=equipment,
        warehouses=warehouses,
        engineers=engineers,
        jobs=jobs,
        events=events,
        title=raw["meta"].get("title", ""),
        return_to_start=bool(raw["meta"].get("return_to_start", False)),
    )
