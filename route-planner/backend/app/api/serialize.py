"""Превращение доменных объектов в JSON для фронтенда.

Одно решение стоит объяснить: в остановку маршрута кладутся и данные самой
заявки — приоритет, адрес, тип работ, окно. Формально это дублирование, но
карта и Гант иначе вынуждены джойнить два списка на каждый ререндер, а
единственный потребитель у этого API — наш же интерфейс.

Всё время наружу уходит строками "ЧЧ:ММ": внутри системы минуты от полуночи,
на границе — человекочитаемый формат.
"""

from __future__ import annotations

from ..domain.models import Dataset, Job, min_to_hhmm
from ..solver.engine import Plan, Route, Stop
from ..solver.replan import PlanDiff


def job_out(ds: Dataset, job: Job, status: str = "") -> dict:
    return {
        "id": job.id,
        "external_id": job.external_id,
        "customer": job.customer,
        "address": job.address,
        "district": job.district,
        "lat": job.lat,
        "lon": job.lon,
        "work_type": ds.work_types[job.work_type_id].name,
        "work_type_id": job.work_type_id,
        "specialization": job.specialization,
        "specialization_name": ds.specializations.get(job.specialization,
                                                      job.specialization),
        "min_level": job.min_level,
        "complexity": job.complexity,
        "duration_min": job.duration_min,
        "required_equipment": [
            {"id": q, "name": ds.equipment[q].name, "bulky": ds.equipment[q].bulky}
            for q in job.required_equipment
        ],
        "window": [min_to_hhmm(job.tw_start), min_to_hhmm(job.tw_end)],
        "window_hard": job.tw_hard,
        "priority": job.priority.value,
        "sla_deadline": min_to_hhmm(job.sla_deadline),
        "known_at_day_start": job.known_at_day_start,
        "contact_phone": job.contact_phone,
        "status": status,
    }


def stop_out(ds: Dataset, stop: Stop) -> dict:
    out = {
        "kind": stop.kind,
        "job_id": stop.job_id,
        "label": stop.label,
        "lat": stop.lat,
        "lon": stop.lon,
        "arrival": min_to_hhmm(stop.arrival),
        "service_start": min_to_hhmm(stop.service_start),
        "service_end": min_to_hhmm(stop.service_end),
        "wait_min": stop.wait_min,
        "travel_min": stop.travel_min_from_prev,
        "travel_km": stop.travel_km_from_prev,
        "sla_late_min": stop.sla_late_min,
        "geometry": stop.geometry,
        "via_metro": stop.via_metro,
    }
    if stop.job_id:
        job = ds.job(stop.job_id)
        out |= {
            "priority": job.priority.value,
            "customer": job.customer,
            "address": job.address,
            "district": job.district,
            "work_type": ds.work_types[job.work_type_id].name,
            "complexity": job.complexity,
            "window": [min_to_hhmm(job.tw_start), min_to_hhmm(job.tw_end)],
            "window_hard": job.tw_hard,
            "sla_deadline": min_to_hhmm(job.sla_deadline),
        }
    return out


def route_out(ds: Dataset, route: Route) -> dict:
    return {
        "engineer_id": route.engineer_id,
        "engineer_name": route.engineer_name,
        "vehicle_type": route.vehicle_type,
        "pickup_warehouse": route.pickup_warehouse,
        "start": min_to_hhmm(route.start_min),
        "end": min_to_hhmm(route.end_min),
        "job_count": route.job_count,
        "travel_min": route.travel_min,
        "travel_km": route.travel_km,
        "work_min": route.work_min,
        "wait_min": route.wait_min,
        "lunch_start": (min_to_hhmm(route.lunch_start)
                        if route.lunch_start is not None else None),
        "lunch_min": route.lunch_min,
        "stops": [stop_out(ds, s) for s in route.stops],
    }


def plan_out(ds: Dataset, plan: Plan, plan_id: str = "",
             now: int | None = None, completed: dict[str, str] | None = None,
             diff: PlanDiff | None = None) -> dict:
    out = {
        "id": plan_id,
        "date": plan.date,
        "status": plan.status,
        "solve_ms": plan.solve_ms,
        "warm_started": plan.warm_started,
        "now": min_to_hhmm(now) if now is not None else None,
        "kpi": plan.kpi,
        "routes": [route_out(ds, r) for r in plan.routes],
        "unassigned": [job_out(ds, ds.job(j), "unassigned")
                       for j in plan.unassigned],
        "candidates": plan.candidates,
        "pins": plan.pins,
        "completed": sorted(completed or {}),
    }
    if diff is not None:
        out["diff"] = {
            "moved": [{"job_id": j, "from": a, "to": b} for j, a, b in diff.moved],
            "added": [{"job_id": j, "to": e} for j, e in diff.added],
            "removed": diff.removed,
            "kept": diff.kept,
            "affected": diff.affected,
            "untouched": diff.untouched,
            "summary": diff.summary(),
        }
    return out


def engineer_out(ds: Dataset, eng, onboard: dict[str, set[str]] | None = None) -> dict:
    kit = sorted(onboard.get(eng.id, eng.onboard_equipment)) if onboard \
        else sorted(eng.onboard_equipment)
    return {
        "id": eng.id,
        "name": eng.name,
        "skills": [
            {"specialization": s,
             "specialization_name": ds.specializations.get(s, s),
             "level": lv}
            for s, lv in sorted(eng.skills.items())
        ],
        "shift": [min_to_hhmm(eng.shift_start), min_to_hhmm(eng.shift_end)],
        "break_window": [min_to_hhmm(eng.break_from), min_to_hhmm(eng.break_to)],
        "break_min": eng.break_min,
        "vehicle_type": eng.vehicle_type.value,
        "can_carry_bulky": eng.vehicle_type.can_carry_bulky,
        "home": {"lat": eng.home_lat, "lon": eng.home_lon,
                 "address": eng.home_address},
        "equipment": [{"id": q, "name": ds.equipment[q].name} for q in kit],
        "max_overtime_min": eng.max_overtime_min,
    }
