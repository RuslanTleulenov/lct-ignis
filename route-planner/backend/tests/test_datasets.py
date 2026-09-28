"""Наборы данных: выгрузка заказчика, переключение, загрузка CSV/JSON,
базовый вариант по ТЗ и сравнение с фактом.

Маршрутизация — геодезическое приближение, как и в остальных тестах.
"""

from __future__ import annotations

import json

import pytest

from app.api.datasets import BUILTIN
from app.api.service import PlanningService
from app.baseline.control import control_plan
from app.baseline.greedy import tz_baseline
from app.domain.importer import ImportError_, read_table, snapshot_from_tables
from app.domain.loader import load_dataset
from app.domain.models import Priority, TransportMode
from app.solver.engine import Weights, solve

VOSTOK = BUILTIN["beeline-vostok"].path

pytestmark = pytest.mark.skipif(
    not VOSTOK.exists(), reason="выгрузка заказчика не собрана: py -3.11 data/import_beeline.py")


@pytest.fixture(scope="module")
def vostok():
    return load_dataset(VOSTOK)


@pytest.fixture(scope="module")
def vostok_plan(vostok, provider):
    return solve(vostok, provider=provider, time_limit_s=6)


# ---------------------------------------------------------------- выгрузка

def test_customer_dataset_matches_tz_dictionaries(vostok):
    """Справочники ТЗ, п. 2.4.1: три навыка, транспорт из четырёх, два приоритета."""
    assert set(vostok.specializations.values()) == {
        "Локальные работы", "Работы на подключение и дозаказы", "Аварийные работы"}
    assert all(1 <= len(e.skills) <= 3 for e in vostok.engineers)
    assert all(j.priority in (Priority.NORMAL, Priority.URGENT) for j in vostok.jobs)
    assert all(e.vehicle_type in TransportMode for e in vostok.engineers)
    assert not vostok.uses_levels and not vostok.uses_equipment
    assert not vostok.return_to_start


def test_customer_dataset_is_within_tz_size(vostok):
    """ТЗ, п. 6: 10–15 инженеров и не более 100 заявок."""
    assert 10 <= len(vostok.engineers) <= 15
    assert len(vostok.jobs) <= 100


def test_every_job_is_geocoded(vostok):
    for j in vostok.jobs:
        assert 54.5 < j.lat < 56.5 and 36.5 < j.lon < 39.0, f"{j.id}: {j.lat}, {j.lon}"
        assert j.geo_precision in ("house", "house~", "street", "district")
    exact = sum(1 for j in vostok.jobs if j.geo_precision == "house")
    assert exact / len(vostok.jobs) > 0.8, "слишком много приблизительных адресов"


def test_control_assignment_is_attached(vostok):
    """К каждой заявке привязано, кому её отдал реальный диспетчер."""
    with_control = [j for j in vostok.jobs if j.control_engineer]
    assert len(with_control) >= len(vostok.jobs) - 3
    known = {e.id for e in vostok.engineers}
    assert all(j.control_engineer in known for j in with_control)


# ---------------------------------------------------------------- план и сравнение

def test_open_routes_have_no_return_leg(vostok, vostok_plan):
    """ТЗ, п. 2.4: возврат в стартовую точку не планируется и в пробег не входит."""
    for r in vostok_plan.routes:
        if not r.job_count:
            continue
        end = r.stops[-1]
        assert end.kind == "end" and end.travel_min_from_prev == 0
        last_job = [s for s in r.stops if s.kind == "job"][-1]
        assert (end.lat, end.lon) == (last_job.lat, last_job.lon)


def test_tz_baseline_is_first_fit_in_input_order(vostok, provider):
    """Базовый вариант ТЗ: первому по порядку подходящему, визиты в порядке назначения."""
    base = tz_baseline(vostok, provider=provider)
    assert base.status == "BASELINE_TZ"
    for r in base.routes:
        starts = [s.service_start for s in r.stops if s.kind == "job"]
        assert starts == sorted(starts), "порядок визитов не совпадает с порядком назначения"
    assigned = {s.job_id for r in base.routes for s in r.stops if s.kind == "job"}
    morning = {j.id for j in vostok.jobs if j.known_at_day_start}
    assert assigned | set(base.unassigned) == morning


def test_optimizer_uses_no_more_staff_than_tz_baseline(vostok, vostok_plan, provider):
    """Обязательная метрика ТЗ — наименьшее количество персонала."""
    base = tz_baseline(vostok, provider=provider)
    assert vostok_plan.kpi["engineers_used"] <= base.kpi["engineers_used"]
    assert vostok_plan.kpi["jobs_assigned"] >= base.kpi["jobs_assigned"]


def test_control_plan_reproduces_dispatcher(vostok, provider):
    fact = control_plan(vostok, provider=provider)
    assert fact is not None and fact.status == "CONTROL"
    by_plan = {s.job_id: r.engineer_id for r in fact.routes for s in r.stops if s.kind == "job"}
    for j in vostok.jobs:
        if j.control_engineer:
            assert by_plan[j.id] == j.control_engineer
    assert fact.kpi["control_overdue"] == 4          # столько просрочено в выгрузке


def test_staff_weight_reduces_engineers(vostok, provider):
    """Плата за выход инженера действительно сокращает штат в плане."""
    free = solve(vostok, provider=provider, time_limit_s=6, weights=Weights(staff=0))
    tight = solve(vostok, provider=provider, time_limit_s=6, weights=Weights(staff=300))
    assert tight.kpi["engineers_used"] <= free.kpi["engineers_used"]


# ---------------------------------------------------------------- сервис: наборы

def test_service_switches_datasets(provider):
    svc = PlanningService(VOSTOK)
    svc.provider = provider
    assert svc.dataset_key == "beeline-vostok"
    if BUILTIN["synthetic"].path.exists():
        info = svc.switch_dataset("synthetic")
        assert info.key == "synthetic" and svc.ds.uses_equipment
        assert svc.plan is None, "после смены набора старый план должен быть сброшен"
    with pytest.raises(KeyError):
        svc.switch_dataset("нет-такого")


JOBS_CSV = """id;lat;lon;длительность;начало;окончание;приоритет;навык;транспорт
A-1;55.75;37.62;45;10:00;12:00;обычная;Локальные работы;
A-2;55.76;37.60;60;12:00;14:00;срочная;Аварийные работы;Автомобиль
A-3;55.74;37.64;30;14:00;16:00;обычная;Работы на подключение и дозаказы;
"""
ENGINEERS_CSV = """id,name,lat,lon,shift_start,shift_end,skills,transport
E-1,Иванов,55.75,37.60,09:00,18:00,Локальные работы|Аварийные работы,Автомобиль
E-2,Петров,55.76,37.63,09:00,18:00,Работы на подключение и дозаказы,Пешеход
"""


def test_tables_import_by_tz_minimal_fields():
    snap = snapshot_from_tables(read_table(JOBS_CSV), read_table(ENGINEERS_CSV),
                                title="Проверочный набор")
    assert [j["id"] for j in snap["jobs"]] == ["A-1", "A-2", "A-3"]
    assert snap["jobs"][1]["priority"] == "urgent"
    assert snap["jobs"][1]["required_transport"] == "car"
    assert snap["jobs"][2]["specialization"] == "connect"
    assert snap["engineers"][0]["skills"] == {"local": 1, "emergency": 1}
    assert snap["engineers"][1]["vehicle_type"] == "foot"


def test_tables_import_reports_every_problem():
    bad = JOBS_CSV.replace("Локальные работы", "Сварка").replace("14:00;16:00", "16:00;14:00")
    with pytest.raises(ImportError_) as exc:
        snapshot_from_tables(read_table(bad), read_table(ENGINEERS_CSV))
    text = " ".join(exc.value.problems)
    assert "Сварка" in text and "A-3" in text


def test_service_upload_csv_pair(tmp_path, provider):
    svc = PlanningService(VOSTOK)
    svc.provider = provider
    svc.registry.upload_dir = tmp_path            # не мусорить в data/uploads
    info = svc.upload_dataset({"jobs.csv": JOBS_CSV.encode("utf-8"),
                               "engineers.csv": ENGINEERS_CSV.encode("utf-8")},
                              name="Тестовая загрузка")
    assert info.kind == "uploaded" and svc.dataset_key == info.key
    assert len(svc.ds.jobs) == 3 and len(svc.ds.engineers) == 2
    plan = svc.build(time_limit_s=3)
    assert plan.kpi["jobs_assigned"] == 3
    # загруженное лежит на диске и читается заново
    assert json.loads(info.path.read_text(encoding="utf-8"))["meta"]["title"] == "Тестовая загрузка"


# ---------------------------------------------------------------- события по требованию

def test_dispatcher_events_replan_immediately(provider):
    """ТЗ, п. 2.1 (6): отмена заявки и недоступность инженера — с пересчётом."""
    svc = PlanningService(VOSTOK)
    svc.provider = provider
    plan = svc.build(time_limit_s=4)
    busy_route = max((r for r in plan.routes if r.job_count), key=lambda r: r.job_count)
    victim = busy_route.stops[1].job_id

    event, diff = svc.add_event("job_cancelled", victim)
    assert event.type == "job_cancelled" and diff is not None
    assert all(s.job_id != victim for r in svc.plan.routes for s in r.stops), \
        "отменённая заявка осталась в плане"
    with pytest.raises(ValueError):
        svc.add_event("job_cancelled", victim)            # дважды отменить нельзя

    event, diff = svc.add_event("engineer_unavailable", busy_route.engineer_id)
    # выбывший из плана исчезает вовсе — маршрута у него нет
    gone = [r for r in svc.plan.routes if r.engineer_id == busy_route.engineer_id]
    assert not gone or gone[0].job_count == 0, "выбывший инженер всё ещё в маршрутах"
    assert diff.affected, "заявки выбывшего никому не достались"
    with pytest.raises(ValueError):
        svc.add_event("engineer_unavailable", "ENG-99")


# ---------------------------------------------------------------- объяснение маршрута и diff порядка

def test_route_explanation_matches_the_route(vostok, vostok_plan):
    """ТЗ, п. 2.4.2: для выбранного маршрута — краткое объяснение факторов."""
    from app.explain.route import explain_route

    busy = max(vostok_plan.routes, key=lambda r: r.job_count)
    exp = explain_route(vostok, vostok_plan, busy.engineer_id)
    assert exp is not None and exp.constraints and exp.choices
    assert str(busy.job_count) in exp.headline
    assert f"{busy.travel_km:.1f}" in " ".join(exp.choices)
    text = " ".join(exp.constraints)
    assert "Навык" in text and "Транспорт" in text and "Смена" in text
    idle = next((r for r in vostok_plan.routes if not r.job_count), None)
    if idle is not None:
        assert "Заявок не досталось" in explain_route(vostok, vostok_plan, idle.engineer_id).headline
    assert explain_route(vostok, vostok_plan, "ENG-99") is None


def test_diff_reports_reordering_and_time_shifts(vostok, vostok_plan):
    """ТЗ, п. 2.4.2: после перепланирования видно, что порядок заявок изменился."""
    import copy
    from app.solver.replan import diff_plans

    prev = copy.deepcopy(vostok_plan)
    new = copy.deepcopy(vostok_plan)
    route = max(new.routes, key=lambda r: r.job_count)
    jobs = [s for s in route.stops if s.kind == "job"]
    assert len(jobs) >= 3
    # меняем местами первые два визита и сдвигаем третий на полчаса
    i, j = route.stops.index(jobs[0]), route.stops.index(jobs[1])
    route.stops[i], route.stops[j] = route.stops[j], route.stops[i]
    jobs[2].service_start += 30
    assignment = {s.job_id: r.engineer_id for r in prev.routes for s in r.stops if s.kind == "job"}
    d = diff_plans(assignment, new, {}, [r.engineer_id for r in prev.routes], prev_plan=prev)
    assert not d.moved and not d.added and not d.removed
    assert {x[0] for x in d.reordered} == {jobs[0].job_id, jobs[1].job_id}
    assert [x[0] for x in d.shifted] == [jobs[2].job_id]
    assert d.affected == [route.engineer_id]
    assert "переставлено 2" in d.summary() and "сдвинуто по времени 1" in d.summary()
