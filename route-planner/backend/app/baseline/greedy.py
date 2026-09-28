"""Базовые варианты, с которыми сравнивается план.

Нужны ради одной цифры — насколько лучше стало. Без неё жюри не с чем сравнить
«назначено 98 %», и вся работа выглядит как красивая картинка.

Два базовых варианта:

* **по ТЗ** (`tz_baseline`): заявки обрабатываются по порядку поступления и
  назначаются первому по порядку во входных данных доступному инженеру,
  который удовлетворяет обязательным ограничениям; порядок посещения — порядок
  назначения. Так ТЗ задаёт единое сравнение для всех команд (п. 2.3);
* **грамотный диспетчер** (`greedy_plan` по умолчанию): разбирает заявки по
  срочности (сначала те, у кого окно закрывается раньше) и отдаёт каждую
  ближайшему подходящему инженеру. Это честный потолок ручного планирования —
  сравнение с ним показывает выигрыш не над соломенным чучелом.

Чего ни один из них не делает — и в этом вся разница с оптимизатором:

* не переставляет уже назначенные визиты, когда приходит следующая заявка;
* не смотрит на день целиком, а решает по одной заявке;
* не жертвует локальным оптимумом ради общего (ближайший инженер сегодня —
  не значит лучший для маршрута в целом).

Комплектация инструментом берётся та же, что и у оптимизатора: сравниваем
качество маршрутизации, а не удачу утренней выдачи со склада.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

from ..domain.models import Dataset, Engineer, Job, service_minutes
from ..solver.engine import (
    PICKUP_MIN,
    Plan,
    Route,
    Stop,
    Weights,
    assign_equipment,
    compute_kpi,
    eligible_engineers,
)
from ..travel.provider import Point, TravelTimeProvider, default_provider


@dataclass(slots=True)
class _Sched:
    """Текущее состояние инженера по ходу жадного назначения."""

    eng: Engineer
    pos: Point
    free_at: int
    route: Route
    lunch_taken: bool = False


def _latest_start(job: Job, svc: int) -> int:
    """Правило ТЗ: начало работ попадает в окно; окончание им не ограничено."""
    return job.tw_end


def greedy_plan(
    ds: Dataset,
    jobs: list[Job] | None = None,
    provider: TravelTimeProvider | None = None,
    onboard: dict[str, set[str]] | None = None,
    pickup: dict[str, str | None] | None = None,
    order: str = "edf",
    pick: str = "nearest",
) -> Plan:
    """Построить план «как вручную».

    order: "edf"  — сначала те, у кого окно закрывается раньше (грамотный диспетчер)
           "fifo" — строго в порядке поступления (как реально лежит список)
    pick:  "nearest" — из подходящих берём ближайшего (так и рассуждает человек)
           "soonest" — берём того, кто освободится раньше всех
           "first"   — первого подходящего по порядку во входных данных (ТЗ)

    По умолчанию «ближайший»: диспетчер мыслит географией, а не расписанием.
    Вариант «кто раньше освободится» гоняет людей через весь город и делает
    baseline неоправданно слабым — сравнение с таким выглядит подтасовкой.
    """
    started = time.perf_counter()
    provider = provider or default_provider()
    jobs = jobs if jobs is not None else [j for j in ds.jobs if j.known_at_day_start]

    if onboard is None:
        onboard, pickup = assign_equipment(ds, jobs)
    elif pickup is None:
        pickup = {e.id: None for e in ds.engineers}

    # Диспетчер разбирает список сверху вниз
    if order == "edf":
        queue = sorted(jobs, key=lambda j: (j.tw_end, j.priority.value, j.id))
    else:
        queue = sorted(jobs, key=lambda j: (j.created_at_min, j.id))

    state: list[_Sched] = []
    for eng in ds.engineers:
        wh = ds.warehouses.get(pickup.get(eng.id)) if pickup.get(eng.id) else None
        start_pos: Point = (wh.lat, wh.lon) if wh else eng.home
        start_at = eng.shift_start
        if wh:
            start_at = max(start_at, wh.open_from)
        route = Route(
            engineer_id=eng.id, engineer_name=eng.name,
            vehicle_type=eng.vehicle_type.value,
            pickup_warehouse=wh.id if wh else None,
            start_min=start_at,
        )
        route.stops.append(Stop(
            kind="start", job_id=None,
            label=(f"Склад: {wh.name}" if wh else f"Выезд из дома — {eng.home_address}"),
            lat=start_pos[0], lon=start_pos[1],
            arrival=start_at, service_start=start_at,
            service_end=start_at + (PICKUP_MIN if wh else 0),
            wait_min=0, travel_min_from_prev=0, travel_km_from_prev=0.0,
        ))
        state.append(_Sched(eng=eng, pos=start_pos,
                            free_at=start_at + (PICKUP_MIN if wh else 0),
                            route=route))

    unassigned: list[str] = []

    for job in queue:
        allowed = set(eligible_engineers(ds, job, onboard))
        best: tuple[int, int, int] | None = None      # ключ выбора
        best_idx = -1

        for idx, s in enumerate(state):
            if idx not in allowed:
                continue
            svc = service_minutes(job, s.eng)
            depart = s.free_at
            # обед: один раз за день, как только попали в окно обеда
            if not s.lunch_taken and depart >= s.eng.break_from:
                depart += s.eng.break_min
            travel = provider.minutes(s.pos, job.location,
                                      s.eng.vehicle_type, depart)
            start = max(depart + travel, job.tw_start)
            if start > _latest_start(job, svc):
                continue
            back = (provider.minutes(job.location, s.eng.home,
                                     s.eng.vehicle_type, start + svc)
                    if ds.return_to_start else 0)
            if start + svc + back > s.eng.shift_end + s.eng.max_overtime_min:
                continue
            if pick == "nearest":
                key = (travel, start, idx)
            elif pick == "first":
                key = (idx, start, travel)
            else:
                key = (start, travel, idx)
            if best is None or key < best:
                best, best_idx = key, idx

        if best is None:
            unassigned.append(job.id)
            continue

        s = state[best_idx]
        svc = service_minutes(job, s.eng)
        depart = s.free_at
        if not s.lunch_taken and depart >= s.eng.break_from:
            s.route.lunch_start = depart
            s.route.lunch_min = s.eng.break_min
            depart += s.eng.break_min
            s.lunch_taken = True
        travel = provider.minutes(s.pos, job.location, s.eng.vehicle_type, depart)
        km = provider.distance_km(s.pos, job.location)
        arrival = depart + travel
        start = max(arrival, job.tw_start)

        s.route.stops.append(Stop(
            kind="job", job_id=job.id,
            label=f"{job.customer} — {ds.work_types[job.work_type_id].name}",
            lat=job.lat, lon=job.lon,
            arrival=arrival, service_start=start, service_end=start + svc,
            wait_min=max(0, start - arrival),
            travel_min_from_prev=travel, travel_km_from_prev=round(km, 2),
            sla_deadline=job.sla_deadline,
            sla_late_min=max(0, start - job.sla_deadline),
            window=(job.tw_start, job.tw_end),
        ))
        s.route.travel_min += travel
        s.route.travel_km += km
        s.route.work_min += svc
        s.route.wait_min += max(0, start - arrival)
        s.pos = job.location
        s.free_at = start + svc

    routes: list[Route] = [_finish(ds, s, provider) for s in state]

    plan = Plan(
        date=ds.date, routes=routes, unassigned=unassigned, kpi={},
        solve_ms=int((time.perf_counter() - started) * 1000),
        status="BASELINE", weights=Weights(), onboard=onboard, pickup=pickup,
    )
    plan.kpi = compute_kpi(plan, jobs)
    return plan


def _finish(ds: Dataset, s: _Sched, provider: TravelTimeProvider) -> Route:
    """Закрыть маршрут: вернуться домой, если так устроен набор, либо
    закончить на последней заявке. Инженер без заявок остаётся дома —
    холостой рейс на склад ему не нужен (см. solver.engine)."""
    e = s.eng
    if not s.route.job_count:
        home = Stop(kind="start", job_id=None, label=f"Смена не начата — {e.home_address}",
                    lat=e.home_lat, lon=e.home_lon, arrival=e.shift_start,
                    service_start=e.shift_start, service_end=e.shift_start,
                    wait_min=0, travel_min_from_prev=0, travel_km_from_prev=0.0)
        s.route.stops = [home, replace(home, kind="end")]
        s.route.pickup_warehouse = None
        s.route.travel_min = s.route.travel_km = 0
        s.route.start_min = s.route.end_min = e.shift_start
        return s.route
    if ds.return_to_start:
        back = provider.minutes(s.pos, e.home, e.vehicle_type, s.free_at)
        km = provider.distance_km(s.pos, e.home)
        end_point, label = e.home, f"Возврат домой — {e.home_address}"
    else:
        back, km = 0, 0.0
        end_point, label = s.pos, "Конец маршрута"
    end = s.free_at + back
    s.route.travel_min += back
    s.route.travel_km = round(s.route.travel_km + km, 2)
    s.route.end_min = end
    s.route.stops.append(Stop(
        kind="end", job_id=None, label=label,
        lat=end_point[0], lon=end_point[1],
        arrival=end, service_start=end, service_end=end,
        wait_min=0, travel_min_from_prev=back, travel_km_from_prev=round(km, 2),
    ))
    return s.route


def tz_baseline(ds: Dataset, jobs: list[Job] | None = None,
                provider: TravelTimeProvider | None = None,
                onboard: dict[str, set[str]] | None = None,
                pickup: dict[str, str | None] | None = None) -> Plan:
    """Базовый вариант ровно по ТЗ, п. 2.3 — единый для всех команд."""
    plan = greedy_plan(ds, jobs, provider, onboard, pickup, order="fifo", pick="first")
    plan.status = "BASELINE_TZ"
    return plan


# --------------------------------------------------------------------------
# Сравнение
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Comparison:
    rows: list[tuple[str, str, str, str]] = field(default_factory=list)

    def add(self, label: str, base: float, opt: float,
            lower_is_better: bool = True, neutral: bool = False) -> None:
        if neutral:
            # Показатель без однозначной «хорошей» стороны: меньше занятых
            # инженеров при большем числе заявок — это высвобожденные люди,
            # а не потеря. Оценку оставляем человеку.
            self.rows.append((label, f"{base:g}", f"{opt:g}", ""))
            return
        if base == 0:
            if opt == 0:
                delta = "—"
            else:
                delta = "лучше ✓" if not lower_is_better else "хуже"
        else:
            pct = (opt - base) / abs(base) * 100
            good = (pct < 0) if lower_is_better else (pct > 0)
            sign = "" if pct < 0 else "+"
            mark = " ✓" if good and abs(pct) >= 1 else ""
            delta = f"{sign}{pct:.0f} %{mark}"
        self.rows.append((label, f"{base:g}", f"{opt:g}", delta))

    def text(self) -> str:
        width = max(len(r[0]) for r in self.rows)
        lines = [f"{'Показатель'.ljust(width)}   {'Вручную':>12}   "
                 f"{'Оптимизатор':>12}   Эффект",
                 "-" * (width + 46)]
        for label, base, opt, delta in self.rows:
            lines.append(f"{label.ljust(width)}   {base:>12}   {opt:>12}   {delta}")
        return "\n".join(lines)


def compare(baseline: Plan, optimized: Plan) -> Comparison:
    b, o = baseline.kpi, optimized.kpi
    c = Comparison()

    # Главная строка. Сравнивать нарушения SLA напрямую нельзя: план, который
    # просто не взял неудобные заявки, покажет меньше срывов — но неназначенная
    # заявка для клиента хуже опоздания. Считаем то, что видит заказчик:
    # сколько заявок закрыто и закрыто вовремя.
    c.add("Закрыто в срок", b["jobs_assigned"] - b["sla_violations"],
          o["jobs_assigned"] - o["sla_violations"], lower_is_better=False)
    c.add("Назначено заявок", b["jobs_assigned"], o["jobs_assigned"],
          lower_is_better=False)
    c.add("Не назначено", b["jobs_unassigned"], o["jobs_unassigned"])
    c.add("Нарушений SLA", b["sla_violations"], o["sla_violations"])
    c.add("Время в пути, мин", b["travel_min"], o["travel_min"])
    c.add("Пробег, км", b["travel_km"], o["travel_km"])
    c.add("Простой в ожидании, мин", b["wait_min"], o["wait_min"])
    c.add("Разброс загрузки, мин", b["busy_spread_min"], o["busy_spread_min"])
    c.add("Задействовано инженеров", b["engineers_used"], o["engineers_used"],
          neutral=True)
    return c
