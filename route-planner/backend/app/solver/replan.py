"""Перепланирование дня по событиям.

Ключевая мысль: перепланирование — это НЕ пересчёт с нуля. День уже частично
прожит, и то, что случилось, обсуждению не подлежит:

    выполненные и начатые визиты   — зафиксированы, из модели исключены;
    инженер стартует              — не из дома, а оттуда, где он сейчас;
    смена                         — от «сейчас» до конца рабочего дня;
    инструмент                     — уже на руках с утра, второй раз на склад никто не поедет;
    прошлое решение                — тёплый старт, чтобы не перетряхивать весь город;
    перенос визита другому         — платный (Weights.stability).

На выходе — не только новый план, но и diff: у кого что изменилось. Именно он
показывает, что система не перекраивает день целиком из-за одной заявки.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..domain.models import Dataset, DayEvent, Engineer, TransportMode, min_to_hhmm
from ..travel.provider import TravelTimeProvider
from .engine import Plan, Weights, compute_kpi, solve


# --------------------------------------------------------------------------
# Состояние дня
# --------------------------------------------------------------------------

@dataclass(slots=True)
class DayState:
    """Что успело произойти к моменту `now`."""

    now: int
    cancelled: set[str] = field(default_factory=set)
    unavailable: set[str] = field(default_factory=set)     # инженеры выбыли
    lost_vehicle: set[str] = field(default_factory=set)    # остались без авто
    overrun: dict[str, int] = field(default_factory=dict)  # заявка -> +минут
    arrived: set[str] = field(default_factory=set)         # заявки, поступившие днём

    #: Что уже выполнено, накопительно по ходу дня: заявка -> инженер.
    #: Копится между перепланированиями и НЕ выводится заново из последнего
    #: плана. Иначе визит, начатый в 09:00, при пересчёте в 09:05 снова окажется
    #: «в будущем», уедет дальше по расписанию — и так весь день, пока работа
    #: не перестанет выполняться вовсе.
    completed: dict[str, str] = field(default_factory=dict)

    def describe(self) -> list[str]:
        out = []
        if self.arrived:
            out.append(f"новых заявок: {len(self.arrived)}")
        if self.cancelled:
            out.append(f"отменено: {len(self.cancelled)}")
        if self.unavailable:
            out.append(f"выбыли инженеры: {', '.join(sorted(self.unavailable))}")
        if self.lost_vehicle:
            out.append(f"без авто: {', '.join(sorted(self.lost_vehicle))}")
        if self.overrun:
            out.append(f"затянулись визиты: {len(self.overrun)}")
        return out


def state_at(events: list[DayEvent], now: int,
             completed: dict[str, str] | None = None) -> DayState:
    """Свернуть ленту событий в состояние на момент `now`.

    `completed` передаётся симулятором и накапливается между вызовами.
    """
    st = DayState(now=now, completed=completed if completed is not None else {})
    for ev in events:
        if ev.at > now:
            continue
        if ev.type == "job_created":
            st.arrived.add(ev.payload)
        elif ev.type == "job_cancelled":
            st.cancelled.add(ev.payload)
        elif ev.type == "job_overrun":
            job_id, _, delta = ev.payload.partition(":+")
            st.overrun[job_id] = int(delta or 0)
        elif ev.type == "engineer_unavailable":
            st.unavailable.add(ev.payload)
        elif ev.type == "vehicle_breakdown":
            st.lost_vehicle.add(ev.payload)
    return st


# --------------------------------------------------------------------------
# Проекция прошлого плана на момент времени
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Projection:
    engineers: list[Engineer]        # с текущей позицией и остатком смены
    locked: dict[str, str]           # заявка -> инженер (сделано или в работе)
    previous: dict[str, str]         # заявка -> инженер по прошлому плану


def project(ds: Dataset, prev: Plan, st: DayState) -> Projection:
    """Где инженеры к моменту `now` и что уже нельзя трогать."""
    locked: dict[str, str] = dict(st.completed)   # выполненное ранее — навсегда
    previous: dict[str, str] = {}
    engineers: list[Engineer] = []

    for route in prev.routes:
        eng = ds.engineer(route.engineer_id)
        for stop in route.stops:
            if stop.kind == "job" and stop.job_id:
                previous[stop.job_id] = eng.id

        # Идём по маршруту до момента `now`. Визиты, которые успели начаться,
        # фиксируем; на первом недостигнутом узле интерполируем положение
        # вдоль отрезка.
        #
        # Интерполяция здесь не украшательство. Если инженера, который уже в
        # пути, при каждом пересчёте возвращать в начало маршрута, дорога
        # считается заново — и при частых пересчётах он не доезжает никуда
        # вообще. День перестаёт выполняться.
        position = (route.stops[0].lat, route.stops[0].lon)
        departed_at = route.stops[0].service_end
        free_at = max(st.now, departed_at)

        for stop in route.stops[1:]:
            started = (stop.kind == "job" and stop.job_id
                       and stop.service_start <= st.now)
            if started:
                locked[stop.job_id] = eng.id
                position = (stop.lat, stop.lon)
                departed_at = stop.service_end + st.overrun.get(stop.job_id, 0)
                free_at = max(st.now, departed_at)
                continue
            # до этой точки инженер ещё не доехал — он где-то на отрезке
            if st.now > departed_at and stop.arrival > departed_at:
                done = min(1.0, (st.now - departed_at) / (stop.arrival - departed_at))
                position = (position[0] + (stop.lat - position[0]) * done,
                            position[1] + (stop.lon - position[1]) * done)
            break

        if eng.id in st.unavailable:
            continue                          # выбыл: его будущие заявки в общий котёл

        engineers.append(replace(
            eng,
            vehicle_type=(TransportMode.WALK_TRANSIT if eng.id in st.lost_vehicle
                          else eng.vehicle_type),
            current_lat=position[0],
            current_lon=position[1],
            available_from=min(free_at, eng.shift_end),
        ))

    # то, что зафиксировано сейчас, остаётся зафиксированным и на следующих шагах
    st.completed.update(locked)
    return Projection(engineers=engineers, locked=locked, previous=previous)


# --------------------------------------------------------------------------
# Diff двух планов
# --------------------------------------------------------------------------

@dataclass(slots=True)
class PlanDiff:
    moved: list[tuple[str, str, str]] = field(default_factory=list)   # заявка, откуда, куда
    added: list[tuple[str, str]] = field(default_factory=list)        # заявка, кому
    removed: list[str] = field(default_factory=list)                  # выпали из плана
    kept: int = 0
    affected: list[str] = field(default_factory=list)
    untouched: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"перенесено {len(self.moved)}, добавлено {len(self.added)}, "
                f"снято {len(self.removed)}, без изменений {self.kept}; "
                f"затронуто инженеров {len(self.affected)} "
                f"из {len(self.affected) + len(self.untouched)}")


def diff_plans(prev_assignment: dict[str, str], new_plan: Plan,
               locked: dict[str, str], all_engineers: list[str]) -> PlanDiff:
    d = PlanDiff()
    new_assignment = {
        s.job_id: r.engineer_id
        for r in new_plan.routes for s in r.stops
        if s.kind == "job" and s.job_id
    }
    touched: set[str] = set()

    for job_id, eng_id in new_assignment.items():
        was = prev_assignment.get(job_id)
        if was is None:
            d.added.append((job_id, eng_id))
            touched.add(eng_id)
        elif was != eng_id:
            d.moved.append((job_id, was, eng_id))
            touched.update({was, eng_id})
        else:
            d.kept += 1

    for job_id, eng_id in prev_assignment.items():
        if job_id in locked:
            continue
        if job_id not in new_assignment:
            d.removed.append(job_id)
            touched.add(eng_id)

    d.affected = sorted(touched)
    d.untouched = sorted(set(all_engineers) - touched)
    return d


# --------------------------------------------------------------------------
# Собственно перепланирование
# --------------------------------------------------------------------------

def replan(
    ds: Dataset,
    prev: Plan,
    st: DayState,
    weights: Weights | None = None,
    provider: TravelTimeProvider | None = None,
    time_limit_s: int = 3,
    pins: dict[str, str] | None = None,
) -> tuple[Plan, PlanDiff]:
    """Пересчитать остаток дня. Быстро: лимит по умолчанию 3 секунды."""
    weights = weights or Weights(stability=40)
    proj = project(ds, prev, st)

    plannable = [
        j for j in ds.jobs
        if j.id not in proj.locked
        and j.id not in st.cancelled
        and j.created_at_min <= st.now
    ]
    plannable_ids = {j.id for j in plannable}

    # тёплый старт: порядок маршрутов должен совпадать с порядком инженеров
    prev_routes = {
        r.engineer_id: [s.job_id for s in r.stops if s.kind == "job" and s.job_id]
        for r in prev.routes
    }
    warm = [[jid for jid in prev_routes.get(e.id, []) if jid in plannable_ids]
            for e in proj.engineers]

    plan = solve(
        ds,
        jobs=plannable,
        weights=weights,
        provider=provider,
        time_limit_s=time_limit_s,
        engineers=proj.engineers,
        onboard=prev.onboard or None,
        previous_assignment=proj.previous,
        pins=pins,
        initial_routes=warm if any(warm) else None,
    )

    # заявки, зафиксированные до перепланирования, остаются в плане дня:
    # они выполнены, и KPI обязан их учитывать
    plan.kpi = compute_kpi(plan, plannable)
    plan.kpi["locked_done"] = len(proj.locked)
    plan.kpi["replanned_at"] = min_to_hhmm(st.now)

    all_ids = [e.id for e in ds.engineers]
    d = diff_plans(
        {j: e for j, e in proj.previous.items() if j not in proj.locked},
        plan, proj.locked, all_ids)
    return plan, d
