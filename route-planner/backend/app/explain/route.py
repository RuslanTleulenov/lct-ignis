"""Почему маршрут инженера выглядит именно так.

ТЗ, п. 2.4.2: «для выбранного маршрута — краткое объяснение, какие
ограничения и факторы повлияли на решение». Объяснения по заявкам (why_this)
отвечают «почему этот инженер»; здесь ответ на другой вопрос — почему визиты
идут в таком порядке, откуда взялось время выезда и что в этом маршруте
диктовали ограничения, а что выбрала оптимизация.

Всё считается по готовому плану, ничего не придумывается: каждая фраза
подтверждается числом из маршрута.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import Dataset, min_to_hhmm
from ..solver.engine import Plan, Route
from ..travel.provider import haversine_km


@dataclass(slots=True)
class RouteExplanation:
    engineer_id: str
    engineer_name: str
    #: короткая сводка: одна строка
    headline: str
    #: что диктовали ограничения — окна, смена, навыки, транспорт, обед
    constraints: list[str] = field(default_factory=list)
    #: что выбрала оптимизация — порядок, выезд, дорога
    choices: list[str] = field(default_factory=list)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def explain_route(ds: Dataset, plan: Plan, engineer_id: str) -> RouteExplanation | None:
    route: Route | None = next((r for r in plan.routes if r.engineer_id == engineer_id), None)
    if route is None:
        return None
    eng = ds.engineer(engineer_id)
    visits = [s for s in route.stops if s.kind == "job" and s.job_id]
    if not visits:
        return RouteExplanation(
            engineer_id, route.engineer_name,
            "Заявок не досталось: все подходящие по навыку и транспорту закрыты "
            "другими инженерами с меньшей дорогой. В смену не выходит — пробег ноль.")

    jobs = [ds.job(s.job_id) for s in visits]
    out = RouteExplanation(engineer_id, route.engineer_name, "")

    # ---- ограничения --------------------------------------------------
    hard = sum(1 for j in jobs if j.tw_hard)
    late = [s for s in visits if s.sla_late_min]
    if hard and not late:
        out.constraints.append(
            f"Все {_plural(len(visits), 'визит', 'визита', 'визитов')} начинаются внутри "
            f"окон клиентов, {hard} из них жёсткие — начало не сдвинуть ни в одну сторону.")
    elif late:
        out.constraints.append(
            f"{_plural(len(late), 'визит', 'визита', 'визитов')} с опозданием к SLA: "
            + ", ".join(f"{ds.job(s.job_id).customer} +{s.sla_late_min} мин" for s in late[:3])
            + " — иначе заявку пришлось бы не брать вовсе.")
    else:
        out.constraints.append("Окна клиентов мягкие: порядок выбирался по дороге.")

    skills = {ds.specializations.get(j.specialization, j.specialization) for j in jobs}
    out.constraints.append(
        "Навык: " + ", ".join(sorted(skills)) + " — есть у инженера, поэтому эти заявки "
        "ему доступны; заявок с другим навыком в маршруте нет.")

    transport_req = {ds.required_transport(j) for j in jobs} - {None}
    if transport_req:
        out.constraints.append(
            "Транспорт: " + ", ".join(t.label.lower() for t in transport_req)
            + f" требуется явно, у инженера — {eng.vehicle_type.label.lower()}.")
    else:
        out.constraints.append(
            f"Транспорт: {eng.vehicle_type.label.lower()}; ограничений по транспорту "
            f"у заявок нет, время в пути считалось по его профилю.")

    if route.lunch_min:
        if route.lunch_start is not None:
            out.constraints.append(
                f"Обед {route.lunch_min} мин с {min_to_hhmm(route.lunch_start)} — в окне "
                f"{min_to_hhmm(eng.break_from)}–{min_to_hhmm(eng.break_to)}, между визитами.")
        else:
            out.constraints.append(
                f"Обед {route.lunch_min} мин размещён внутри перегона в окне "
                f"{min_to_hhmm(eng.break_from)}–{min_to_hhmm(eng.break_to)}.")

    end_slack = eng.shift_end - route.end_min
    if end_slack < 0:
        out.constraints.append(
            f"Смена до {min_to_hhmm(eng.shift_end)}, маршрут заканчивается в "
            f"{min_to_hhmm(route.end_min)}: переработка {-end_slack} мин в пределах "
            f"допустимых {eng.max_overtime_min}.")
    else:
        out.constraints.append(
            f"Смена {min_to_hhmm(eng.shift_start)}–{min_to_hhmm(eng.shift_end)}: маршрут "
            f"укладывается, запас {end_slack} мин к концу смены.")

    # ---- выбор оптимизации ---------------------------------------------
    starts = [j.tw_start for j in jobs]
    if len(jobs) > 1 and starts == sorted(starts) and hard:
        out.choices.append(
            "Порядок объезда задан окнами: "
            + " → ".join(f"{min_to_hhmm(j.tw_start)}–{min_to_hhmm(j.tw_end)}" for j in jobs[:6])
            + (" → …" if len(jobs) > 6 else "") + ".")
    else:
        out.choices.append(
            "Порядок объезда выбран по дороге: соседние визиты — ближайшие по улицам, "
            "а не по списку.")

    first, first_job = visits[0], jobs[0]
    window = f"{min_to_hhmm(first_job.tw_start)}–{min_to_hhmm(first_job.tw_end)}"
    if route.start_min <= eng.starts_at + 5:
        out.choices.append(f"Выезд в начале смены, {min_to_hhmm(route.start_min)}.")
    elif first.service_start <= first_job.tw_start + 5:
        out.choices.append(
            f"Выезд в {min_to_hhmm(route.start_min)} — ровно к открытию первого окна "
            f"{window}: раньше ехать некуда, ожидания у подъезда нет.")
    else:
        out.choices.append(
            f"Выезд в {min_to_hhmm(route.start_min)}, позже начала смены: первый визит "
            f"поставлен на {min_to_hhmm(first.service_start)} внутри окна {window}, чтобы "
            f"следующие визиты шли встык, а не через ожидание у подъездов.")

    straight = 0.0
    for prev, stop in zip(route.stops, route.stops[1:]):
        if stop.kind == "job":
            straight += haversine_km((prev.lat, prev.lon), (stop.lat, stop.lon))
    detour = (route.travel_km / straight - 1) * 100 if straight > 0 else 0
    out.choices.append(
        f"Дорога {route.travel_min} мин и {route.travel_km:.1f} км на "
        f"{_plural(len(visits), 'визит', 'визита', 'визитов')}; по прямой было бы "
        f"{straight:.1f} км — улицы добавляют {detour:.0f} %.")

    waited = sum(s.wait_min for s in visits)
    if waited:
        worst = max(visits, key=lambda s: s.wait_min)
        out.choices.append(
            f"Ожидание открытия окон {waited} мин, больше всего у "
            f"«{ds.job(worst.job_id).customer}» ({worst.wait_min} мин): ближе никого "
            f"подставить не вышло, уехать и вернуться дороже.")
    else:
        out.choices.append("Ожидания у подъездов нет: визиты состыкованы окнами.")

    if not ds.return_to_start:
        out.choices.append(
            "Маршрут заканчивается на последней заявке: возврат в стартовую точку "
            "по ТЗ не планируется и в пробег не входит.")

    busy = route.work_min + route.travel_min
    out.headline = (
        f"{_plural(len(visits), 'визит', 'визита', 'визитов')}, "
        f"{min_to_hhmm(route.start_min)}–{min_to_hhmm(route.end_min)}: работа "
        f"{route.work_min} мин, дорога {route.travel_min} мин ({route.travel_km:.1f} км), "
        f"занятость {busy // 60} ч {busy % 60:02d} м"
        + (f", опозданий к SLA {len(late)}" if late else ", без опозданий") + ".")
    return out
