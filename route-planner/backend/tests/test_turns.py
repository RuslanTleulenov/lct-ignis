"""Граф манёвров: штрафы за поворот и запреты со знаков.

Проверяется на игрушечном перекрёстке, где правильный ответ известен заранее.
На настоящей выгрузке OSM такое не проверить: там нельзя сказать, объехал
маршрут «кирпич» или просто нашёл другую улицу.

Перекрёсток — плюс: с запада, востока, севера и юга в центр С сходятся четыре
улицы, все двусторонние.

        N
        |
  W --- C --- E
        |
        S
"""

from __future__ import annotations

import numpy as np
import pytest

from app.travel.turns import (LEFT_MIN, RIGHT_MIN, STRAIGHT_MIN, TurnGraph,
                              maneuver_cost, matches_kind, turn_angle)

C, W, E, N, S = 0, 1, 2, 3, 4
COORD = {
    C: (55.750, 37.600),
    W: (55.750, 37.580),
    E: (55.750, 37.620),
    N: (55.762, 37.600),
    S: (55.738, 37.600),
}
#: Улицы OSM: запад–восток и север–юг. Каждая проходит перекрёсток насквозь,
#: как это чаще всего и бывает в выгрузке.
WAY = {(W, C): 100, (C, E): 100, (E, C): 100, (C, W): 100,
       (N, C): 200, (C, S): 200, (S, C): 200, (C, N): 200}

MINUTES = 2.0


@pytest.fixture
def pieces():
    edges = [(u, v, MINUTES, way) for (u, v), way in WAY.items()]
    geometry = {(u, v): [COORD[u], COORD[v]] for u, v in WAY}
    node_index = {1000 + k: k for k in COORD}      # id узла OSM -> вершина
    return edges, geometry, node_index


def build(pieces, restrictions=()):
    edges, geometry, node_index = pieces
    return TurnGraph.build(edges, geometry, list(restrictions), node_index)


def minutes_between(graph: TurnGraph, a: int, b: int) -> float:
    return float(graph.matrix(np.array([a, b]))[0, 1])


def has_arc(graph: TurnGraph, frm: tuple[int, int], to: tuple[int, int]) -> bool:
    """Разрешён ли манёвр «с ребра frm на ребро to»."""
    i, j = graph.pairs.index(frm), graph.pairs.index(to)
    return j in graph.arcs[i].indices


# ---------------------------------------------------------------- геометрия

def test_turn_angle_signs():
    """Направо — положительный угол, налево — отрицательный."""
    east, north, west = 90.0, 0.0, 270.0
    assert turn_angle(east, east) == pytest.approx(0)
    assert turn_angle(east, north) < 0            # с востока на север — налево
    assert turn_angle(east, 180.0) > 0            # на юг — направо
    assert abs(turn_angle(east, west)) > 155      # разворот


def test_left_costs_more_than_right():
    assert maneuver_cost(0) == STRAIGHT_MIN
    assert maneuver_cost(90) == RIGHT_MIN
    assert maneuver_cost(-90) == LEFT_MIN
    assert LEFT_MIN > RIGHT_MIN > STRAIGHT_MIN


# ---------------------------------------------------------------- штрафы

def test_left_turn_is_more_expensive_than_right(pieces):
    """Едем с запада: на север — налево, на юг — направо."""
    g = build(pieces)
    left = minutes_between(g, W, N)
    right = minutes_between(g, W, S)
    assert left > right, "левый поворот не дороже правого"
    assert left - right == pytest.approx(LEFT_MIN - RIGHT_MIN, abs=1e-6)


def test_straight_is_free(pieces):
    """Проезд прямо стоит ровно два ребра — без надбавки."""
    g = build(pieces)
    assert minutes_between(g, W, E) == pytest.approx(2 * MINUTES, abs=1e-6)


def test_u_turn_at_crossroads_is_dropped(pieces):
    """Через перекрёсток развернуться нельзя — там есть куда свернуть."""
    g = build(pieces)
    assert not has_arc(g, (W, C), (C, W)), "разворот через перекрёсток остался"
    assert has_arc(g, (W, C), (C, E)), "проезд прямо пропал вместе с разворотом"
    assert g.stats["uturns_dropped"] == 4       # по одному на каждый подъезд


def test_u_turn_in_dead_end_survives():
    """В тупике разворот — единственный выход, иначе адрес недостижим."""
    a, b = 0, 1
    edges = [(a, b, 1.0, 1), (b, a, 1.0, 1)]
    geometry = {(a, b): [(55.75, 37.60), (55.75, 37.61)],
                (b, a): [(55.75, 37.61), (55.75, 37.60)]}
    g = TurnGraph.build(edges, geometry, [], {})
    assert g.stats["arcs"] > 0, "из тупика не выехать"
    assert float(g.matrix(np.array([a, b]))[1, 0]) < np.inf


# ---------------------------------------------------------------- запреты

def restriction(kind: str, frm: int, to: int) -> dict:
    return {"kind": kind, "from": frm, "via": 1000 + C, "to": to}


def test_no_left_turn_removes_the_maneuver(pieces):
    """Знак «налево нельзя» должен убрать манёвр, а не сделать его дороже."""
    free = build(pieces)
    blocked = build(pieces, [restriction("no_left_turn", 100, 200)])
    assert has_arc(free, (W, C), (C, N))
    assert not has_arc(blocked, (W, C), (C, N)), \
        "маршрут всё ещё сворачивает под знак"
    # правый поворот и проезд прямо запрет не задевает
    assert has_arc(blocked, (W, C), (C, S))
    assert has_arc(blocked, (W, C), (C, E))


def test_forbidden_turn_costs_a_detour(pieces):
    """Раз повернуть нельзя, до цели придётся ехать дольше."""
    free = build(pieces)
    blocked = build(pieces, [restriction("no_left_turn", 100, 200)])
    assert minutes_between(blocked, W, N) > minutes_between(free, W, N), \
        "запрет не отразился на времени в пути"


def test_restriction_does_not_hit_opposite_approach(pieces):
    """Запрет с двусторонней улицы не должен глушить встречное направление.

    В OSM `from` — это путь целиком, и к перекрёстку он подходит с двух сторон.
    Для машины с запада поворот на север левый, для машины с востока — правый;
    знак «налево нельзя» относится только к первой.
    """
    g = build(pieces, [restriction("no_left_turn", 100, 200)])
    assert not has_arc(g, (W, C), (C, N))
    assert has_arc(g, (E, C), (C, N)), \
        "запрет применился к встречному подъезду, где поворот правый"


def test_only_straight_on_blocks_the_turns(pieces):
    """Предписывающий знак: с запада можно только прямо."""
    g = build(pieces, [restriction("only_straight_on", 100, 100)])
    assert has_arc(g, (W, C), (C, E))
    assert not has_arc(g, (W, C), (C, N))
    assert not has_arc(g, (W, C), (C, S))


def test_kind_must_match_the_maneuver():
    assert matches_kind("no_left_turn", -90)
    assert not matches_kind("no_left_turn", 90)
    assert matches_kind("no_right_turn", 90)
    assert matches_kind("no_straight_on", 5)
    assert matches_kind("no_u_turn", 179)
    assert not matches_kind("no_u_turn", 90)
    assert matches_kind("no_entry", 0), "запрет въезда не про угол манёвра"


def test_unmatched_restriction_is_counted_not_applied(pieces):
    """Запрет на улицу, которой нет в выгрузке, должен попасть в статистику."""
    g = build(pieces, [restriction("no_left_turn", 100, 999)])
    assert g.stats["applied"] == 0
    assert g.stats["no_ways"] == 1
