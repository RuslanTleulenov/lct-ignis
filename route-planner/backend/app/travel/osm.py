"""Провайдер времени в пути поверх дорожного графа OSM.

Заменяет `HaversineProvider` там, где выгрузка OSM доступна. Реализует тот же
протокол `TravelTimeProvider`, поэтому солвер о подмене не знает, и добавляет
`path()` — геометрию маршрута для карты.

Кеширование обязательно, а не для красоты. `why_not` и `why_this` перебирают
позиции вставки и дёргают `minutes()` десятки раз подряд; без кеша каждый вызов
запускал бы Дейкстру по 38 тысячам вершин. Поэтому результат поиска от вершины
(расстояния и предшественники) сохраняется целиком: первый вызов стоит около
10 мс, остальные из этого же источника — бесплатны.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import numpy as np

from ..domain.models import TransportMode
from .graph import RoutingGraph, haversine_km, source_signature
from .provider import (DETOUR_FACTOR, Matrix, Point, straight_speed_kmh,
                       traffic_factor)

#: Запас вокруг выгрузки, градусы (~2 км): точка чуть за краем ещё привязывается
#: к дорогам, а Кашира в ста километрах южнее — уже нет.
COVERAGE_MARGIN = 0.02

#: Профиль графа для каждого типа транспорта. Велосипед едет по пешему графу
#: (без магистралей, в обе стороны по любой улице), но со своей скоростью.
PROFILE = {
    TransportMode.CAR: "car",
    TransportMode.FOOT: "foot",
    TransportMode.BIKE: "bike",
    TransportMode.TRANSIT: "transit",
}

#: Сколько результатов поиска держим в памяти. На областной выгрузке строка
#: автомобильного профиля — 118 тыс. рёбер × (float32 + int32) ≈ 0,9 МБ, так
#: что 192 источника — это около 180 МБ. Меньше нельзя: матрица дня — это
#: 80–100 источников, и они должны помещаться целиком.
SSSP_CACHE = 192


class OsmRoutingProvider:
    """Маршрутизация по дорогам, пешком и на метро."""

    def __init__(self, graph: RoutingGraph) -> None:
        self.graph = graph
        self._sssp: OrderedDict[tuple[int, str], tuple] = OrderedDict()
        self._matrix_cache: dict[tuple, Matrix] = {}
        self.hits = 0
        self.misses = 0
        # Границы покрытия. Адрес за пределами выгрузки нельзя привязывать к
        # ближайшей вершине: она окажется на краю карты, и дорога до самого
        # адреса — десятки километров — просто пропадёт. Такие точки считаются
        # по прямой с коэффициентом извилистости, как без графа вовсе.
        road = graph.coords[:graph.n_road] if graph.n_road else graph.coords
        self.coverage = (
            float(road[:, 0].min()) - COVERAGE_MARGIN,
            float(road[:, 1].min()) - COVERAGE_MARGIN,
            float(road[:, 0].max()) + COVERAGE_MARGIN,
            float(road[:, 1].max()) + COVERAGE_MARGIN,
        )

    def covered(self, point: Point) -> bool:
        south, west, north, east = self.coverage
        return south <= point[0] <= north and west <= point[1] <= east

    def _straight_minutes(self, a: Point, b: Point, mode: TransportMode) -> float:
        km = haversine_km(a[0], a[1], b[0], b[1]) * DETOUR_FACTOR
        return km / straight_speed_kmh(mode, km) * 60

    # ------------------------------------------------------------------ загрузка

    @classmethod
    def from_cache(cls, osm_dir: Path, cache_path: Path | None = None
                   ) -> "OsmRoutingProvider | None":
        """Собрать провайдер из выгрузки OSM, переиспользуя готовый граф.

        Возвращает None, если выгрузки нет: вызывающий сам решит, падать ему
        или откатиться на геодезическое приближение.
        """
        osm_dir = Path(osm_dir)
        roads = osm_dir / "roads.json"
        if not roads.exists():
            return None
        cache_path = cache_path or (osm_dir / "graph.pkl")
        metro = osm_dir / "metro.json"
        if Path(cache_path).exists():
            try:
                return cls(RoutingGraph.load(Path(cache_path),
                                             source=source_signature(roads, metro)))
            except Exception:
                pass                       # кеш от другой версии или выгрузки — пересоберём
        graph = RoutingGraph.build(roads, metro)
        try:
            graph.save(Path(cache_path))
        except Exception:
            pass                           # кеш не критичен
        return cls(graph)

    # ------------------------------------------------------------------ поиск

    def _profile(self, mode: TransportMode) -> str:
        return PROFILE.get(mode, "car")

    def _penalty(self, mode: TransportMode) -> float:
        return 1.0

    def _turns_active(self, profile: str) -> bool:
        """Для автомобиля считаем по рёберному графу — с манёврами и знаками."""
        return profile == "car" and self.graph.turns is not None

    def _rows(self, vertices, profile: str) -> list[tuple]:
        """Строки поиска (расстояния и предшественники) для набора источников.

        Считаются только недостающие, одним пакетом: Дейкстра из каждой точки
        стоит одинаково, что по одной, что скопом, а вот повторный расчёт
        уже известной строки — чистая потеря. При перепланировании из
        восьмидесяти точек новые — только текущие позиции инженеров.
        """
        vertices = [int(v) for v in vertices]
        missing = sorted({v for v in vertices if (v, profile) not in self._sssp})
        if missing:
            self.misses += len(missing)
            if self._turns_active(profile):
                _, dist, pred = self.graph.turns.matrix(np.array(missing),
                                                        want_paths=True)
            else:
                dist, pred = self.graph.times(np.array(missing), profile,
                                              want_paths=True)
            dist, pred = np.atleast_2d(dist), np.atleast_2d(pred)
            for i, v in enumerate(missing):
                self._sssp[(v, profile)] = (dist[i].astype(np.float32),
                                            pred[i].astype(np.int32))
            # Вытесняем старое, но не то, что нужно прямо сейчас: матрица на
            # сотню точек обязана поместиться целиком, иначе строки исчезали
            # бы раньше, чем их прочитали.
            needed = {(v, profile) for v in vertices}
            for key in list(self._sssp):
                if len(self._sssp) <= SSSP_CACHE:
                    break
                if key not in needed:
                    del self._sssp[key]
        self.hits += len(vertices) - len(missing)
        out = []
        for v in vertices:
            key = (v, profile)
            self._sssp.move_to_end(key)
            out.append(self._sssp[key])
        return out

    def _search(self, vertex: int, profile: str):
        """Поиск от одной вершины — через тот же кеш строк."""
        return self._rows([vertex], profile)[0]

    def _lookup(self, dist_row, target_vertex: int, profile: str) -> float:
        """Время до перекрёстка: у рёберного графа — минимум по входящим рёбрам."""
        if not self._turns_active(profile):
            return float(dist_row[int(target_vertex)])
        incoming = self.graph.turns.in_of.get(int(target_vertex), ())
        if not incoming:
            return float("inf")
        return float(min(dist_row[e] for e in incoming))

    # ------------------------------------------------------------------ протокол

    def matrix(self, points, mode: TransportMode, departure_min: int) -> Matrix:
        profile = self._profile(mode)
        key = (tuple(points), profile)
        base = self._matrix_cache.get(key)
        if base is None:
            nodes = self.graph.snap(list(points), profile)
            rows = self._rows(nodes, profile)
            base = np.empty((len(points), len(points)))
            for i, (dist, _) in enumerate(rows):
                for j, target in enumerate(nodes):
                    base[i, j] = self._lookup(dist, int(target), profile)
            np.fill_diagonal(base, 0.0)
            # Изолированная точка и точка вне покрытия не должны ронять
            # расчёт: пары с ними считаются по прямой с коэффициентом.
            outside = np.array([not self.covered(p) for p in points])
            bad = ~np.isfinite(base) | outside[:, None] | outside[None, :]
            if bad.any():
                fallback = np.array([
                    [self._straight_minutes(a, b, mode) for b in points]
                    for a in points])
                base = np.where(bad, fallback, base)
            self._matrix_cache[key] = base

        scale = traffic_factor(mode, departure_min) * self._penalty(mode)
        scaled = np.rint(base * scale).astype(int)
        np.fill_diagonal(scaled, 0)
        return np.maximum(scaled, 0).tolist()

    def minutes(self, a: Point, b: Point, mode: TransportMode,
                departure_min: int) -> int:
        if a == b:
            return 0
        profile = self._profile(mode)
        if not (self.covered(a) and self.covered(b)):
            value = self._straight_minutes(a, b, mode)
        else:
            va, vb = self.graph.snap([a, b], profile)
            dist, _ = self._search(int(va), profile)
            value = self._lookup(dist, int(vb), profile)
            if not np.isfinite(value):
                value = self._straight_minutes(a, b, mode)
        return max(1, int(round(
            value * traffic_factor(mode, departure_min) * self._penalty(mode))))

    def distance_km(self, a: Point, b: Point,
                    mode: TransportMode = TransportMode.CAR) -> float:
        """Длина реального пути по дорогам, а не по прямой."""
        if not (self.covered(a) and self.covered(b)):
            return haversine_km(a[0], a[1], b[0], b[1]) * DETOUR_FACTOR
        line = self.path(a, b, mode)
        if len(line) < 2:
            return haversine_km(a[0], a[1], b[0], b[1]) * DETOUR_FACTOR
        total = 0.0
        for (lon1, lat1), (lon2, lat2) in zip(line, line[1:]):
            total += haversine_km(lat1, lon1, lat2, lon2)
        return total

    # ------------------------------------------------------------------ геометрия

    def path(self, a: Point, b: Point,
             mode: TransportMode = TransportMode.CAR) -> list[list[float]]:
        """Ломаная [[lon, lat], …] по дорогам — то, что рисуется на карте."""
        if a == b:
            return []
        if not (self.covered(a) and self.covered(b)):
            return [[a[1], a[0]], [b[1], b[0]]]      # вне покрытия — прямая
        profile = self._profile(mode)
        va, vb = self.graph.snap([a, b], profile)
        dist, pred = self._search(int(va), profile)
        if self._turns_active(profile):
            line = self._geometry_of(
                self.graph.turns.edges_of_path(pred, dist, int(vb)))
        else:
            line = self.graph.route_geometry(pred, int(va), int(vb))
        if not line:
            return [[a[1], a[0]], [b[1], b[0]]]
        # Дом и объект стоят не на перекрёстке: дотягиваем концы до точек.
        return [[a[1], a[0]], *line, [b[1], b[0]]]

    def _geometry_of(self, edges: list[tuple[int, int]]) -> list[list[float]]:
        """Склеить ломаную по последовательности рёбер дорог."""
        out: list[list[float]] = []
        for key in edges:
            seg = self.graph.geometry.get(key)
            if not seg:
                continue
            pts = seg if not out else seg[1:]
            out.extend([[lon, lat] for lat, lon in pts])
        return out

    def uses_metro(self, a: Point, b: Point) -> bool:
        """Проходит ли пеший маршрут через метро — для подписи на карте."""
        if not (self.covered(a) and self.covered(b)):
            return False
        profile = "transit"
        va, vb = self.graph.snap([a, b], profile)
        _, pred = self._search(int(va), profile)
        node, guard = int(vb), 0
        while node != int(va) and node >= 0 and guard < 100_000:
            if node >= self.graph.n_road:
                return True
            node = int(pred[node])
            guard += 1
        return False
