"""Построить план дня из датасета и напечатать его. Проверка ядра без UI.

    python solve_cli.py
    python solve_cli.py --preset sla --limit 5
    python solve_cli.py --all-jobs --time-limit 20
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.domain.loader import load_dataset
from app.explain.why_not import why_not_report
from app.explain.why_this import why_this
from app.solver.engine import Weights, format_plan, solve
from app.travel.provider import default_provider

DEFAULT_SNAPSHOT = (Path(__file__).resolve().parents[1]
                    / "data" / "seed" / "snapshot.json")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    p.add_argument("--preset", default="default",
                   choices=["default", "sla", "travel", "balance"],
                   help="что важнее: SLA, пробег или ровная загрузка")
    p.add_argument("--time-limit", type=int, default=10, help="секунд на поиск")
    p.add_argument("--limit", type=int, default=None, help="сколько маршрутов печатать")
    p.add_argument("--all-jobs", action="store_true",
                   help="планировать и те заявки, что поступают в течение дня")
    p.add_argument("--no-breaks", action="store_true", help="отключить обед")
    p.add_argument("--why", action="store_true",
                   help="разобрать причины по неназначенным заявкам")
    p.add_argument("--explain", metavar="JOB-ID", nargs="+",
                   help="объяснить назначение конкретных заявок")
    args = p.parse_args()

    ds = load_dataset(args.snapshot)
    provider = default_provider()
    jobs = ds.jobs if args.all_jobs else [j for j in ds.jobs if j.known_at_day_start]

    print(f"Датасет: {len(ds.jobs)} заявок, {len(ds.engineers)} инженеров, "
          f"дата {ds.date}")
    print(f"Планируем: {len(jobs)} заявок, пресет «{args.preset}», "
          f"лимит {args.time_limit} с\n")

    plan = solve(
        ds, jobs,
        weights=Weights.preset(args.preset),
        provider=provider,
        time_limit_s=args.time_limit,
        use_breaks=not args.no_breaks,
    )
    print(format_plan(plan, ds, limit=args.limit))
    if args.explain:
        for job_id in args.explain:
            print()
            exp = why_this(ds, plan, ds.job(job_id), provider)
            print(exp.text() if exp else f"{job_id}: в плане нет")
    if args.why:
        print()
        print(why_not_report(ds, plan, provider))


if __name__ == "__main__":
    main()
