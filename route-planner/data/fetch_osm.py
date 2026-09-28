"""Выгрузка дорожной сети и метро из OpenStreetMap через Overpass API.

Нужна, чтобы маршруты считались по дорогам, а не по прямой. Скачивается один
раз и кладётся в кеш: дальше сервис работает с диска и в сеть не ходит — на
защите интернета может не быть.

    py -3.11 data/fetch_osm.py                 # выгрузить в data/osm/ (Москва + юг области)
    py -3.11 data/fetch_osm.py --bbox moscow   # только Москва: вдвое меньше и быстрее
    py -3.11 data/fetch_osm.py --force         # перекачать, даже если кеш есть

Что берём и почему не всё:

* **артериальная сеть** (магистрали → третьестепенные улицы, 40 тыс. линий).
  Полная сеть Москвы вместе с жилыми проездами и тротуарами — 426 тыс. линий:
  это десятки минут загрузки и граф, который чистый Python не потянет
  интерактивно. Визиты в плане разнесены на десятки минут, поэтому потеря
  последних сотен метров до подъезда на качество маршрута не влияет.
* **метро**: станции и линии целиком — их мало, а для пеших инженеров это
  основной способ передвижения по городу.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

#: Границы выгрузки. По умолчанию — Москва и юг области до Каширы: заявки
#: заказчика на Юго-востоке уходят в Домодедово, Каширу и Ступино. Контур
#: вчетверо больше московского по площади, но за МКАД артериальная сеть
#: редкая: 66,8 тыс. линий против 43,1 тыс., граф собирается за 6 с вместо 3.
BBOXES = {
    "moscow": (55.55, 37.30, 55.94, 37.88),
    "oblast": (54.75, 37.30, 55.94, 38.40),
}
BBOX = BBOXES["oblast"]

#: Классы дорог для автомобильного графа. Жилые проезды и тротуары не берём
#: сознательно, см. docstring модуля.
CAR_CLASSES = (
    "motorway|trunk|primary|secondary|tertiary|unclassified"
    "|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
)

OUT_DIR = Path(__file__).parent / "osm"
HEADERS = {"User-Agent": "lct2026-route-planner/0.1 (hackathon prototype)"}


def query(body: str, attempt_limit: int = 4) -> dict:
    """Выполнить запрос к Overpass с отступлением при 429/504.

    Публичный Overpass ограничивает частоту запросов, и на втором подряд
    прилетает 429. Ретраи с паузой обязательны, иначе выгрузка разваливается
    на середине.
    """
    last: Exception | None = None
    for attempt in range(attempt_limit):
        endpoint = OVERPASS[attempt % len(OVERPASS)]
        try:
            req = urllib.request.Request(endpoint, data=body.encode("utf-8"),
                                         headers=HEADERS)
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 504, 503):
                pause = 15 * (attempt + 1)
                print(f"    {exc.code} от {endpoint.split('/')[2]}, "
                      f"жду {pause} с…")
                time.sleep(pause)
                continue
            raise
        except Exception as exc:                      # сеть моргнула
            last = exc
            time.sleep(10)
    raise RuntimeError(f"Overpass не ответил после {attempt_limit} попыток: {last}")


def bbox_str() -> str:
    return ",".join(str(v) for v in BBOX)


# --------------------------------------------------------------------------

def fetch_roads() -> dict:
    """Линии дорог с координатами узлов."""
    print("  дороги…")
    started = time.time()
    data = query(
        f"[out:json][timeout:600];"
        f'way({bbox_str()})["highway"~"^({CAR_CLASSES})$"];'
        f"(._;>;);out skel qt;"
    )
    nodes: dict[int, tuple[float, float]] = {}
    ways: list[dict] = []
    for el in data["elements"]:
        if el["type"] == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])
        elif el["type"] == "way":
            ways.append({"id": el["id"], "nodes": el["nodes"]})

    apply_tags(ways)

    print(f"    {len(ways)} линий, {len(nodes)} узлов "
          f"({time.time() - started:.0f} c)")
    return {"nodes": {str(k): v for k, v in nodes.items()}, "ways": ways}


def fetch_tags() -> dict:
    """Теги дорог отдельным запросом: `out skel` их не отдаёт."""
    print("  теги дорог…")
    time.sleep(3)
    tagged = query(
        f"[out:json][timeout:600];"
        f'way({bbox_str()})["highway"~"^({CAR_CLASSES})$"];'
        f"out tags qt;"
    )
    return {el["id"]: el.get("tags", {}) for el in tagged["elements"]}


def apply_tags(ways: list[dict], tags: dict | None = None) -> None:
    """Разложить нужные теги по линиям. Что не взяли — то графу не видно."""
    tags = tags if tags is not None else fetch_tags()
    with_speed = 0
    for w in ways:
        t = tags.get(w["id"], {})
        w["highway"] = t.get("highway", "unclassified")
        w["oneway"] = t.get("oneway", "no")
        w["junction"] = t.get("junction", "")
        # Разрешённая скорость есть примерно у двух третей линий. Там, где её
        # нет, остаётся оценка по классу дороги.
        w["maxspeed"] = t.get("maxspeed", "")
        with_speed += bool(w["maxspeed"])
    print(f"    разрешённая скорость указана у {with_speed} из {len(ways)} линий "
          f"({with_speed / max(1, len(ways)) * 100:.0f} %)")


def fetch_restrictions() -> dict:
    """Запреты поворота: отношения type=restriction.

    В нашей области их около 12 тысяч. Без них маршрут сворачивает там, где
    поворот запрещён знаком или разметкой.

    Берём только запреты «через узел»: `via` в виде цепочки путей встречается
    на развязках и требует отдельной обработки, которой у нас нет — такие
    отношения пропускаем и считаем в сводке, чтобы масштаб упрощения был виден.
    """
    print("  запреты поворота…")
    time.sleep(3)
    raw = query(
        f"[out:json][timeout:600];"
        f'relation({bbox_str()})["type"="restriction"];'
        f"out body qt;"
    )
    items, via_way, incomplete = [], 0, 0
    for el in raw["elements"]:
        if el.get("type") != "relation":
            continue
        kind = el.get("tags", {}).get("restriction", "")
        members = el.get("members", [])
        frm = next((m["ref"] for m in members
                    if m.get("role") == "from" and m["type"] == "way"), None)
        to = next((m["ref"] for m in members
                   if m.get("role") == "to" and m["type"] == "way"), None)
        via = [m for m in members if m.get("role") == "via"]
        if frm is None or to is None or not via:
            incomplete += 1
            continue
        if via[0]["type"] != "node":
            via_way += 1
            continue
        items.append({"kind": kind, "from": frm, "via": via[0]["ref"], "to": to})

    print(f"    {len(items)} запретов через узел, "
          f"{via_way} через путь (пропущены), {incomplete} неполных")
    return {"restrictions": items, "skipped_via_way": via_way}


def fetch_metro() -> dict:
    """Станции метро и последовательности остановок по линиям."""
    print("  метро…")
    time.sleep(3)
    stations_raw = query(
        f"[out:json][timeout:300];"
        f'node({bbox_str()})["station"="subway"];'
        f"out body qt;"
    )
    stations = [
        {"id": el["id"], "lat": el["lat"], "lon": el["lon"],
         "name": el.get("tags", {}).get("name", "")}
        for el in stations_raw["elements"] if el["type"] == "node"
    ]

    time.sleep(3)
    routes_raw = query(
        f"[out:json][timeout:300];"
        f'relation({bbox_str()})["route"="subway"];'
        f"out body qt;"
    )
    routes = []
    for el in routes_raw["elements"]:
        if el["type"] != "relation":
            continue
        stops = [m["ref"] for m in el.get("members", [])
                 if m["type"] == "node" and m.get("role", "").startswith("stop")]
        if len(stops) < 2:
            # у части отношений остановки размечены ролью platform
            stops = [m["ref"] for m in el.get("members", [])
                     if m["type"] == "node" and "platform" in m.get("role", "")]
        if len(stops) >= 2:
            routes.append({
                "id": el["id"],
                "name": el.get("tags", {}).get("name", ""),
                "colour": el.get("tags", {}).get("colour", ""),
                "stops": stops,
            })

    # Координаты остановок обязательны. В OSM маршрут метро ссылается на узлы
    # stop_position, лежащие на путях, а не на сами станции: их идентификаторы
    # со станциями не совпадают ни разу из 558. Связать линии со станциями можно
    # только через географию, поэтому тянем координаты узлов-остановок.
    print("  остановки маршрутов…")
    time.sleep(3)
    members_raw = query(
        f"[out:json][timeout:300];"
        f'relation({bbox_str()})["route"="subway"];'
        f"node(r);out skel qt;"
    )
    stop_nodes = {str(el["id"]): (el["lat"], el["lon"])
                  for el in members_raw["elements"] if el["type"] == "node"}

    print(f"    {len(stations)} станций, {len(routes)} маршрутов линий, "
          f"{len(stop_nodes)} узлов-остановок")
    return {"stations": stations, "routes": routes, "stop_nodes": stop_nodes}


# --------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true", help="перекачать поверх кеша")
    p.add_argument("--tags-only", action="store_true",
                   help="обновить только теги дорог, не трогая геометрию")
    p.add_argument("--out", default=str(OUT_DIR))
    p.add_argument("--bbox", default="oblast",
                   help="moscow | oblast | 'юг,запад,север,восток' в градусах")
    args = p.parse_args()

    global BBOX
    if args.bbox in BBOXES:
        BBOX = BBOXES[args.bbox]
    else:
        BBOX = tuple(float(x) for x in args.bbox.split(","))
        if len(BBOX) != 4:
            raise SystemExit("--bbox: четыре числа через запятую либо moscow/oblast")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    roads_path, metro_path = out / "roads.json", out / "metro.json"

    print(f"Выгрузка OSM в {out}")
    print(f"  область: {bbox_str()}")

    # Теги меняются чаще геометрии, а весит она в тридцать раз больше. Отдельный
    # режим избавляет от перекачки 14 МБ узлов из-за одного нового тега.
    if args.tags_only:
        if not roads_path.exists():
            raise SystemExit("сначала нужна полная выгрузка: без --tags-only")
        roads = json.loads(roads_path.read_text(encoding="utf-8"))
        apply_tags(roads["ways"])
        roads_path.write_text(json.dumps(roads), encoding="utf-8")
        print("Готово. Удалите data/osm/graph.pkl, чтобы граф пересобрался.")
        return

    if roads_path.exists() and not args.force:
        print(f"  дороги уже выгружены ({roads_path.stat().st_size / 1e6:.0f} МБ), "
              f"пропускаю — --force чтобы перекачать")
    else:
        roads = fetch_roads()
        roads_path.write_text(json.dumps(roads), encoding="utf-8")
        print(f"    записано {roads_path.stat().st_size / 1e6:.0f} МБ")

    if metro_path.exists() and not args.force:
        print("  метро уже выгружено, пропускаю")
    else:
        metro = fetch_metro()
        metro_path.write_text(json.dumps(metro, ensure_ascii=False),
                              encoding="utf-8")
        print(f"    записано {metro_path.stat().st_size / 1e6:.1f} МБ")

    turns_path = out / "restrictions.json"
    if turns_path.exists() and not args.force:
        print("  запреты поворота уже выгружены, пропускаю")
    else:
        turns = fetch_restrictions()
        turns_path.write_text(json.dumps(turns), encoding="utf-8")
        print(f"    записано {turns_path.stat().st_size / 1e6:.1f} МБ")

    print("Готово. Граф строится при первом запуске сервиса и кешируется.")


if __name__ == "__main__":
    main()
