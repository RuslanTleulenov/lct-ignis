"""Перепланирование: что нельзя ломать при пересчёте дня."""

from __future__ import annotations

import pytest

from app.solver.engine import Weights, solve
from app.solver.replan import project, replan, state_at
from conftest import visits


@pytest.fixture(scope="module")
def replanned(ds, plan, provider):
    """Пересчёт в 10:30 — день уже наполовину прожит."""
    st = state_at(ds.events, 10 * 60 + 30, {})
    new_plan, diff = replan(ds, plan, st, weights=Weights(stability=200),
                            provider=provider, time_limit_s=4)
    return st, new_plan, diff


def test_started_visits_are_locked(ds, plan, replanned):
    """Начатый визит не возвращается в планирование."""
    st, new_plan, _ = replanned
    proj = project(ds, plan, st)
    replanned_ids = {s.job_id for _, s in visits(new_plan)}
    assert proj.locked, "к 10:30 никто ничего не начал — проверка бессмысленна"
    assert not (replanned_ids & set(proj.locked)), (
        "выполненная заявка снова попала в план")


def test_completed_accumulates_and_never_shrinks(ds, plan, provider):
    """Выполненное копится между шагами, а не выводится заново.

    Каждый пересчёт переносит невыполненное вперёд от «сейчас», поэтому визит,
    начатый в 09:00, при пересчёте в 09:05 снова оказался бы в будущем — и день
    не проживался бы вообще.
    """
    completed: dict[str, str] = {}
    current = plan
    sizes = []
    for now in (9 * 60, 10 * 60, 11 * 60, 13 * 60):
        st = state_at(ds.events, now, completed)
        current, _ = replan(ds, current, st, weights=Weights(stability=200),
                            provider=provider, time_limit_s=2)
        sizes.append(len(completed))
    assert sizes == sorted(sizes), f"число выполненных заявок убывало: {sizes}"
    assert sizes[-1] > 0, "за полдня не выполнено ни одной заявки"


def test_unavailable_engineer_gets_no_work(ds, plan, provider):
    victim = ds.engineers[0].id
    st = state_at([], 8 * 60 + 5, {})
    st.unavailable.add(victim)
    new_plan, _ = replan(ds, plan, st, weights=Weights(stability=200),
                         provider=provider, time_limit_s=4)
    assert all(r.engineer_id != victim for r in new_plan.routes if r.job_count), (
        "выбывший инженер продолжает получать заявки")


def test_engineer_without_vehicle_drops_bulky_jobs(ds, plan, provider):
    """Сломалось авто — габаритные заявки уходят другим."""
    with_car = next(e for e in ds.engineers if e.vehicle_type.can_carry_bulky)
    st = state_at([], 8 * 60 + 5, {})
    st.lost_vehicle.add(with_car.id)
    new_plan, _ = replan(ds, plan, st, weights=Weights(stability=200),
                         provider=provider, time_limit_s=4)
    for route, stop in visits(new_plan):
        if route.engineer_id != with_car.id:
            continue
        assert not ds.needs_vehicle(ds.job(stop.job_id)), (
            f"{stop.job_id} требует авто, а {with_car.name} остался без машины")


def test_stability_reduces_churn(ds, plan, provider):
    """Штраф за нестабильность действительно уменьшает перетасовку."""
    st_free = state_at(ds.events, 9 * 60, {})
    _, diff_free = replan(ds, plan, st_free, weights=Weights(stability=0),
                          provider=provider, time_limit_s=4)
    st_firm = state_at(ds.events, 9 * 60, {})
    _, diff_firm = replan(ds, plan, st_firm, weights=Weights(stability=600),
                          provider=provider, time_limit_s=4)
    assert len(diff_firm.moved) <= len(diff_free.moved), (
        f"со штрафом 600 перенесено {len(diff_firm.moved)}, "
        f"без штрафа — {len(diff_free.moved)}")


def test_pin_is_honoured(ds, morning_jobs, provider, plan):
    """Закреплённая заявка достаётся именно тому, за кем закреплена."""
    job_id, candidates = next(
        (j, c) for j, c in plan.candidates.items() if len(c) > 1)
    assigned = {s.job_id: r.engineer_id for r, s in visits(plan)}
    target = next(c for c in candidates if c != assigned.get(job_id))

    pinned = solve(ds, morning_jobs, provider=provider, time_limit_s=6,
                   onboard=plan.onboard, pickup=plan.pickup,
                   pins={job_id: target})
    got = {s.job_id: r.engineer_id for r, s in visits(pinned)}
    assert got.get(job_id) == target, (
        f"{job_id} закреплена за {target}, а ушла к {got.get(job_id)}")
