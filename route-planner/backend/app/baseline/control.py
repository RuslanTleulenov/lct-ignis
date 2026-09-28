"""Факт: как диспетчер заказчика раскидал заявки на самом деле.

В выгрузке «Билайн Бизнес» к каждой заявке приложено контрольное
распределение — бригада, которой её отдали, и статус к концу дня. Это не
модель диспетчера, а сам диспетчер, поэтому сравнение с ним честнее любого
базового варианта: план оптимизатора против того, что произошло 17 августа.

Чего в контроле нет — порядка визитов и времени выезда. Восстанавливаем их
единственным разумным способом: бригада объезжает свои заявки по началу окна,
а при равных окнах — в порядке выгрузки. Время в пути считается тем же
провайдером, что и для плана, иначе сравнение пробега потеряло бы смысл.
"""

from __future__ import annotations

from collections import defaultdict

from ..domain.models import Dataset, Job, service_minutes
from ..solver.engine import Plan, Route, Stop, Weights, compute_kpi
from ..travel.provider import TravelTimeProvider, default_provider


def has_control(jobs: list[Job]) -> bool:
    return any(j.control_engineer for j in jobs)


def control_plan(ds: Dataset, jobs: list[Job] | None = None,
                 provider: TravelTimeProvider | None = None) -> Plan | None:
    """Воспроизвести распределение диспетчера как план. None — контроля нет."""
    provider = provider or default_provider()
    jobs = jobs if jobs is not None else [j for j in ds.jobs if j.known_at_day_start]
    if not has_control(jobs):
        return None

    by_engineer: dict[str, list[Job]] = defaultdict(list)
    for j in jobs:
        if j.control_engineer:
            by_engineer[j.control_engineer].append(j)

    routes: list[Route] = []
    for e in ds.engineers:
        route = Route(engineer_id=e.id, engineer_name=e.name,
                      vehicle_type=e.vehicle_type.value, pickup_warehouse=None,
                      start_min=e.shift_start)
        mine = sorted(by_engineer.get(e.id, []),
                      key=lambda j: (j.tw_start, jobs.index(j)))
        route.stops.append(Stop(
            kind="start", job_id=None,
            label=(f"Выезд — {e.home_address}" if mine
                   else f"Смена не начата — {e.home_address}"),
            lat=e.home_lat, lon=e.home_lon,
            arrival=e.shift_start, service_start=e.shift_start,
            service_end=e.shift_start, wait_min=0,
            travel_min_from_prev=0, travel_km_from_prev=0.0))

        pos, free_at, lunch_taken = e.home, e.shift_start, False
        for n, job in enumerate(mine):
            svc = service_minutes(job, e)
            depart = free_at
            if n == 0:
                # Никто не выезжает в 09:30 ждать окна 14:00 у подъезда:
                # к первой заявке бригада выезжает так, чтобы приехать к
                # открытию окна. Иначе «факт» получал бы часы простоя,
                # которых у диспетчера не было.
                travel = provider.minutes(pos, job.location, e.vehicle_type, depart)
                depart = max(e.shift_start, job.tw_start - travel)
                route.start_min = depart
                route.stops[0].arrival = route.stops[0].service_start = depart
                route.stops[0].service_end = depart
            if not lunch_taken and depart >= e.break_from:
                route.lunch_start, route.lunch_min = depart, e.break_min
                depart += e.break_min
                lunch_taken = True
            travel = provider.minutes(pos, job.location, e.vehicle_type, depart)
            km = provider.distance_km(pos, job.location, e.vehicle_type)
            arrival = depart + travel
            start = max(arrival, job.tw_start)
            route.stops.append(Stop(
                kind="job", job_id=job.id,
                label=f"{job.customer} — {ds.work_types[job.work_type_id].name}",
                lat=job.lat, lon=job.lon,
                arrival=arrival, service_start=start, service_end=start + svc,
                wait_min=max(0, start - arrival),
                travel_min_from_prev=travel, travel_km_from_prev=round(km, 2),
                sla_deadline=job.sla_deadline,
                sla_late_min=max(0, start - job.sla_deadline),
                window=(job.tw_start, job.tw_end)))
            route.travel_min += travel
            route.travel_km += km
            route.work_min += svc
            route.wait_min += max(0, start - arrival)
            pos, free_at = job.location, start + svc

        route.travel_km = round(route.travel_km, 2)
        route.end_min = free_at
        route.stops.append(Stop(
            kind="end", job_id=None, label="Конец маршрута",
            lat=pos[0], lon=pos[1], arrival=free_at, service_start=free_at,
            service_end=free_at, wait_min=0, travel_min_from_prev=0,
            travel_km_from_prev=0.0))
        routes.append(route)

    unassigned = [j.id for j in jobs if not j.control_engineer]
    plan = Plan(date=ds.date, routes=routes, unassigned=unassigned, kpi={},
                solve_ms=0, status="CONTROL", weights=Weights())
    plan.kpi = compute_kpi(plan, jobs)
    # Статусы из выгрузки — то, что произошло на самом деле, а не наша оценка.
    statuses = [j.control_status for j in jobs]
    plan.kpi["control_overdue"] = sum(1 for s in statuses if s == "Просрочена")
    plan.kpi["control_cancelled"] = sum(1 for s in statuses if s == "Отменена")
    plan.kpi["control_done"] = sum(1 for s in statuses if s == "Выполнена")
    return plan
