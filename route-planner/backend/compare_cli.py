"""Сравнение оптимизатора с базовыми вариантами — главная цифра для защиты.

    python compare_cli.py
    python compare_cli.py --snapshot ../data/beeline/vostok/snapshot.json
    python compare_cli.py --baseline smart --time-limit 30

Базовых варианта два (см. app/baseline/greedy.py): «по ТЗ» — единый для всех
команд порядок «первому подходящему», и «грамотный диспетчер» — по срочности
окна ближайшему. Для выгрузки заказчика есть и третий столбец — факт: как
заявки раскидал настоящий диспетчер 17 августа.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.baseline.control import control_plan
from app.baseline.greedy import compare, greedy_plan, tz_baseline
from app.domain.loader import load_dataset
from app.solver.engine import Weights, solve
from app.travel.provider import default_provider

DEFAULT_SNAPSHOT = (Path(__file__).resolve().parents[1]
                    / "data" / "beeline" / "vostok" / "snapshot.json")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    p.add_argument("--time-limit", type=int, default=20)
    p.add_argument("--preset", default="default",
                   choices=["default", "sla", "travel", "balance", "staff"])
    p.add_argument("--baseline", default="tz", choices=["tz", "smart"],
                   help="tz — первому подходящему по порядку (ТЗ, п. 2.3); "
                        "smart — по срочности окна ближайшему")
    p.add_argument("--all-jobs", action="store_true")
    args = p.parse_args()

    ds = load_dataset(args.snapshot)
    provider = default_provider()
    jobs = ds.jobs if args.all_jobs else [j for j in ds.jobs if j.known_at_day_start]

    print(f"Набор: {ds.title or args.snapshot}")
    print(f"  {len(jobs)} заявок, {len(ds.engineers)} инженеров, {ds.date}, "
          f"{'с возвратом' if ds.return_to_start else 'без возврата'} в стартовую точку\n")

    if args.baseline == "tz":
        base = tz_baseline(ds, jobs, provider=provider)
        print("Базовый вариант по ТЗ: заявки по порядку поступления, каждая — первому\n"
              "по порядку подходящему инженеру; порядок визитов = порядок назначения.\n")
    else:
        base = greedy_plan(ds, jobs, provider=provider)
        print("Базовый вариант «грамотный диспетчер»: заявки по срочности окна,\n"
              "каждая — ближайшему подходящему инженеру.\n")

    # оптимизатору отдаём ту же комплектацию инструментом: сравниваем
    # маршрутизацию, а не удачу утренней выдачи со склада
    opt = solve(ds, jobs, weights=Weights.preset(args.preset), provider=provider,
                time_limit_s=args.time_limit, onboard=base.onboard,
                pickup=base.pickup)

    print(compare(base, opt).text())
    print(f"\nРасчёт: базовый {base.solve_ms} мс, оптимизатор {opt.solve_ms} мс")

    b, o = base.kpi, opt.kpi
    print("\nОбязательные метрики ТЗ:")
    print(f"  задействовано исполнителей: {b['engineers_used']} → {o['engineers_used']}")
    print(f"  суммарный пробег:           {b['travel_km']:.1f} → {o['travel_km']:.1f} км")
    print("  пробег по исполнителям (оптимизатор):")
    for r in sorted(opt.routes, key=lambda r: -r.travel_km):
        if r.job_count:
            print(f"    {r.engineer_name:24s} {r.job_count:2d} заявок  {r.travel_km:6.1f} км")

    fact = control_plan(ds, jobs, provider)
    if fact is not None:
        print("\nФакт — распределение диспетчера заказчика из контрольного файла\n"
              "(порядок визитов восстановлен по началу окна, время в пути — наше):")
        print(compare(fact, opt).text().replace("Вручную", "   Факт"))
        f = fact.kpi
        print(f"\n  по статусам выгрузки: выполнено {f['control_done']}, "
              f"просрочено {f['control_overdue']}, отменено {f['control_cancelled']}, "
              f"не отправлено {f['jobs_unassigned']}")


if __name__ == "__main__":
    main()
