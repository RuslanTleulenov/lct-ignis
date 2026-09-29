"""Симуляция рабочего дня: утренний план + перепланирование по событиям.

Это сценарий защиты в текстовом виде. Показывает главное: система не
перекраивает день целиком из-за одной заявки — трогает только затронутых.

    python replan_cli.py
    python replan_cli.py --time-limit 5 --stability 80
    python replan_cli.py --routes 3          # печатать маршруты после каждого шага
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

if sys.platform == "win32":
    # Вывод использует символы вне кодовой страницы консоли (⚠, →, ↳, ✗): на
    # cp1251 падает с UnicodeEncodeError. UTF-8 работает при любой локали.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.domain.loader import load_dataset
from app.domain.models import min_to_hhmm
from app.solver.engine import Weights, format_plan, solve
from app.solver.replan import project, replan, state_at
from app.travel.provider import default_provider

DEFAULT_SNAPSHOT = (Path(__file__).resolve().parents[1]
                    / "data" / "seed" / "snapshot.json")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    p.add_argument("--morning-limit", type=int, default=20,
                   help="секунд на утренний план")
    p.add_argument("--time-limit", type=int, default=3,
                   help="секунд на перепланирование — оно обязано быть быстрым")
    p.add_argument("--stability", type=int, default=40,
                   help="во сколько минут пути обходится перенос визита другому инженеру")
    p.add_argument("--routes", type=int, default=0,
                   help="печатать N маршрутов после каждого шага")
    args = p.parse_args()

    ds = load_dataset(args.snapshot)
    provider = default_provider()

    morning_jobs = [j for j in ds.jobs if j.known_at_day_start]
    print(f"Утренний план: {len(morning_jobs)} заявок, {len(ds.engineers)} инженеров")
    plan = solve(ds, morning_jobs, provider=provider,
                 time_limit_s=args.morning_limit)
    k = plan.kpi
    print(f"  назначено {k['jobs_assigned']}/{k['jobs_total']} ({k['assign_rate']} %), "
          f"в пути {k['travel_min']} мин, SLA-нарушений {k['sla_violations']}, "
          f"расчёт {plan.solve_ms} мс")
    if args.routes:
        print(format_plan(plan, ds, limit=args.routes))

    # Каждое время события — точка перепланирования. События, случившиеся в
    # одну минуту, обрабатываются одним пересчётом: диспетчер не станет
    # запускать оптимизатор трижды подряд.
    checkpoints = sorted({ev.at for ev in ds.events})
    weights = Weights(stability=args.stability)
    total_ms = 0

    print(f"\nСобытий за день: {len(ds.events)}, "
          f"точек перепланирования: {len(checkpoints)}\n")

    completed: dict[str, str] = {}      # копится по ходу дня, см. DayState
    for now in checkpoints:
        st = state_at(ds.events, now, completed)
        fresh = [ev for ev in ds.events if ev.at == now]
        print(f"{min_to_hhmm(now)}")
        for ev in fresh:
            print(f"   • {ev.comment}")

        plan, d = replan(ds, plan, st, weights=weights, provider=provider,
                         time_limit_s=args.time_limit)
        total_ms += plan.solve_ms
        k = plan.kpi
        warm = "" if plan.warm_started else "  ⚠ БЕЗ тёплого старта"
        print(f"     пересчёт {plan.solve_ms} мс{warm} → {d.summary()}")
        print(f"     в плане {k['jobs_assigned']}/{k['jobs_total']}, "
              f"выполнено к этому моменту {k['locked_done']}, "
              f"в пути {k['travel_min']} мин, SLA-нарушений {k['sla_violations']}")
        for job_id, was, now_eng in d.moved[:4]:
            print(f"       ↳ {job_id}: {was} → {now_eng}")
        if len(d.moved) > 4:
            print(f"       ↳ … ещё {len(d.moved) - 4}")
        if d.removed:
            print(f"       ✗ снято с плана: {', '.join(d.removed[:6])}")
        print()

    # Последнее событие случается задолго до конца смен, поэтому «доигрываем»
    # день до конца рабочего дня: всё, что стоит в плане, к этому моменту
    # выполнено. Без этого итог показывал бы вечно недоделанный день.
    day_end = max(e.shift_end for e in ds.engineers) + 60
    project(ds, plan, state_at(ds.events, day_end, completed))

    final = state_at(ds.events, day_end)
    known = [j for j in ds.jobs
             if j.created_at_min <= day_end and j.id not in final.cancelled]
    deferred = [j for j in known if j.id not in completed]

    print(f"Итог дня из {len(known)} заявок: выполнено {len(completed)}, "
          f"перенос на завтра {len(deferred)}, отменено клиентом "
          f"{len(final.cancelled)}, нарушений SLA {plan.kpi['sla_violations']}")

    if deferred:
        # Разбор переносов: без него цифра «28 не сделано» выглядит провалом,
        # хотя часть заявок физически нельзя было выполнить.
        late = sum(1 for j in deferred if not j.known_at_day_start)
        closed = sum(1 for j in deferred if j.tw_hard and j.tw_end < day_end - 60)
        prio = Counter(j.priority.value for j in deferred)
        print(f"  из них поступили в течение дня: {late}, "
              f"жёсткое окно закрылось: {closed}")
        print("  по приоритетам: "
              + ", ".join(f"{p} — {prio[p]}" for p in ("P1", "P2", "P3", "P4")
                          if prio[p]))
    print(f"Суммарное время всех перепланирований: {total_ms} мс "
          f"({total_ms / max(1, len(checkpoints)):.0f} мс в среднем, "
          f"{len(checkpoints)} пересчётов)")
    if args.routes:
        print(format_plan(plan, ds, limit=args.routes))


if __name__ == "__main__":
    main()
