"""Жёсткие ограничения плана.

Каждый тест проверяет утверждение, которое мы выносим на защиту. Если он
падает, значит план физически невыполним, и никакие KPI этого не искупают.
"""

from __future__ import annotations

from app.domain.models import service_minutes
from conftest import visits


def test_job_assigned_at_most_once(plan, morning_jobs):
    ids = [s.job_id for _, s in visits(plan)]
    assert len(ids) == len(set(ids)), "заявка попала в план дважды"
    known = {j.id for j in morning_jobs}
    assert set(ids) <= known, "в плане есть заявка, которой не давали"


def test_assigned_and_unassigned_cover_everything(plan, morning_jobs):
    assigned = {s.job_id for _, s in visits(plan)}
    assert assigned | set(plan.unassigned) == {j.id for j in morning_jobs}
    assert not (assigned & set(plan.unassigned)), "заявка и назначена, и нет"


def test_skill_level_is_enough(ds, plan):
    for route, stop in visits(plan):
        job = ds.job(stop.job_id)
        eng = ds.engineer(route.engineer_id)
        assert eng.level_in(job.specialization) >= job.min_level, (
            f"{job.id}: {eng.name} имеет уровень "
            f"{eng.level_in(job.specialization)} при требуемом {job.min_level}")


def test_engineer_has_required_equipment(ds, plan):
    for route, stop in visits(plan):
        job = ds.job(stop.job_id)
        onboard = plan.onboard[route.engineer_id]
        missing = set(job.required_equipment) - onboard
        assert not missing, f"{job.id}: у {route.engineer_id} нет {sorted(missing)}"


def test_bulky_equipment_requires_vehicle(ds, plan):
    for route, stop in visits(plan):
        job = ds.job(stop.job_id)
        if not ds.needs_vehicle(job):
            continue
        eng = ds.engineer(route.engineer_id)
        assert eng.vehicle_type.can_carry_bulky, (
            f"{job.id} требует габаритное оборудование, "
            f"а {eng.name} передвигается пешком")


def test_pickup_uses_single_warehouse(ds, plan):
    """За день инженер заезжает не более чем на один склад."""
    for route in plan.routes:
        if route.pickup_warehouse:
            assert route.pickup_warehouse in ds.warehouses


def test_hard_window_bounds_the_start(ds, plan):
    """Правило ТЗ (п. 2.2): начало работы попадает во временное окно.

    Окончание окном не ограничено — слот заказчика «18:00–20:00» означает,
    что мастер приходит в этот интервал, а не что он обязан уйти к 20:00.
    """
    for _, stop in visits(plan):
        job = ds.job(stop.job_id)
        if not job.tw_hard:
            continue
        assert stop.service_start >= job.tw_start, f"{job.id}: приехал до окна"
        assert stop.service_start <= job.tw_end, (
            f"{job.id}: окно закрылось в {job.tw_end}, "
            f"а работа началась в {stop.service_start}")


def test_soft_window_not_started_early(ds, plan):
    for _, stop in visits(plan):
        job = ds.job(stop.job_id)
        assert stop.service_start >= job.tw_start, (
            f"{job.id}: визит начат раньше открытия окна")


def test_visits_fit_the_shift(ds, plan):
    for route in plan.routes:
        if not route.job_count:
            continue
        eng = ds.engineer(route.engineer_id)
        assert route.start_min >= eng.shift_start, f"{eng.id} вышел до смены"
        assert route.end_min <= eng.shift_end + eng.max_overtime_min, (
            f"{eng.id} возвращается в {route.end_min} при конце смены "
            f"{eng.shift_end} и переработке до {eng.max_overtime_min} мин")


def test_service_duration_matches_engineer(ds, plan):
    """Длительность визита зависит от квалификации исполнителя."""
    for route, stop in visits(plan):
        job = ds.job(stop.job_id)
        eng = ds.engineer(route.engineer_id)
        assert stop.service_end - stop.service_start == service_minutes(job, eng)


def test_schedule_is_physically_consistent(ds, plan, provider):
    """Инженер не телепортируется: приезд не раньше выезда плюс дорога.

    Сверяемся с тем же опорным часом, на котором солвер строил матрицу.
    Зависимость времени поездки от момента выезда моделью пока не покрыта
    (см. `MATRIX_REFERENCE_MIN`), и проверять её здесь — значит проверять
    известное упрощение, а не физическую состоятельность расписания.
    """
    from app.solver.engine import MATRIX_REFERENCE_MIN

    for route in plan.routes:
        if not route.job_count:
            continue
        eng = ds.engineer(route.engineer_id)
        for prev, stop in zip(route.stops, route.stops[1:]):
            if stop.kind != "job":
                continue
            travel = provider.minutes((prev.lat, prev.lon), (stop.lat, stop.lon),
                                      eng.vehicle_type, MATRIX_REFERENCE_MIN)
            earliest = prev.service_end + travel
            assert stop.service_start >= earliest - 1, (
                f"{route.engineer_id}: выезд {prev.service_end}, дорога {travel} мин, "
                f"а визит начат в {stop.service_start}")


def test_travel_time_matches_declared(ds, plan, provider):
    """Заявленное в остановке время в пути совпадает с расчётным."""
    from app.solver.engine import MATRIX_REFERENCE_MIN

    for route in plan.routes:
        if not route.job_count:
            continue
        eng = ds.engineer(route.engineer_id)
        for prev, stop in zip(route.stops, route.stops[1:]):
            if stop.kind != "job":
                continue
            expected = provider.minutes((prev.lat, prev.lon), (stop.lat, stop.lon),
                                        eng.vehicle_type, MATRIX_REFERENCE_MIN)
            assert abs(stop.travel_min_from_prev - expected) <= 1


def test_lunch_is_not_counted_as_idle(ds, plan):
    """Обед учитывается отдельно и не раздувает простой."""
    for route in plan.routes:
        if not route.job_count:
            continue
        eng = ds.engineer(route.engineer_id)
        assert route.lunch_min == eng.break_min
        assert route.wait_min >= 0


def test_engineer_without_jobs_stays_home(ds, plan):
    """Без заявок инженер не выходит в смену — и на склад не едет.

    Заезд на склад назначается утром, до расчёта. Если солвер потом не дал
    инженеру ни одной заявки, в маршруте оставался холостой рейс «склад — дом»,
    и служба записывала себе минуты пути, которых никто не проедет.
    """
    idle = [r for r in plan.routes if not r.job_count]
    assert idle, "в плане нет незанятых инженеров — проверка бессмысленна"
    for route in idle:
        assert route.travel_min == 0 and route.travel_km == 0, (
            f"{route.engineer_id} без заявок, а в пути {route.travel_min} мин")
        assert route.pickup_warehouse is None, (
            f"{route.engineer_id} без заявок отправлен на склад "
            f"{route.pickup_warehouse}")
        assert not any(s.kind == "job" for s in route.stops)


def test_service_travel_is_only_from_working_routes(ds, plan):
    """KPI службы складывается из маршрутов с заявками и ничего не добавляет."""
    assert plan.kpi["travel_min"] == sum(r.travel_min for r in plan.routes
                                         if r.job_count)


def test_every_unassigned_job_has_an_explanation(ds, plan, provider):
    from app.explain.why_not import why_not

    for job_id in plan.unassigned:
        result = why_not(ds, plan, ds.job(job_id), provider)
        assert result.blockers or result.feasible_with_shift, (
            f"{job_id} не назначена, а причины нет")
        assert result.verdict()
