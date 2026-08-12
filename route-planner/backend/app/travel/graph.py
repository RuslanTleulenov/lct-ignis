"""Граф маршрутизации по данным OSM: дороги, пешком и метро.

Заменяет геодезическую прямую на честный путь по улицам. Даёт и время в пути,
и геометрию для карты.

Устройство:

1. **Контракция.** Сырой OSM — 191 тыс. узлов на 43 тыс. линий, но большинство
   узлов лишь описывают изгиб дороги. Оставляем только перекрёстки и концы
   линий, а промежуточные точки складываем в геометрию ребра. Граф сжимается
   в несколько раз, а рисовать по-прежнему есть что.

2. **Три профиля на одном графе.** Рёбра общие, различаются веса:
   `car` — скорость по классу дороги с учётом односторонности;
   `foot` — 4.8 км/ч, без магистралей;
   `transit` — пешие рёбра плюс слой метро (перегоны, пересадки, вход).

3. **Матрицы — scipy.** `csgraph.dijkstra` с несколькими источниками считает
   90×90 за секунды; на чистом Python это были бы минуты.

Пробки учитываются множителем к готовой матрице, а не пересборкой графа:
в нашей модели коэффициент одинаков по всей сети, поэтому достаточно
домножить результат.
"""

from __future__ import annotations

import json
import math
import pickle
import time
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

EARTH_R_KM = 6371.0


def _csr(edges: list[tuple[int, int, float]], n: int) -> csr_matrix:
    """Собрать разреженную матрицу, оставив минимальный вес на пару вершин.

    Дубликаты неизбежны: две улицы могут соединять одни и те же перекрёстки.
    csr_matrix складывает такие веса, а нужен минимум — иначе параллельная
    дорога делает путь дороже, а не дешевле.
    """
    if not edges:
        return csr_matrix((n, n))
    best: dict[tuple[int, int], float] = {}
    for a, b, w in edges:
        key = (a, b)
        if w < best.get(key, math.inf):
            best[key] = w
    rows = np.fromiter((k[0] for k in best), dtype=np.int32, count=len(best))
    cols = np.fromiter((k[1] for k in best), dtype=np.int32, count=len(best))
    data = np.fromiter(best.values(), dtype=np.float64, count=len(best))
    return csr_matrix((data, (rows, cols)), shape=(n, n))

#: Скорость по классу дороги, км/ч. Значения городские: это не разрешённая
#: скорость, а реальная средняя с учётом светофоров и поворотов.
CAR_SPEED = {
    "motorway": 72, "motorway_link": 45,
    "trunk": 60, "trunk_link": 40,
    "primary": 45, "primary_link": 32,
    "secondary": 38, "secondary_link": 28,
    "tertiary": 32, "tertiary_link": 25,
    "unclassified": 28,
}

#: По магистралям пешком не ходят.
FOOT_FORBIDDEN = {"motorway", "motorway_link", "trunk", "trunk_link"}
FOOT_SPEED_KMH = 4.8

#: Метро: средняя скорость с учётом стоянок, пересадка и вход с ожиданием.
METRO_SPEED_KMH = 41.0
METRO_DWELL_MIN = 0.4
TRANSFER_MIN = 4.0
BOARDING_MIN = 3.0          # спуск, турникеты, ожидание поезда
TRANSFER_RADIUS_KM = 0.45   # станции ближе этого считаем пересадочными
ACCESS_RADIUS_KM = 1.3      # дальше этого до станции пешком не идут
ACCESS_LINKS = 3            # к скольким ближайшим станциям привязывать точку


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(math.sqrt(h))


class RoutingGraph:
    """Контрактированный граф дорог с наложенным слоем метро."""

    def __init__(self) -> None:
        self.coords: np.ndarray = np.zeros((0, 2))     # (lat, lon) вершин
        self.n_road: int = 0                           # сколько из них дорожных
        self.station_name: list[str] = []
        self.graphs: dict[str, csr_matrix] = {}
        self.geometry: dict[tuple[int, int], list[tuple[float, float]]] = {}
        #: профиль -> (KD-дерево, номера вершин крупнейшей связной компоненты)
        self._snap: dict[str, tuple[cKDTree, np.ndarray]] = {}

    # ------------------------------------------------------------------ сборка

    @classmethod
    def build(cls, roads_path: Path, metro_path: Path | None = None) -> "RoutingGraph":
        g = cls()
        started = time.time()

        raw = json.loads(Path(roads_path).read_text(encoding="utf-8"))
        nodes = {int(k): tuple(v) for k, v in raw["nodes"].items()}
        ways = raw["ways"]

        # --- какие узлы оставляем: перекрёстки и концы линий ---
        usage: dict[int, int] = {}
        for w in ways:
            ids = w["nodes"]
            for nid in ids:
                usage[nid] = usage.get(nid, 0) + 1
            if ids:
                usage[ids[0]] = usage.get(ids[0], 0) + 1
                usage[ids[-1]] = usage.get(ids[-1], 0) + 1
        junctions = {nid for nid, count in usage.items() if count >= 2}

        index: dict[int, int] = {}
        coords: list[tuple[float, float]] = []
        for nid in junctions:
            if nid in nodes:
                index[nid] = len(coords)
                coords.append(nodes[nid])

        # Рёбра храним раздельно по профилям. Складывать их в один список с
        # «запрещающим» весом 1e9 нельзя: Dijkstra спокойно проходит по таким
        # рёбрам, и в матрице появляется время в три миллиарда минут.
        car: list[tuple[int, int, float]] = []
        foot: list[tuple[int, int, float]] = []

        for w in ways:
            highway = w.get("highway", "unclassified")
            speed = CAR_SPEED.get(highway, 25)
            walkable = highway not in FOOT_FORBIDDEN
            oneway_tag = str(w.get("oneway", "no"))
            reverse_only = oneway_tag == "-1"
            oneway = (oneway_tag in ("yes", "true", "1", "-1")
                      or w.get("junction") == "roundabout")

            ids = [n for n in w["nodes"] if n in nodes]
            if len(ids) < 2:
                continue

            chain: list[tuple[float, float]] = []
            start: int | None = None
            length = 0.0
            prev: tuple[float, float] | None = None

            for nid in ids:
                point = nodes[nid]
                if prev is not None:
                    length += haversine_km(prev[0], prev[1], point[0], point[1])
                chain.append(point)
                prev = point
                if nid in index:
                    if start is None:
                        start, chain, length = index[nid], [point], 0.0
                        continue
                    end = index[nid]
                    if end != start and length > 0:
                        g._add_edge(car, foot, start, end, length, speed,
                                    walkable, chain, oneway, reverse_only)
                    start, chain, length = end, [point], 0.0

        g.coords = np.array(coords, dtype=np.float64)
        g.n_road = len(coords)
        print(f"    дорожный граф: {g.n_road} вершин, "
              f"{len(car)} авто / {len(foot)} пеших рёбер "
              f"({time.time() - started:.1f} c)")

        metro_edges = g._build_metro(metro_path)

        n = g.coords.shape[0]
        g.graphs["car"] = _csr(car, n)
        g.graphs["foot"] = _csr(foot, n)
        g.graphs["transit"] = _csr(foot + metro_edges, n)

        g._pick_main_component()
        print(f"    граф собран за {time.time() - started:.1f} c")
        return g

    def _add_edge(self, car: list, foot: list, a: int, b: int,
                  length_km: float, speed: float, walkable: bool,
                  chain: list, oneway: bool, reverse_only: bool) -> None:
        drive = length_km / speed * 60
        walk = length_km / FOOT_SPEED_KMH * 60

        # Автомобиль уважает одностороннее движение…
        if not reverse_only:
            car.append((a, b, drive))
        if (not oneway) or reverse_only:
            car.append((b, a, drive))
        # …а пешеход ходит в обе стороны по любой проходимой улице.
        if walkable:
            foot.append((a, b, walk))
            foot.append((b, a, walk))

        self.geometry[(a, b)] = list(chain)
        self.geometry[(b, a)] = list(reversed(chain))

    def _pick_main_component(self) -> None:
        """Для каждого профиля — своя крупнейшая связная компонента.

        Считать компоненты по объединению профилей нельзя: пешеходный граф
        исключает магистрали, поэтому кварталы, к которым ведёт только шоссе,
        связны для машины и изолированы для пешехода. По объединению они
        выглядят достижимыми, а в пешей матрице оказываются бесконечностью.

        В выгрузке всегда есть и висячие куски: отрезки, обрубленные границей
        области, проезды без выезда. Привязываться к ним нельзя ни в одном
        профиле.
        """
        self._snap: dict[str, tuple[cKDTree, np.ndarray]] = {}
        for mode, graph in self.graphs.items():
            # Компоненты именно СИЛЬНОсвязные. Слабой связности мало: по
            # односторонней улице к дому можно подъехать, но не уехать, и
            # вершина, слабо связанная с сетью, в матрице даёт бесконечность.
            _, labels = connected_components(graph, directed=True,
                                             connection="strong")
            road_labels = labels[:self.n_road]
            main = np.bincount(road_labels).argmax()
            keep = np.flatnonzero(road_labels == main)
            self._snap[mode] = (cKDTree(self._projected(self.coords[keep])), keep)
            print(f"    {mode:8s} крупнейшая компонента: {len(keep)} вершин "
                  f"({len(keep) / max(1, self.n_road) * 100:.1f}% дорожных)")

    def _build_metro(self, metro_path: Path | None) -> list[tuple[int, int, float]]:
        """Слой метро: перегоны, пересадки, вход и выход."""
        edges: list[tuple[int, int, float]] = []
        if not metro_path or not Path(metro_path).exists():
            print("    метро: данных нет, пеший профиль останется без него")
            return edges

        data = json.loads(Path(metro_path).read_text(encoding="utf-8"))
        stations = data.get("stations", [])
        if not stations:
            return edges

        base = self.coords.shape[0]
        extra = np.array([(s["lat"], s["lon"]) for s in stations], dtype=np.float64)
        self.station_name = [s["name"] for s in stations]
        self.coords = np.vstack([self.coords, extra])

        def link(a: int, b: int, cost: float) -> None:
            edges.append((a, b, cost))
            edges.append((b, a, cost))

        st_tree = cKDTree(self._projected(extra))

        # --- перегоны ---
        # Маршрут метро в OSM ссылается на узлы stop_position, лежащие на путях;
        # их идентификаторы не совпадают с идентификаторами станций ни разу.
        # Единственная связь — географическая: ищем ближайшую станцию к узлу.
        stop_nodes = {int(k): v for k, v in data.get("stop_nodes", {}).items()}
        cache: dict[int, int | None] = {}

        def station_of(ref: int) -> int | None:
            if ref in cache:
                return cache[ref]
            point = stop_nodes.get(ref)
            result = None
            if point is not None:
                d, i = st_tree.query(self._projected(np.array([point])), k=1)
                if float(np.atleast_1d(d)[0]) < 400:      # метров
                    result = base + int(np.atleast_1d(i)[0])
            cache[ref] = result
            return result

        spans = 0
        for route in data.get("routes", []):
            seq = [s for s in (station_of(r) for r in route["stops"]) if s is not None]
            for a, b in zip(seq, seq[1:]):
                if a == b:
                    continue
                km = haversine_km(self.coords[a][0], self.coords[a][1],
                                  self.coords[b][0], self.coords[b][1])
                link(a, b, km / METRO_SPEED_KMH * 60 + METRO_DWELL_MIN)
                spans += 1

        # --- пересадки между близко стоящими станциями ---
        pairs = st_tree.query_pairs(TRANSFER_RADIUS_KM * 1000)
        for a, b in pairs:
            link(base + a, base + b, TRANSFER_MIN)

        # --- вход и выход ---
        # Вход дороже выхода на время ожидания поезда: спуститься и уехать —
        # не то же самое, что подняться и пойти.
        road_tree = cKDTree(self._projected(self.coords[:self.n_road]))
        dist, idx = road_tree.query(self._projected(extra), k=ACCESS_LINKS)
        access = 0
        for i in range(extra.shape[0]):
            for d, j in zip(np.atleast_1d(dist[i]), np.atleast_1d(idx[i])):
                if d > ACCESS_RADIUS_KM * 1000:
                    continue
                walk = (d / 1000) / FOOT_SPEED_KMH * 60
                edges.append((int(j), base + i, walk + BOARDING_MIN))
                edges.append((base + i, int(j), walk))
                access += 1

        print(f"    метро: {extra.shape[0]} станций, {spans} перегонов, "
              f"{len(pairs)} пересадок, {access} входов")
        return edges

    @staticmethod
    def _projected(coords: np.ndarray) -> np.ndarray:
        """Грубая проекция в метры — чтобы KD-дерево искало по расстоянию."""
        lat0 = 55.75
        x = np.radians(coords[:, 1]) * EARTH_R_KM * 1000 * math.cos(math.radians(lat0))
        y = np.radians(coords[:, 0]) * EARTH_R_KM * 1000
        return np.column_stack([x, y])

    # ------------------------------------------------------------------ запросы

    def snap(self, points: list[tuple[float, float]], mode: str = "car") -> np.ndarray:
        """Ближайшая дорожная вершина, достижимая в этом профиле."""
        tree, ids = self._snap[mode]
        query = self._projected(np.array(points, dtype=np.float64))
        _, idx = tree.query(query, k=1)
        return ids[np.atleast_1d(idx)]

    def times(self, sources: np.ndarray, mode: str,
              want_paths: bool = False):
        """Матрица времени от источников до всех вершин (минуты)."""
        graph = self.graphs[mode]
        return dijkstra(graph, directed=True, indices=sources,
                        return_predecessors=want_paths)

    def route_geometry(self, predecessors: np.ndarray, source: int,
                       target: int) -> list[list[float]]:
        """Развернуть путь в ломаную [[lon, lat], …] для карты."""
        chain: list[int] = []
        node = target
        guard = 0
        while node != source and node >= 0 and guard < 100_000:
            chain.append(node)
            node = int(predecessors[node])
            guard += 1
        if node != source:
            return []
        chain.append(source)
        chain.reverse()

        out: list[list[float]] = []
        for a, b in zip(chain, chain[1:]):
            seg = self.geometry.get((a, b))
            if seg:
                pts = seg if not out else seg[1:]
                out.extend([[lon, lat] for lat, lon in pts])
            else:
                # ребро метро или пересадка — геометрии нет, соединяем прямой
                if not out:
                    out.append([self.coords[a][1], self.coords[a][0]])
                out.append([self.coords[b][1], self.coords[b][0]])
        return out

    # ------------------------------------------------------------------ кеш

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "coords": self.coords, "n_road": self.n_road,
                "station_name": self.station_name,
                "snap": {m: ids for m, (_, ids) in self._snap.items()},
                "graphs": self.graphs, "geometry": self.geometry,
            }, f, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def load(cls, path: Path) -> "RoutingGraph":
        with open(path, "rb") as f:
            blob = pickle.load(f)
        g = cls()
        g.coords = blob["coords"]
        g.n_road = blob["n_road"]
        g.station_name = blob["station_name"]
        g.graphs = blob["graphs"]
        g.geometry = blob["geometry"]
        g._snap = {m: (cKDTree(g._projected(g.coords[ids])), ids)
                   for m, ids in blob["snap"].items()}
        return g
