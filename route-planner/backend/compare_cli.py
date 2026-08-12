"""Сравнение оптимизатора с ручным планированием — главная цифра для защиты.

    python compare_cli.py
    python compare_cli.py --order fifo --time-limit 30
    python compare_cli.py --all-jobs
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.baseline.greedy import compare, greedy_plan
from app.domain.loader import load_dataset
from app.solver.engine import Weights, solve
from app.travel.provider import default_provider

DEFAULT_SNAPSHOT = (Path(__file__).resolve().parents[1]
                    / "data" / "seed" / "snapshot.json")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    p.add_argument("--time-limit", type=int, default=20)
    p.add_argument("--preset", default="default",
                   choices=["default", "sla", "travel", "balance"])
    p.add_argument("--order", default="edf", choices=["edf", "fifo"],
                   help="как диспетчер разбирает список: по срочности или по поступлению")
    p.add_argument("--pick", default="nearest", choices=["nearest", "soonest"],
                   help="кого выбирает диспетчер: ближайшего или того, кто раньше освободится")
    p.add_argument("--all-jobs", action="store_true")
    args = p.parse_args()

    ds = load_dataset(args.snapshot)
    provider = default_provider()
    jobs = ds.jobs if args.all_jobs else [j for j in ds.jobs if j.known_at_day_start]

    how = ("по срочности окна" if args.order == "edf" else "в порядке поступления")
    who = ("ближайшему подходящему инженеру" if args.pick == "nearest"
           else "тому, кто раньше освободится")
    print(f"Датасет: {len(jobs)} заявок, {len(ds.engineers)} инженеров, {ds.date}")
    print(f"Baseline: диспетчер разбирает заявки {how}\n"
          f"          и отдаёт каждую {who}.\n")

    base = greedy_plan(ds, jobs, provider=provider, order=args.order,
                       pick=args.pick)
    # оптимизатору отдаём ту же комплектацию инструментом: сравниваем
    # маршрутизацию, а не удачу утренней выдачи со склада
    opt = solve(ds, jobs, weights=Weights.preset(args.preset), provider=provider,
                time_limit_s=args.time_limit, onboard=base.onboard,
                pickup=base.pickup)

    print(compare(base, opt).text())
    print(f"\nРасчёт: вручную {base.solve_ms} мс, оптимизатор {opt.solve_ms} мс")

    b, o = base.kpi, opt.kpi
    saved = b["travel_min"] - o["travel_min"]
    extra = o["jobs_assigned"] - b["jobs_assigned"]
    print("\nЧто это значит на день:")
    if extra > 0:
        print(f"  +{extra} заявки закрыто теми же людьми")
    if saved > 0:
        print(f"  −{saved} мин в пути = {saved / 60:.1f} человеко-часов, "
              f"освободившихся под работу")
    if b["sla_violations"] - o["sla_violations"] > 0:
        print(f"  −{b['sla_violations'] - o['sla_violations']} срыва SLA")


if __name__ == "__main__":
    main()
