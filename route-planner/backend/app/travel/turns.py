"""Рёберный граф для автомобиля: манёвры и запреты поворота.

В обычном графе вершина — перекрёсток, и стоимость проезда не зависит от того,
откуда машина приехала. Поэтому в нём нельзя ни оштрафовать левый поворот, ни
запретить его: запрет — это свойство тройки «откуда, через что, куда».

Здесь вершиной становится **направленное ребро дороги**, а дугой — переход с
одного ребра на другое через общий перекрёсток. Тогда запрет выражается просто
отсутствием дуги, а штраф за манёвр — её весом.

Размер: около 63 тысяч рёбер на автомобильном профиле, дуг — порядка 190 тысяч.
Для scipy это немного, зато маршруты перестают сворачивать под «кирпич».

Запросы идут между перекрёстками, а вершины здесь — рёбра, поэтому к графу
на время расчёта пристёгиваются виртуальные вершины-источники: по одной на
точку, с дугами во все рёбра, выходящие из её перекрёстка. Так одна Дейкстра
на точку даёт всю строку матрицы.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

#: Плата за манёвр, минуты. Левый поворот пересекает встречный поток и стоит
#: дороже правого; проезд прямо бесплатен. Значения городские, порядок важнее
#: точности: они меняют выбор маршрута, а не абсолютное время.
#: Радиус поиска в минутах — тот же, что у графа перекрёстков (graph.py):
#: не ограничен, см. пояснение там.
SEARCH_LIMIT_MIN = float("inf")

STRAIGHT_MIN = 0.0
RIGHT_MIN = 0.08          # ~5 секунд
LEFT_MIN = 0.42           # ~25 секунд: ожидание разрыва во встречном потоке
STRAIGHT_ARC = 25.0       # градусов, в пределах которых манёвр считается прямым
UTURN_ARC = 155.0         # круче — это разворот

#: Запреты, которые умеем читать.
NEGATIVE = {"no_left_turn", "no_right_turn", "no_straight_on", "no_u_turn",
            "no_entry", "no_exit"}
POSITIVE = {"only_left_turn", "only_right_turn", "only_straight_on",
            "only_u_turn"}


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Азимут из точки a в точку b, градусы от севера по часовой стрелке."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    y = math.sin(dlon) * math.cos(lat2)
    x = (math.cos(lat1) * math.sin(lat2)
         - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    return math.degrees(math.atan2(y, x)) % 360


def turn_angle(out_bear: float, in_bear: float) -> float:
    """Угол манёвра в градусах: положительный — направо, отрицательный — налево."""
    return (in_bear - out_bear + 540) % 360 - 180


def maneuver_cost(angle: float) -> float:
    if abs(angle) <= STRAIGHT_ARC:
        return STRAIGHT_MIN
    return RIGHT_MIN if angle > 0 else LEFT_MIN


def matches_kind(kind: str, angle: float) -> bool:
    """Совпадает ли манёвр с тем, что назван в запрете.

    Проверка нужна не для красоты. В OSM запрет ссылается на *путь*, а
    двусторонняя улица подходит к перекрёстку с обеих сторон, и оба подъезда
    одинаково «принадлежат» этому пути. Без сверки угла знак «поворот налево
    запрещён» глушил бы заодно и встречное направление, где тот же поворот —
    правый и совершенно законный.
    """
    if kind == "no_left_turn":
        return -UTURN_ARC <= angle < -STRAIGHT_ARC
    if kind == "no_right_turn":
        return STRAIGHT_ARC < angle <= UTURN_ARC
    if kind == "no_straight_on":
        return abs(angle) <= STRAIGHT_ARC
    if kind == "no_u_turn":
        return abs(angle) > UTURN_ARC
    if kind == "only_left_turn":
        return -UTURN_ARC <= angle < -STRAIGHT_ARC
    if kind == "only_right_turn":
        return STRAIGHT_ARC < angle <= UTURN_ARC
    if kind == "only_straight_on":
        return abs(angle) <= STRAIGHT_ARC
    if kind == "only_u_turn":
        return abs(angle) > UTURN_ARC
    # no_entry / no_exit говорят о направлении, а не о геометрии манёвра
    return True


class TurnGraph:
    """Граф манёвров поверх контрактированной дорожной сети."""

    def __init__(self) -> None:
        self.tail = np.zeros(0, dtype=np.int32)
        self.head = np.zeros(0, dtype=np.int32)
        self.minutes = np.zeros(0, dtype=np.float64)
        self.pairs: list[tuple[int, int]] = []      # ребро -> (u, v) графа дорог
        self.out_of: dict[int, list[int]] = {}
        self.in_of: dict[int, list[int]] = {}
        self.arcs: csr_matrix | None = None
        self.stats: dict[str, int] = {}

    # ------------------------------------------------------------------ сборка

    @classmethod
    def build(cls, edges: list[tuple[int, int, float, int]],
              geometry: dict[tuple[int, int], list[tuple[float, float]]],
              restrictions: list[dict],
              node_index: dict[int, int]) -> "TurnGraph":
        """edges: (перекрёсток-начало, перекрёсток-конец, минуты, id пути OSM)."""
        g = cls()
        g.pairs = [(u, v) for u, v, _, _ in edges]
        g.tail = np.array([u for u, _, _, _ in edges], dtype=np.int32)
        g.head = np.array([v for _, v, _, _ in edges], dtype=np.int32)
        g.minutes = np.array([m for _, _, m, _ in edges], dtype=np.float64)
        ways = [w for _, _, _, w in edges]

        for i, (u, v) in enumerate(g.pairs):
            g.out_of.setdefault(u, []).append(i)
            g.in_of.setdefault(v, []).append(i)

        # азимуты на концах рёбер — по первому и последнему звену геометрии
        out_bear = np.zeros(len(edges))
        in_bear = np.zeros(len(edges))
        for i, key in enumerate(g.pairs):
            chain = geometry.get(key)
            if not chain or len(chain) < 2:
                continue
            in_bear[i] = bearing(chain[0], chain[1])
            out_bear[i] = bearing(chain[-2], chain[-1])

        forbidden, only = g._compile_restrictions(restrictions, ways, node_index,
                                                  out_bear, in_bear)

        rows: list[int] = []
        cols: list[int] = []
        cost: list[float] = []
        blocked = uturns = 0
        for i in range(len(edges)):
            via = int(g.head[i])
            options = g.out_of.get(via, ())
            allowed = only.get(i)
            for j in options:
                if allowed is not None and j not in allowed:
                    blocked += 1
                    continue
                if (i, j) in forbidden:
                    blocked += 1
                    continue
                angle = turn_angle(out_bear[i], in_bear[j])
                # Разворот запрещаем везде, кроме тупика: там это единственный
                # способ уехать, и запрет сделал бы адрес недостижимым.
                if abs(angle) > UTURN_ARC and len(options) > 1:
                    uturns += 1
                    continue
                rows.append(i)
                cols.append(j)
                cost.append(g.minutes[j] + maneuver_cost(angle))

        n = len(edges)
        g.arcs = csr_matrix((np.array(cost), (np.array(rows, dtype=np.int32),
                                              np.array(cols, dtype=np.int32))),
                            shape=(n, n))
        g.stats.update(edges=n, arcs=len(rows),
                       blocked_by_signs=blocked, uturns_dropped=uturns)
        print(f"    манёвры: {n} рёбер, {len(rows)} переходов, "
              f"{blocked} запрещено знаками, {uturns} разворотов убрано")
        return g

    def _compile_restrictions(self, restrictions: list[dict], ways: list[int],
                              node_index: dict[int, int],
                              out_bear: np.ndarray, in_bear: np.ndarray):
        """Перевести отношения OSM в пары рёбер нашего графа.

        Один путь OSM после контракции превращается в несколько рёбер, поэтому
        отношение раскрывается в набор пар «входящее ребро → исходящее», а из
        них остаются только те, чей манёвр совпадает с названным в запрете
        (см. `matches_kind`).
        """
        by_way_head: dict[tuple[int, int], list[int]] = {}
        by_way_tail: dict[tuple[int, int], list[int]] = {}
        for i, way in enumerate(ways):
            by_way_head.setdefault((way, int(self.head[i])), []).append(i)
            by_way_tail.setdefault((way, int(self.tail[i])), []).append(i)

        forbidden: set[tuple[int, int]] = set()
        only: dict[int, set[int]] = {}
        applied = no_node = no_ways = no_match = 0
        for r in restrictions:
            via = node_index.get(r["via"])
            kind = r.get("kind", "")
            if via is None or (kind not in NEGATIVE and kind not in POSITIVE):
                no_node += 1
                continue
            src = by_way_head.get((r["from"], via), ())
            dst = by_way_tail.get((r["to"], via), ())
            if not src or not dst:
                no_ways += 1
                continue

            hit = False
            for i in src:
                fits = [j for j in dst
                        if matches_kind(kind, turn_angle(out_bear[i], in_bear[j]))]
                if not fits:
                    continue
                hit = True
                if kind in NEGATIVE:
                    forbidden.update((i, j) for j in fits)
                else:
                    only.setdefault(i, set()).update(fits)
            applied += hit
            no_match += not hit

        self.stats.update(applied=applied, no_node=no_node,
                          no_ways=no_ways, no_match=no_match)
        print(f"    запреты поворота: применено {applied}; мимо: "
              f"{no_node} без перекрёстка, {no_ways} без улицы в выгрузке, "
              f"{no_match} не совпал манёвр")
        return forbidden, only

    # ------------------------------------------------------------------ запросы

    def _augmented(self, nodes: np.ndarray) -> tuple[csr_matrix, int]:
        """Граф с виртуальными источниками: по одному на запрошенную точку."""
        assert self.arcs is not None
        n = self.arcs.shape[0]
        rows: list[int] = []
        cols: list[int] = []
        cost: list[float] = []
        for k, node in enumerate(nodes):
            for e in self.out_of.get(int(node), ()):
                rows.append(n + k)
                cols.append(e)
                cost.append(float(self.minutes[e]))
        total = n + len(nodes)
        extra = csr_matrix(
            (np.array(cost or [0.0]),
             (np.array(rows or [n], dtype=np.int32),
              np.array(cols or [0], dtype=np.int32))),
            shape=(total, total))
        # Виртуальные источники дописываются строками снизу, поэтому у базовой
        # матрицы достаточно продлить indptr: новых ненулевых элементов в ней
        # нет, и данные переиспользуются без копирования.
        indptr = np.concatenate([
            self.arcs.indptr,
            np.full(len(nodes), self.arcs.indptr[-1], dtype=self.arcs.indptr.dtype)])
        base = csr_matrix((self.arcs.data, self.arcs.indices, indptr),
                          shape=(total, total))
        return (base + extra).tocsr(), n

    def matrix(self, nodes: np.ndarray, want_paths: bool = False,
               limit: float = SEARCH_LIMIT_MIN):
        """Матрица времени между перекрёстками с учётом манёвров."""
        aug, n = self._augmented(nodes)
        sources = np.arange(n, n + len(nodes))
        result = dijkstra(aug, directed=True, indices=sources,
                          return_predecessors=want_paths, limit=limit)
        dist, pred = (result if want_paths else (result, None))
        dist = np.atleast_2d(dist)

        out = np.full((len(nodes), len(nodes)), np.inf)
        for j, node in enumerate(nodes):
            incoming = self.in_of.get(int(node), ())
            if not incoming:
                continue
            out[:, j] = dist[:, list(incoming)].min(axis=1)
        np.fill_diagonal(out, 0.0)
        return (out, dist, pred) if want_paths else out

    def edges_of_path(self, pred_row: np.ndarray, dist_row: np.ndarray,
                      target_node: int) -> list[tuple[int, int]]:
        """Развернуть путь до перекрёстка в последовательность рёбер дорог."""
        incoming = list(self.in_of.get(int(target_node), ()))
        if not incoming:
            return []
        best = min(incoming, key=lambda e: dist_row[e])
        if not np.isfinite(dist_row[best]):
            return []
        chain: list[int] = []
        node = best
        guard = 0
        while node >= 0 and node < len(self.pairs) and guard < 100_000:
            chain.append(node)
            node = int(pred_row[node])
            guard += 1
        chain.reverse()
        return [self.pairs[e] for e in chain]
