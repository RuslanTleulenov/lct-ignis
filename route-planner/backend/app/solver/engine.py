"""Построение и решение задачи маршрутизации на OR-Tools.

Модель: VRPTW с мульти-депо, разнородным транспортом, ограничениями по
квалификации и оборудованию и необязательными визитами.

Соответствие требований кейса механизмам библиотеки:

    временные окна          -> AddDimensionWithVehicleTransits + CumulVar.SetRange
    мягкие окна и SLA       -> SetCumulVarSoftUpperBound
    квалификация            -> VehicleVar(index).SetValues([допустимые инженеры])
    оборудование            -> тот же фильтр + утреннее распределение инструмента
    габарит требует авто    -> тот же фильтр
    авто / пешком           -> своя матрица времени и свой callback на инженера
    рабочая смена           -> CumulVar на Start/End маршрута
    обед                    -> SetBreakIntervalsOfVehicle
    заявку можно не взять   -> AddDisjunction со штрафом по приоритету
    равномерность загрузки  -> SetGlobalSpanCostCoefficient
    быстрый переплан        -> ReadAssignmentFromRoutes (см. replan.py)
"""

from __future__ import annotations

import os
import time
from collections import Counter
from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from ..domain.models import (
    Dataset,
    Engineer,
    Job,
    TransportMode,
    min_to_hhmm,
    service_minutes,
)
from ..travel.provider import Point, TravelTimeProvider, default_provider

HORIZON = 24 * 60          # верхняя граница времени, минуты от полуночи
MAX_WAIT = 12 * 60         # ожидание открытия окна не ограничено жёстко: простой платный (см. idle)
PICKUP_MIN = 15            # время получения инструмента на складе
READ_BUDGET = 16           # сколько раз читать прошлое решение при починке тёплого старта
REPAIR_SECONDS = 1.0       # и сколько времени на это отводится
READ_TIMEOUT_MS = 400      # лимит одного чтения: дольше — значит, маршрут отдаём поиску

#: Час, на который считается матрица времени в пути.
#:
#: Матрица одна на весь день: сделать время поездки зависимым от момента выезда
#: в OR-Tools можно только отдельными callback'ами на каждый временной интервал,
#: и это отдельная большая переделка. Значит, опорный час надо выбирать честно.
#: Среднее значение коэффициента пробок по рабочему дню 08:00–19:00 — 1.32;
#: ближе всего к нему 15:00 (1.25). Раньше здесь стояло 10:00 с коэффициентом
#: 1.15 — утреннее затишье, из-за которого весь день был занижен примерно на
#: десятую часть, а вечерние перегоны — в полтора раза.
MATRIX_REFERENCE_MIN = 15 * 60


class _Timing:
    """Замер фаз solve() — печатается при RP_TIMING=1. Оставлен намеренно:
    именно так нашлось, что чтение тёплого старта стоит 176 мс, а
    Python-колбэки съедали две трети лимита поиска."""

    def __init__(self) -> None:
        self.enabled = bool(os.environ.get("RP_TIMING"))
        self.last = time.perf_counter()
        self.parts: list[tuple[str, float]] = []

    def mark(self, label: str) -> None:
        now = time.perf_counter()
        self.parts.append((label, now - self.last))
        self.last = now

    def report(self) -> None:
        if self.enabled:
            print("    [solve] " + ", ".join(f"{k} {v * 1000:.0f} мс" for k, v in self.parts))



# --------------------------------------------------------------------------
# Настройки оптимизации
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Weights:
    """Веса целевой функции. В UI это ползунки, здесь — просто множители."""

    travel: int = 1        # минута в пути
    sla: int = 1           # множитель к штрафу за просрочку SLA
    drop: int = 1          # множитель к штрафу за неназначенную заявку
    soft_window: int = 15  # штраф за минуту выхода за мягкое окно
    balance: int = 0       # выравнивание загрузки (SetGlobalSpanCostCoefficient)
    idle: int = 1          # штраф за длину рабочего дня: сжимает простои
    stability: int = 0     # штраф за перенос визита другому инженеру при перепланировании
    staff: int = 60        # цена выхода инженера в поле, в минутах дороги (метрика ТЗ)

    @staticmethod
    def preset(name: str) -> "Weights":
        return {
            "sla":      Weights(travel=1, sla=3, drop=2, soft_window=30, balance=0),
            "travel":   Weights(travel=3, sla=1, drop=1, soft_window=10, balance=0),
            "balance":  Weights(travel=1, sla=1, drop=1, soft_window=15, balance=8, staff=0),
            "staff":    Weights(travel=1, sla=1, drop=1, soft_window=15, balance=0, staff=300),
            "default":  Weights(),
        }[name]


# --------------------------------------------------------------------------
# Результат
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Stop:
    kind: str                  # "start" | "job" | "end"
    job_id: str | None
    label: str
    lat: float
    lon: float
    arrival: int               # когда инженер оказался в точке
    service_start: int
    service_end: int
    wait_min: int
    travel_min_from_prev: int
    travel_km_from_prev: float
    sla_deadline: int | None = None
    sla_late_min: int = 0
    window: tuple[int, int] | None = None
    #: Ломаная [[lon, lat], …] от предыдущей точки маршрута. Заполняется не
    #: солвером, а отдельным проходом: восстановление пути стоит времени, а
    #: при перепланировании за три секунды каждая миллисекунда на счету.
    geometry: list[list[float]] = field(default_factory=list)
    via_metro: bool = False


@dataclass(slots=True)
class Route:
    engineer_id: str
    engineer_name: str
    vehicle_type: str
    pickup_warehouse: str | None
    stops: list[Stop] = field(default_factory=list)
    travel_min: int = 0
    travel_km: float = 0.0
    work_min: int = 0
    wait_min: int = 0
    start_min: int = 0
    end_min: int = 0
    #: Когда инженер обедал. Без этого обед попадает в «простой в ожидании» и
    #: раздувает KPI на 45 минут на человека, а объяснение уверенно врёт
    #: «ждал открытия окна».
    lunch_start: int | None = None
    lunch_min: int = 0

    @property
    def job_count(self) -> int:
        return sum(1 for s in self.stops if s.kind == "job")

    @property
    def busy_min(self) -> int:
        return self.travel_min + self.work_min


@dataclass(slots=True)
class Plan:
    date: str
    routes: list[Route]
    unassigned: list[str]
    kpi: dict
    solve_ms: int
    status: str
    weights: Weights
    # служебное — нужно для перепланирования и объяснений
    onboard: dict[str, set[str]] = field(default_factory=dict)
    #: Кто на какой склад заезжал утром. Хранится рядом с onboard: это две
    #: половины одного решения, и разлучать их нельзя — план, построенный
    #: с чужим onboard, но без заездов, стартует из других точек.
    pickup: dict[str, str | None] = field(default_factory=dict)
    candidates: dict[str, list[str]] = field(default_factory=dict)
    #: ручные закрепления диспетчера, действовавшие при расчёте
    pins: dict[str, str] = field(default_factory=dict)
    #: удалось ли стартовать от прошлого решения. False при перепланировании —
    #: тревожный признак: значит план пересобран с нуля и будет сильно отличаться
    warm_started: bool = False

    def routes_as_node_lists(self) -> list[list[str]]:
        return [[s.job_id for s in r.stops if s.kind == "job"] for r in self.routes]


# --------------------------------------------------------------------------
# Утреннее распределение дефицитного инструмента
# --------------------------------------------------------------------------

def assign_equipment(ds: Dataset, jobs: list[Job],
                     engineers: list[Engineer] | None = None
                     ) -> tuple[dict[str, set[str]], dict[str, str | None]]:
    """Кто с какого склада начинает день и какой инструмент забирает.

    Инструмент, которого нет на руках, инженер получает утром на складе — так
    работает и реальная выездная служба. Маршрут такого инженера стартует не из
    дома, а со склада, то есть оборудование это не тупик, а стоимость.

    Комплектуем КОМПЛЕКТАМИ, а не по одной позиции. Рефлектометр без сварочного
    аппарата бесполезен: заявка требует оба, и половина набора не даёт ничего,
    зато занимает дефицитную единицу. Поэтому идём от типов работ: набор берём
    целиком либо не берём вовсе.

    Ограничение осознанное: за день инженер заезжает не более чем на ОДИН склад.
    Мотаться по двум складам он не станет, и диспетчер такой план не примет.
    """
    engineers = engineers if engineers is not None else ds.engineers
    onboard = {e.id: set(e.onboard_equipment) for e in engineers}
    pickup: dict[str, str | None] = {e.id: None for e in engineers}

    # свободные экземпляры: общий парк минус то, что уже роздано на руки
    remaining = {q: eq.units for q, eq in ds.equipment.items()}
    for e in engineers:
        for q in e.onboard_equipment:
            remaining[q] = remaining.get(q, 0) - 1

    demand = Counter(j.work_type_id for j in jobs)

    def missing_for(e: Engineer, wt, warehouse_id: str) -> set[str] | None:
        """Чего не хватает под этот тип работ. None — набрать невозможно."""
        if not demand[wt.id] or e.level_in(wt.specialization) < wt.min_level:
            return None
        kit = set(wt.equipment)
        if (any(ds.equipment[q].bulky for q in kit)
                and not e.vehicle_type.can_carry_bulky):
            return None                        # габарит без машины не увезти
        need = kit - onboard[e.id]
        if all(ds.equipment[q].available_at(warehouse_id) and remaining[q] > 0
               for q in need):
            return need
        return None

    def coverage(e: Engineer, warehouse_id: str) -> tuple[int, set[str]]:
        """Сколько заявок дня откроется, если отовариться на этом складе."""
        jobs_covered, take = 0, set()
        for wt in ds.work_types.values():
            need = missing_for(e, wt, warehouse_id)
            if need is not None:
                jobs_covered += demand[wt.id]
                take |= need
        return jobs_covered, take

    # Порядок важен: дефицитные приборы достаются старшим — они закрывают те
    # работы, которые больше никто не возьмёт.
    order = sorted(engineers, key=lambda e: (-max(e.skills.values()), e.id))

    for e in order:
        best: tuple[int, float, str, set[str]] | None = None
        for wh in ds.warehouses.values():
            covered, take = coverage(e, wh.id)
            if not take:
                continue
            detour = (e.home_lat - wh.lat) ** 2 + (e.home_lon - wh.lon) ** 2
            key = (covered, -detour, wh.id, take)
            if best is None or key[:2] > best[:2]:
                best = key
        if best is None:
            continue                            # всё нужное уже на руках
        _, _, wh_id, take = best
        for q in take:
            onboard[e.id].add(q)
            remaining[q] -= 1
        pickup[e.id] = wh_id

    return onboard, pickup


def eligible_engineers(ds: Dataset, job: Job, onboard: dict[str, set[str]],
                       engineers: list[Engineer] | None = None) -> list[int]:
    """Индексы инженеров, которые в принципе могут выполнить заявку.

    Только жёсткие ограничения — время и география решаются солвером.

    Индексы возвращаются относительно переданного списка. При перепланировании
    состав урезан (кто-то заболел), и нумерация обязана совпадать с той, по
    которой построена модель, иначе заявка уедет не тому человеку.
    """
    engineers = engineers if engineers is not None else ds.engineers
    transport = ds.required_transport(job)
    out = []
    for v, e in enumerate(engineers):
        if e.level_in(job.specialization) < job.min_level:
            continue
        # ТЗ: если в заявке указан тип транспорта, у инженера должен быть
        # именно он. Габаритный инструмент требует автомобиля тем же путём.
        if transport is not None and e.vehicle_type is not transport:
            continue
        if not set(job.required_equipment) <= onboard[e.id]:
            continue
        out.append(v)
    return out


# --------------------------------------------------------------------------
# Решатель
# --------------------------------------------------------------------------

def solve(
    ds: Dataset,
    jobs: list[Job] | None = None,
    weights: Weights | None = None,
    provider: TravelTimeProvider | None = None,
    time_limit_s: int = 10,
    use_breaks: bool = True,
    initial_routes: list[list[str]] | None = None,
    engineers: list[Engineer] | None = None,
    onboard: dict[str, set[str]] | None = None,
    pickup: dict[str, str | None] | None = None,
    previous_assignment: dict[str, str] | None = None,
    pins: dict[str, str] | None = None,
) -> Plan:
    """Построить план дня.

    jobs                 какие заявки планировать (по умолчанию — известные с утра)
    engineers            переопределённый состав: при перепланировании у людей
                         сдвинута смена и задана текущая позиция
    onboard              уже выданный утром инструмент; если задан, повторное
                         распределение не делается
    pickup               заезды на склад. По умолчанию при заданном onboard их
                         нет — это режим перепланирования, инструмент уже на
                         руках. Чтобы воспроизвести утренний план с чужой
                         комплектацией, передавайте pickup вместе с onboard
    previous_assignment  заявка -> инженер из прошлого плана, для штрафа за
                         нестабильность
    pins                 ручные закрепления диспетчера: заявка -> инженер.
                         Солвер обязан отдать визит именно ему
    initial_routes       прошлое решение для тёплого старта
    """
    weights = weights or Weights()
    provider = provider or default_provider()
    jobs = jobs if jobs is not None else [j for j in ds.jobs if j.known_at_day_start]
    engineers = engineers if engineers is not None else ds.engineers

    n_jobs, n_veh = len(jobs), len(engineers)
    if not jobs or not n_veh:
        return Plan(ds.date, [], [j.id for j in jobs], {}, 0, "EMPTY", weights)

    timing = _Timing()
    if onboard is None:
        onboard, pickup = assign_equipment(ds, jobs, engineers)
    else:
        # Перепланирование: инструмент уже на руках с утра, второй раз на склад
        # никто не поедет — если вызывающий явно не сказал обратное.
        onboard = {k: set(v) for k, v in onboard.items()}
        pickup = dict(pickup) if pickup is not None else {e.id: None for e in engineers}

    # ---- узлы: заявки, затем старт каждого инженера, затем финиш ----
    points: list[Point] = [j.location for j in jobs]
    for e in engineers:
        wh_id = pickup.get(e.id)
        wh = ds.warehouses.get(wh_id) if wh_id else None
        points.append(e.current_position
                      or ((wh.lat, wh.lon) if wh else e.home))
    points += [e.home for e in engineers]

    starts = [n_jobs + v for v in range(n_veh)]
    ends = [n_jobs + n_veh + v for v in range(n_veh)]

    timing.mark("подготовка")
    manager = pywrapcp.RoutingIndexManager(len(points), n_veh, starts, ends)
    routing = pywrapcp.RoutingModel(manager)

    # ---- матрицы времени: своя на каждый вид транспорта ----
    matrices = {
        mode: [row[:] for row in provider.matrix(points, mode, MATRIX_REFERENCE_MIN)]
        for mode in {e.vehicle_type for e in engineers}
    }
    if not ds.return_to_start:
        # Обязательный MVP по ТЗ: после последней заявки возвращаться в
        # стартовую точку не требуется. Дорога до узла-финиша обнуляется, и
        # маршрут заканчивается там, где закончилась работа. Иначе в пробег и
        # в смену попадал бы обратный путь, которого у заказчика нет.
        for m in matrices.values():
            for row in m:
                for end in ends:
                    row[end] = 0

    if weights.staff:
        # Обязательная метрика ТЗ — «наименьшее количество персонала». Фиксированная
        # плата за каждый непустой маршрут: лишнего инженера солвер выведет в
        # поле, только если это сэкономит больше минут дороги, чем стоит выход.
        routing.SetFixedCostOfAllVehicles(int(weights.staff))

    def service_at(node: int, v: int) -> int:
        if node < n_jobs:
            return service_minutes(jobs[node], engineers[v])
        if node == starts[v] and pickup[engineers[v].id]:
            return PICKUP_MIN            # получение инструмента на складе
        return 0

    # Транзиты регистрируются готовыми матрицами, а не Python-функциями.
    # Поиск дёргает транзит миллионы раз, и на колбэках уходило две трети
    # лимита времени в интерпретатор — солвер перебирал втрое меньше решений,
    # чем мог. Матрицы живут на стороне C++ и стоят ничего.
    n_nodes = len(points)
    time_cb_indices, cost_cb_indices = [], []
    for v, e in enumerate(engineers):
        matrix = matrices[e.vehicle_type]
        service = [service_at(a, v) for a in range(n_nodes)]
        time_matrix = [[matrix[a][b] + service[a] for b in range(n_nodes)]
                       for a in range(n_nodes)]
        cost_matrix = [[weights.travel * matrix[a][b] for b in range(n_nodes)]
                       for a in range(n_nodes)]
        cost_cb_indices.append(routing.RegisterTransitMatrix(cost_matrix))
        time_cb_indices.append(routing.RegisterTransitMatrix(time_matrix))

    # Стоимость дуги — только дорога, взвешенная. Если бы сюда входило время
    # работы, солверу было бы выгодно бросать длинные заявки ради «экономии».
    for v in range(n_veh):
        routing.SetArcCostEvaluatorOfVehicle(cost_cb_indices[v], v)

    routing.AddDimensionWithVehicleTransits(
        time_cb_indices, MAX_WAIT, HORIZON, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    if weights.balance:
        time_dim.SetGlobalSpanCostCoefficient(weights.balance)
    if weights.idle:
        # Штраф за протяжённость рабочего дня. Без него солверу всё равно,
        # что инженер два часа ждёт открытия окна: в стоимость входит только
        # дорога. Тут же простой становится платным, и день сжимается.
        time_dim.SetSpanCostCoefficientForAllVehicles(weights.idle)

    # ---- штраф за нестабильность плана ----
    # Отдельное измерение «Churn»: транзит в узел стоит 1, если эта заявка
    # раньше была у другого инженера. Сумма по маршруту = число перенесённых
    # визитов, и она попадает в целевую функцию через стоимость протяжённости.
    # Диспетчер не хочет обзванивать полгорода из-за одной новой заявки.
    if previous_assignment and weights.stability:
        eng_index = {e.id: v for v, e in enumerate(engineers)}
        churn_cbs = []
        for v in range(n_veh):
            # 1 за визит, который раньше был у другого инженера; новая
            # заявка — 0, переносить нечего. Зависит только от узла назначения,
            # но регистрируется матрицей, а не унарным вектором: унарный
            # транзит OR-Tools относит к узлу отправления, и после выбытия
            # инженера измерение с ним становилось противоречивым — тёплый
            # старт отвергался даже для пустого решения.
            churn = [0] * n_nodes
            for node, job in enumerate(jobs):
                prev = previous_assignment.get(job.id)
                if prev is not None and eng_index.get(prev) != v:
                    churn[node] = 1
            churn_cbs.append(routing.RegisterTransitMatrix(
                [churn[:] for _ in range(n_nodes)]))
        routing.AddDimensionWithVehicleTransits(
            churn_cbs, 0, n_jobs + 1, True, "Churn")
        routing.GetDimensionOrDie("Churn").SetSpanCostCoefficientForAllVehicles(
            weights.stability)

    # ---- смены инженеров ----
    for v, e in enumerate(engineers):
        earliest = e.starts_at
        wh_id = pickup.get(e.id)
        if wh_id:
            earliest = max(earliest, ds.warehouses[wh_id].open_from)
        time_dim.CumulVar(routing.Start(v)).SetRange(
            earliest, max(earliest, e.shift_end))
        time_dim.CumulVar(routing.End(v)).SetRange(
            earliest, max(earliest, e.shift_end + e.max_overtime_min))

    # ---- заявки: окна, SLA, допустимые исполнители, право не брать ----
    pins = pins or {}
    by_engineer_id = {e.id: v for v, e in enumerate(engineers)}
    candidates: dict[str, list[str]] = {}
    allowed_by_node: list[list[int]] = []
    for node, job in enumerate(jobs):
        index = manager.NodeToIndex(node)
        allowed = eligible_engineers(ds, job, onboard, engineers)
        # Полный список допустимых сохраняем до сужения: интерфейс показывает
        # диспетчеру, кому ещё можно отдать заявку, и закрепление не должно
        # прятать от него остальные варианты.
        candidates[job.id] = [engineers[v].id for v in allowed]

        pinned = by_engineer_id.get(pins.get(job.id, ""))
        if pinned is not None and pinned in allowed:
            allowed = [pinned]
        allowed_by_node.append(allowed)

        # -1 обязателен: без него заявку нельзя оставить неназначенной
        routing.VehicleVar(index).SetValues([-1] + allowed)
        routing.AddDisjunction([index], weights.drop * job.priority.drop_penalty)

        if job.tw_hard:
            # Правило ТЗ (п. 2.2, «Время»): начало работы должно попадать во
            # временное окно заявки. Окончание окном не ограничено — так
            # устроены и слоты заказчика: «18:00–20:00» означает, что мастер
            # приходит в этот интервал, а не что он обязан уйти к 20:00.
            time_dim.CumulVar(index).SetRange(job.tw_start, job.tw_end)
            soft_at, soft_w = job.sla_deadline, weights.sla * job.priority.sla_penalty_per_min
        else:
            # раньше начала окна на объект не пускают, позже — можно со штрафом
            time_dim.CumulVar(index).SetRange(job.tw_start, HORIZON)
            soft_at = min(job.tw_end, job.sla_deadline)
            soft_w = (weights.sla * job.priority.sla_penalty_per_min
                      + weights.soft_window)

        # У переменной может быть только одна мягкая верхняя граница, поэтому
        # штрафы за окно и за SLA сведены в один порог с суммарным весом.
        if soft_at < HORIZON:
            time_dim.SetCumulVarSoftUpperBound(index, soft_at, int(soft_w))

    # ---- обед ----
    if use_breaks:
        solver = routing.solver()
        for v, e in enumerate(engineers):
            # При перепланировании днём обед не пересматриваем: утренний план
            # его уже поставил. Если требовать его заново, когда окно обеда
            # открылось, а маршрут после проекции забит слотами, солвер ищет,
            # куда втиснуть перерыв, и чтение тёплого старта упирается в лимит
            # времени — 3 секунды на маршрут вместо миллисекунд.
            if not e.break_min or e.starts_at > e.break_from:
                continue
            latest_break_start = e.break_to - e.break_min
            service_by_index = [0] * routing.Size()
            for node in range(n_jobs):
                idx = manager.NodeToIndex(node)
                if 0 <= idx < len(service_by_index):
                    service_by_index[idx] = service_minutes(jobs[node], e)
            brk = solver.FixedDurationIntervalVar(
                max(e.break_from, e.starts_at), latest_break_start,
                e.break_min, False, f"break_{e.id}")
            time_dim.SetBreakIntervalsOfVehicle([brk], v, service_by_index)

    # ---- параметры поиска ----
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION)
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    params.time_limit.FromSeconds(time_limit_s)

    timing.mark("модель")
    started = time.perf_counter()
    warm_started = False
    if initial_routes:
        # Тёплый старт: при перепланировании держимся прошлого решения.
        # Визит, который этому инженеру больше не по силам (сломалось авто,
        # изменился набор инструмента), из начального решения выбрасываем:
        # ReadAssignmentFromRoutes отвергает маршрут целиком, если хоть один
        # узел нарушает ограничение, и молча откатывается к холодному поиску.
        node_of = {j.id: n for n, j in enumerate(jobs)}
        warm: list[list[int]] = []
        for v, route in enumerate(initial_routes):
            nodes = [node_of[jid] for jid in route if jid in node_of]
            nodes = [n for n in nodes if v in allowed_by_node[n]]
            kept = _prune_late_visits(nodes, v, engineers[v], jobs,
                                      matrices[engineers[v].vehicle_type],
                                      starts[v], service_at)
            warm.append(kept)
            if timing.enabled and len(kept) != len(nodes):
                print(f"    [warm] {engineers[v].id}: из {len(nodes)} визитов "
                      f"оставлено {len(kept)}")
        # Модель закрывается с коротким лимитом: он действует на чтение
        # прошлого решения, а не на поиск. Чтение с обедами иногда само
        # превращается в поиск и иначе съедает полный лимит — по 3 с на
        # маршрут. Что не читается за 0,4 с, отдаём поиску: он получит свои
        # `params` со своим лимитом при вызове Solve…WithParameters.
        read_params = pywrapcp.DefaultRoutingSearchParameters()
        read_params.CopyFrom(params)
        read_params.time_limit.FromMilliseconds(READ_TIMEOUT_MS)
        routing.CloseModelWithParameters(read_params)
        timing.mark("закрытие модели")
        initial = _repair_warm_start(routing, warm, timing.enabled)
        warm_started = initial is not None
        timing.mark("тёплый старт")
        solution = (routing.SolveFromAssignmentWithParameters(initial, params)
                    if initial else routing.SolveWithParameters(params))
    else:
        solution = routing.SolveWithParameters(params)
    solve_ms = int((time.perf_counter() - started) * 1000)
    timing.mark("поиск")

    if solution is None:
        return Plan(ds.date, [], [j.id for j in jobs], {}, solve_ms,
                    "NO_SOLUTION", weights, onboard, candidates)

    timing.report()
    plan = _extract(ds, jobs, engineers, manager, routing, solution, time_dim,
                    matrices, points, pickup, provider, starts, ends)
    plan.solve_ms = solve_ms
    plan.status = "OK"
    plan.warm_started = warm_started
    plan.weights = weights
    plan.onboard = onboard
    plan.pickup = pickup
    plan.pins = dict(pins)
    plan.candidates = candidates
    plan.kpi = compute_kpi(plan, jobs)
    return plan


def _prune_late_visits(route: list[int], v: int, e: Engineer, jobs: list[Job],
                       matrix: list[list[int]], start_node: int,
                       service_at) -> list[int]:
    """Выбросить из прошлого маршрута визиты, которые больше не успеть.

    Проверка та же, что у солвера, — дорога, окна, смена, — но в Python за
    микросекунды, а не через `ReadAssignmentFromRoutes` по 176 мс на чтение.
    Обед здесь не учитывается: если из-за него солвер маршрут всё же отвергнет,
    доработает `_repair_warm_start`, но таких случаев остаются единицы.
    """
    kept: list[int] = []
    prev, t = start_node, e.starts_at + service_at(start_node, v)
    for node in route:
        job = jobs[node]
        arrival = t + matrix[prev][node]
        start = max(arrival, job.tw_start)
        if job.tw_hard and start > job.tw_end:
            continue                        # окно уже не поймать — отдаём поиску
        end = start + service_at(node, v)
        if end > e.shift_end + e.max_overtime_min:
            break                           # дальше только хуже: хвост долой
        kept.append(node)
        prev, t = node, end
    return kept


def _repair_warm_start(routing, warm: list[list[int]], verbose: bool = False):
    """Принять прошлое решение, подрезав только те маршруты, что стали недопустимы.

    Прошлое решение могло стать чуть недопустимым: время ушло вперёд, инженер
    спроецирован на середину перегона, и дорога до следующего визита выросла
    на пару минут — а окно закрывалось впритык. `ReadAssignmentFromRoutes`
    бракует всё решение целиком, если недопустим хоть один маршрут, и раньше
    мы подрезали хвосты у ВСЕХ маршрутов разом: три попытки — и холодный старт,
    на слотах заказчика так шло три пересчёта из пяти.

    Чтение решения на модели с обедами стоит десятки миллисекунд, поэтому
    проб должно быть мало: одна на всё решение целиком; если не принято —
    по одной на маршрут, чтобы найти виноватых; и только им — варианты с
    отрезанным хвостом или головой. Выброшенные заявки поиск вернёт сам, зато
    остальной день не пересобирается с нуля.
    """
    if not any(warm):
        return None
    t0 = time.perf_counter()
    whole = routing.ReadAssignmentFromRoutes(warm, True)
    if verbose:
        print(f"    [warm] чтение целиком: {'принято' if whole else 'отвергнуто'}, "
              f"{(time.perf_counter() - t0) * 1000:.0f} мс, "
              f"визитов {sum(len(r) for r in warm)}")
    if whole:
        return whole

    def alone(v: int, route: list[int]):
        trial = [[] for _ in warm]
        trial[v] = route
        return routing.ReadAssignmentFromRoutes(trial, True)

    def variants(route: list[int]):
        if len(route) > 1:
            yield route[:-1]
            yield route[1:]
        if len(route) > 2:
            yield route[:-2]
            yield route[1:-1]
        yield []

    # Чтение обычно стоит миллисекунды, но на модели с обедами может упереться
    # в лимит времени поиска. Поэтому ремонт ограничен и числом проб, и
    # стенными часами: что не успели проверить — отдаём поиску.
    budget = READ_BUDGET
    deadline = time.perf_counter() + REPAIR_SECONDS

    def within() -> bool:
        return budget > 0 and time.perf_counter() < deadline

    repaired: list[list[int]] = []
    for v, route in enumerate(warm):
        if not route:
            repaired.append(route)
            continue
        if not within():
            repaired.append([])
            continue
        budget -= 1
        t1 = time.perf_counter()
        ok = alone(v, route)
        if verbose:
            print(f"    [warm] маршрут {v}: {len(route)} визитов, "
                  f"{'ок' if ok else 'нарушает'}, {(time.perf_counter() - t1) * 1000:.0f} мс")
        if ok:
            repaired.append(route)
            continue
        chosen: list[int] = []
        for attempt in variants(route):
            if not attempt or not within():
                break
            budget -= 1
            if alone(v, attempt):
                chosen = attempt
                break
        repaired.append(chosen)
    if not any(repaired):
        return None
    return routing.ReadAssignmentFromRoutes(repaired, True) or None


# --------------------------------------------------------------------------
# Разбор решения
# --------------------------------------------------------------------------

def _extract(ds, jobs, engineers, manager, routing, solution, time_dim,
             matrices, points, pickup, provider, starts, ends) -> Plan:
    n_jobs = len(jobs)
    routes: list[Route] = []
    visited: set[str] = set()

    for v, e in enumerate(engineers):
        matrix = matrices[e.vehicle_type]
        wh_id = pickup[e.id]
        wh = ds.warehouses.get(wh_id) if wh_id else None

        route = Route(
            engineer_id=e.id, engineer_name=e.name,
            vehicle_type=e.vehicle_type.value, pickup_warehouse=wh_id,
        )

        index = routing.Start(v)
        prev_node = manager.IndexToNode(index)
        start_time = solution.Min(time_dim.CumulVar(index))
        route.start_min = start_time
        if e.current_position:
            start_label = "Текущая позиция инженера"
        elif wh:
            start_label = f"Склад: {wh.name}"
        else:
            start_label = f"Выезд из дома — {e.home_address}"
        route.stops.append(Stop(
            kind="start",
            job_id=None,
            label=start_label,
            lat=points[prev_node][0], lon=points[prev_node][1],
            arrival=start_time, service_start=start_time,
            service_end=start_time + (PICKUP_MIN if wh else 0),
            wait_min=0, travel_min_from_prev=0, travel_km_from_prev=0.0,
        ))

        index = solution.Value(routing.NextVar(index))
        prev_departure = route.stops[0].service_end

        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            job = jobs[node]
            travel = matrix[prev_node][node]
            km = provider.distance_km(points[prev_node], points[node],
                                      e.vehicle_type)
            arrival = prev_departure + travel
            svc_start = solution.Min(time_dim.CumulVar(index))
            svc_len = service_minutes(job, e)
            late = max(0, svc_start - job.sla_deadline)

            route.stops.append(Stop(
                kind="job", job_id=job.id,
                label=f"{job.customer} — {ds.work_types[job.work_type_id].name}",
                lat=job.lat, lon=job.lon,
                arrival=arrival, service_start=svc_start,
                service_end=svc_start + svc_len,
                wait_min=max(0, svc_start - arrival),
                travel_min_from_prev=travel, travel_km_from_prev=round(km, 2),
                sla_deadline=job.sla_deadline, sla_late_min=late,
                window=(job.tw_start, job.tw_end),
            ))
            route.travel_min += travel
            route.travel_km += km
            route.work_min += svc_len
            route.wait_min += max(0, svc_start - arrival)
            visited.add(job.id)

            prev_node = node
            prev_departure = svc_start + svc_len
            index = solution.Value(routing.NextVar(index))

        end_node = manager.IndexToNode(index)
        end_time = solution.Min(time_dim.CumulVar(index))
        if ds.return_to_start:
            travel = matrix[prev_node][end_node]
            km = provider.distance_km(points[prev_node], points[end_node],
                                      e.vehicle_type)
            end_point, end_label = points[end_node], f"Возврат домой — {e.home_address}"
        else:
            # Маршрут заканчивается на последней заявке (ТЗ, п. 2.4): обратный
            # путь не планируется и в пробег не входит.
            travel, km = 0, 0.0
            end_point, end_label = points[prev_node], "Конец маршрута"
        route.travel_min += travel
        route.travel_km += km
        route.end_min = end_time
        route.stops.append(Stop(
            kind="end", job_id=None, label=end_label,
            lat=end_point[0], lon=end_point[1],
            arrival=end_time, service_start=end_time, service_end=end_time,
            wait_min=0, travel_min_from_prev=travel, travel_km_from_prev=round(km, 2),
        ))
        route.travel_km = round(route.travel_km, 2)

        # Солвер размещает обед сам, наружу это не отдаётся. Находим его как
        # первый разрыв, в который обед укладывается и который попадает в его
        # окно, и вычитаем из простоя — иначе законный перерыв выглядит как
        # безделье.
        if e.break_min and route.job_count:
            # Обед гарантирован ограничением модели, поэтому засчитываем его
            # всегда. Локализовать удаётся не всегда: солвер вправе разместить
            # перерыв внутри перегона, и тогда в расписании визитов его не видно.
            route.lunch_min = e.break_min
            for s in route.stops:
                if s.kind != "job":
                    continue
                gap = s.service_start - s.arrival
                if gap >= e.break_min and e.break_from <= s.arrival <= e.break_to:
                    route.lunch_start = s.arrival
                    s.wait_min = gap - e.break_min
                    route.wait_min -= e.break_min
                    break

        # Инженеру, которому не досталось ни одной заявки, склад не нужен: он
        # просто не выходит в смену. Заезд назначается утром, до расчёта, и без
        # этой проверки в плане остаётся холостой рейс «склад — дом» — четыре
        # таких рейса добавляли к KPI службы 115 минут пути, которых в
        # реальности никто не проедет. Остановки не убираем совсем: и
        # перепланирование, и карта рассчитывают, что у маршрута есть начало.
        if not route.job_count:
            route.pickup_warehouse = None
            route.travel_min = route.work_min = route.wait_min = 0
            route.travel_km = 0.0
            route.start_min = route.end_min = e.starts_at
            route.stops = [
                Stop(kind=kind, job_id=None,
                     label=f"Смена не начата — {e.home_address}",
                     lat=e.home_lat, lon=e.home_lon,
                     arrival=e.starts_at, service_start=e.starts_at,
                     service_end=e.starts_at, wait_min=0,
                     travel_min_from_prev=0, travel_km_from_prev=0.0)
                for kind in ("start", "end")
            ]
        routes.append(route)

    unassigned = [j.id for j in jobs if j.id not in visited]
    return Plan(ds.date, routes, unassigned, {}, 0, "OK", Weights())


# --------------------------------------------------------------------------
# KPI
# --------------------------------------------------------------------------

def attach_geometry(plan: Plan, ds: Dataset, provider: TravelTimeProvider) -> None:
    """Проложить каждый перегон по дорогам и записать ломаную в остановки.

    Вызывается отдельно от расчёта: карте геометрия нужна, солверу — нет.
    Если провайдер её не умеет (геодезическое приближение), остановки остаются
    без ломаных, и фронтенд рисует прямые — как раньше.
    """
    if not hasattr(provider, "path"):
        return
    for route in plan.routes:
        if not route.job_count:
            continue
        mode = ds.engineer(route.engineer_id).vehicle_type
        for prev, stop in zip(route.stops, route.stops[1:]):
            try:
                stop.geometry = provider.path((prev.lat, prev.lon),
                                              (stop.lat, stop.lon), mode)
                if mode is TransportMode.TRANSIT and hasattr(provider, "uses_metro"):
                    stop.via_metro = provider.uses_metro((prev.lat, prev.lon),
                                                         (stop.lat, stop.lon))
            except Exception:
                stop.geometry = []      # маршрут не проложился — не повод падать


def compute_kpi(plan: Plan, jobs: list[Job]) -> dict:
    by_id = {j.id: j for j in jobs}
    assigned = [s for r in plan.routes for s in r.stops if s.kind == "job"]
    late = [s for s in assigned if s.sla_late_min > 0]

    # Загрузку считаем по тем, у кого есть работа. Незанятые инженеры дают нули,
    # а нуль в этой статистике врёт: он превращает разброс в «от нуля до
    # максимума» и занижает среднюю занятость службы, хотя сколько инженеров
    # осталось без заявок, видно отдельной цифрой `engineers_used`.
    busy = [r.busy_min for r in plan.routes if r.job_count]
    mean_busy = sum(busy) / len(busy) if busy else 0
    spread = (max(busy) - min(busy)) if busy else 0
    variance = (sum((b - mean_busy) ** 2 for b in busy) / len(busy)) if busy else 0

    dropped_by_priority = Counter(by_id[jid].priority.value for jid in plan.unassigned)

    return {
        "jobs_total": len(jobs),
        "jobs_assigned": len(assigned),
        "jobs_unassigned": len(plan.unassigned),
        "assign_rate": round(len(assigned) / len(jobs) * 100, 1) if jobs else 0.0,
        "dropped_by_priority": dict(dropped_by_priority),
        "travel_min": sum(r.travel_min for r in plan.routes),
        "travel_km": round(sum(r.travel_km for r in plan.routes), 1),
        "work_min": sum(r.work_min for r in plan.routes),
        "wait_min": sum(r.wait_min for r in plan.routes),
        "lunch_min": sum(r.lunch_min for r in plan.routes),
        "sla_violations": len(late),
        "sla_late_min": sum(s.sla_late_min for s in late),
        "engineers_used": sum(1 for r in plan.routes if r.job_count),
        "busy_mean_min": round(mean_busy),
        "busy_spread_min": spread,
        "busy_stdev_min": round(variance ** 0.5),
        "travel_share": (round(sum(r.travel_min for r in plan.routes)
                               / max(1, sum(busy)) * 100, 1)),
    }


def format_plan(plan: Plan, ds: Dataset, limit: int | None = None) -> str:
    """Текстовый вывод плана — для CLI и для быстрой проверки без UI."""
    lines = [f"План на {plan.date}   статус={plan.status}   "
             f"расчёт {plan.solve_ms} мс"]
    k = plan.kpi
    lines.append(
        f"  назначено {k['jobs_assigned']}/{k['jobs_total']} ({k['assign_rate']} %)   "
        f"в пути {k['travel_min']} мин / {k['travel_km']} км   "
        f"простой {k['wait_min']} мин   обед {k.get('lunch_min', 0)} мин")
    lines.append(
        f"  нарушений SLA: {k['sla_violations']} (суммарно {k['sla_late_min']} мин)   "
        f"занято инженеров: {k['engineers_used']}   "
        f"разброс загрузки: {k['busy_spread_min']} мин (σ={k['busy_stdev_min']})")
    if plan.unassigned:
        lines.append(f"  не назначено: {', '.join(plan.unassigned)}")

    shown = plan.routes if limit is None else plan.routes[:limit]
    for r in shown:
        if not r.job_count:
            lines.append(f"\n  {r.engineer_id} {r.engineer_name} — без заявок")
            continue
        lines.append(
            f"\n  {r.engineer_id} {r.engineer_name} [{r.vehicle_type}]"
            f"{' склад ' + r.pickup_warehouse if r.pickup_warehouse else ''}"
            f"  {min_to_hhmm(r.start_min)}–{min_to_hhmm(r.end_min)}"
            f"  заявок {r.job_count}, в пути {r.travel_min} мин / {r.travel_km} км")
        for s in r.stops:
            if s.kind != "job":
                continue
            job = ds.job(s.job_id)
            mark = "!" if s.sla_late_min else " "
            wait = f" ждал {s.wait_min}" if s.wait_min else ""
            lines.append(
                f"    {mark} {min_to_hhmm(s.service_start)}-{min_to_hhmm(s.service_end)}"
                f"  {s.job_id} {job.priority.value} "
                f"[{min_to_hhmm(job.tw_start)}-{min_to_hhmm(job.tw_end)}"
                f"{'!' if job.tw_hard else ''}]"
                f"  +{s.travel_min_from_prev} мин пути{wait}  {job.district}")
    return "\n".join(lines)
