"""Геокодирование адресов заказчика через Nominatim (OpenStreetMap).

В выгрузке «Билайн Бизнес» координат нет — только адрес и район. Карте и
солверу нужны широта и долгота, поэтому адреса один раз прогоняются через
Nominatim, а результат кладётся в кеш `data/beeline/geocode.json`. Кеш
хранится в репозитории: на защите интернета может не быть, а 200 запросов
при лимите один в секунду — это четыре минуты ожидания.

    py -3.11 data/geocode.py                # прогреть кеш по всем выгрузкам
    py -3.11 data/geocode.py --report       # только показать, что не нашлось

Адреса в выгрузке написаны десятком способов: «Город Москва, ул.Грайвороновская,
д. 10 к 2, кв. 71», «МО, г. Кашира Кржижановского ул. д. 5/1», «Москва
Булатниковский пр-зд. д. 6к1». Nominatim такое не понимает, поэтому адрес
сначала разбирается на город, улицу и дом, а потом собирается заново в форме,
которую геокодер принимает: «Москва, Грайвороновская улица, 10к2».

Точность результата запоминается вместе с координатами:

* `house`   — найден именно этот дом;
* `house~`  — найден дом без корпуса/строения (соседний подъезд, не соседняя улица);
* `street`  — найдена только улица, точка стоит где-то на ней;
* `district` — улица не нашлась, взят центр района;
* `none`    — не нашлось ничего.

Всё, что хуже `house`, интерфейс помечает как приблизительное: диспетчер должен
видеть, где мы угадываем.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

NOMINATIM = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "lct2026-route-planner/0.1 (hackathon prototype)"}
MIN_INTERVAL_S = 1.1            # политика Nominatim: не чаще одного запроса в секунду

CACHE_PATH = Path(__file__).parent / "beeline" / "geocode.json"
RAW_DIR = Path(__file__).parent / "beeline" / "raw"

#: Сокращения типов улиц из выгрузки → полное слово, как в OSM.
STREET_TYPES = {
    "ул": "улица", "пр-кт": "проспект", "просп": "проспект", "пер": "переулок",
    "проезд": "проезд", "пр-зд": "проезд", "пр": "проезд", "б-р": "бульвар",
    "бул": "бульвар", "наб": "набережная", "ш": "шоссе", "пл": "площадь",
    "туп": "тупик", "аллея": "аллея", "линия": "линия", "кв-л": "квартал",
}

#: Города, встречающиеся в выгрузке, и как их называть в запросе.
CITIES = {
    "москва": "Москва",
    "домодедово": "Домодедово, Московская область",
    "кашира": "Кашира, Московская область",
    "ступино": "Ступино, Московская область",
    "видное": "Видное, Московская область",
    "подольск": "Подольск, Московская область",
}


@dataclass(frozen=True)
class Parsed:
    city: str
    street: str          # «Грайвороновская улица» либо пусто
    house: str           # «10к2» либо пусто
    house_base: str      # «10» — дом без корпуса и строения


@dataclass
class Geo:
    lat: float
    lon: float
    precision: str       # house | house~ | street | district | none
    query: str           # что именно спросили у геокодера
    found: str           # display_name из ответа


# ------------------------------------------------------------------ разбор адреса

def _normalize_house(raw: str) -> tuple[str, str]:
    """«10 к 2» → («10к2», «10»); «83с 4» → («83с4», «83»); «5/1» → («5/1», «5/1»)."""
    h = raw.strip().strip(".,").strip()
    h = re.sub(r"\s*стр\.?\s*", "с", h, flags=re.IGNORECASE)
    h = re.sub(r"\s*корп\.?\s*", "к", h, flags=re.IGNORECASE)
    h = re.sub(r"\s*([кс])\s*(?=\d)", r"\1", h)         # «10 к 2» → «10к2»
    h = h.replace(" ", "")
    base = re.split(r"[кс](?=\d)", h)[0]
    if not re.match(r"^\d", base):                      # «к5» без номера дома
        return "", ""
    return h, base


def parse_address(raw: str) -> Parsed:
    """Разобрать адрес из выгрузки на город, улицу и дом.

    Форматы плавают от строки к строке, поэтому не полагаемся на запятые:
    ищем известный город, дом по маркеру «д.» и тип улицы по сокращению —
    в любом порядке.
    """
    text = raw.strip()
    text = re.split(r",?\s*кв\.?\s*\d", text)[0]          # квартира не нужна
    low = text.lower()

    city = ""
    for key, full in CITIES.items():
        if re.search(rf"\b{key}\b", low):
            city = full
            break
    if not city and re.search(r"\bмо\b|московская обл", low):
        city = "Московская область"
    if not city:
        city = "Москва"

    house = house_base = ""
    m = re.search(r"(?:^|[,\s])д\.?\s*([0-9][^,]*)$", text)
    if m:
        house, house_base = _normalize_house(m.group(1))
        text = text[:m.start()]
    else:
        # Выхинская адресация: «б-р.Самаркандский Квартал 137а, д. к5» —
        # номер дома это квартал, а после «д.» только корпус.
        m = re.search(r"квартал\s+(\S+)[,\s]+д\.?\s*(к\s*\d+)$", text, flags=re.IGNORECASE)
        if m:
            house, house_base = _normalize_house(m.group(1).rstrip(",") + m.group(2))
            text = text[:m.start()]

    # выкидываем всё, что относится к городу/области, остаток — улица
    street_part = text
    for pat in (r"г\.?\s*город\s+москва", r"город\s+москва", r"г\.?\s*москва",
                r"\bмосква\b", r"обл\.?\s*московская\s+область", r"\bмо\b",
                r"г\.?\s*(домодедово|кашира|ступино|видное|подольск)",
                r"\b(домодедово|кашира|ступино|видное|подольск)\b"):
        street_part = re.sub(pat, " ", street_part, flags=re.IGNORECASE)
    street_part = street_part.replace(",", " ").strip(" .")

    street = ""
    kind = ""
    tokens = [t for t in re.split(r"\s+", street_part) if t]
    rest: list[str] = []
    for tok in tokens:
        key = tok.rstrip(".").lower()
        if key in STREET_TYPES and not kind:
            kind = STREET_TYPES[key]
            # «ул.Грайвороновская» — тип приклеен к имени без пробела
            continue
        m2 = re.match(r"^([а-яё\-]+)\.(.+)$", tok, flags=re.IGNORECASE)
        if m2 and m2.group(1).lower() in STREET_TYPES and not kind:
            kind = STREET_TYPES[m2.group(1).lower()]
            rest.append(m2.group(2))
            continue
        rest.append(tok)
    if rest:
        name = " ".join(rest)
        # порядковый номер вперёд: «Советский 1-й» → «1-й Советский»
        m3 = re.match(r"^(.*?)\s+(\d+-[йя])$", name)
        if m3:
            name = f"{m3.group(2)} {m3.group(1)}"
        street = f"{name} {kind}".strip() if kind else name
    return Parsed(city=city, street=street, house=house, house_base=house_base)


def query_variants(p: Parsed) -> list[tuple[str, str]]:
    """Запросы от точного к грубому вместе с точностью, которую они дают."""
    out: list[tuple[str, str]] = []
    if p.street and p.house:
        out.append((f"{p.city}, {p.street}, {p.house}", "house"))
        if p.house_base and p.house_base != p.house:
            out.append((f"{p.city}, {p.street}, {p.house_base}", "house~"))
    if p.street:
        out.append((f"{p.city}, {p.street}", "street"))
    return out


# ------------------------------------------------------------------ Nominatim

class Nominatim:
    def __init__(self) -> None:
        self._last = 0.0
        self.requests = 0

    def search(self, text: str) -> list[dict]:
        wait = MIN_INTERVAL_S - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        params = {"q": text, "format": "jsonv2", "limit": 3, "countrycodes": "ru",
                  "accept-language": "ru", "addressdetails": 1}
        url = NOMINATIM + "?" + urllib.parse.urlencode(params)
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self._last = time.time()
                    self.requests += 1
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503):
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
            except Exception:
                time.sleep(3)
        return []


def _pick(results: list[dict], want: str, city: str) -> dict | None:
    """Выбрать ответ нужной точности и в нужном городе.

    Nominatim охотно возвращает улицу вместо дома или дом в другом городе;
    без фильтра половина точек уехала бы не туда.
    """
    city_key = city.split(",")[0].lower()
    for r in results:
        addr = r.get("address", {})
        place = " ".join(str(v) for v in addr.values()).lower()
        if city_key not in place and city_key != "московская область":
            continue
        if want in ("house", "house~"):
            if addr.get("house_number"):
                return r
        elif want == "street":
            if addr.get("road") and not addr.get("house_number"):
                return r
            if addr.get("road"):
                return r
    return None


# ------------------------------------------------------------------ кеш

class Geocoder:
    def __init__(self, cache_path: Path = CACHE_PATH, online: bool = True) -> None:
        self.cache_path = Path(cache_path)
        self.online = online
        self.cache: dict[str, dict] = {}
        if self.cache_path.exists():
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.client = Nominatim()
        self.dirty = False

    def save(self) -> None:
        if not self.dirty:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self.cache, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8")
        self.dirty = False

    def lookup(self, raw: str, district: str = "") -> Geo:
        # Ключ кеша — нормализованный запрос, а не сырая строка: адрес с
        # квартирой и без неё это один дом, и спрашивать о нём дважды незачем.
        parsed = parse_address(raw)
        variants = query_variants(parsed)
        key = variants[0][0] if variants else raw.strip()
        hit = self.cache.get(key)
        if hit is not None:
            return Geo(**hit)
        if not self.online:
            return Geo(0.0, 0.0, "none", "", "")

        geo = Geo(0.0, 0.0, "none", "", "")
        for text, precision in variants:
            chosen = _pick(self.client.search(text), precision, parsed.city)
            if chosen:
                geo = Geo(float(chosen["lat"]), float(chosen["lon"]), precision,
                          text, chosen.get("display_name", ""))
                break
        if geo.precision == "none" and district:
            text = f"{parsed.city}, район {district}"
            res = self.client.search(text)
            if res:
                geo = Geo(float(res[0]["lat"]), float(res[0]["lon"]), "district",
                          text, res[0].get("display_name", ""))
        self.cache[key] = asdict(geo)
        self.dirty = True
        return geo


# ------------------------------------------------------------------ CLI

def addresses_from_raw() -> list[tuple[str, str]]:
    """Все адреса из выгрузок заказчика вместе с районом (для отката)."""
    seen: dict[str, str] = {}
    for f in sorted(RAW_DIR.glob("*.csv")):
        rows = csv.DictReader(f.read_text(encoding="cp1251").splitlines(), delimiter=";")
        for r in rows:
            if r.get("Заявка", "").strip().lower() == "адрес офиса":   # адрес офиса
                seen.setdefault(r["Тип заявки BK"].strip(), "")
            elif r.get("Адрес"):
                seen.setdefault(r["Адрес"].strip(), r.get("Район", ""))
    return list(seen.items())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--report", action="store_true", help="не ходить в сеть, показать пробелы")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    items = addresses_from_raw()
    gc = Geocoder(online=not args.report)
    print(f"адресов в выгрузках: {len(items)}, в кеше: {len(gc.cache)}")
    started = time.time()
    stats: dict[str, int] = {}
    for i, (raw, district) in enumerate(items, 1):
        geo = gc.lookup(raw, district)
        stats[geo.precision] = stats.get(geo.precision, 0) + 1
        if geo.precision != "house":
            print(f"  [{geo.precision:8s}] {raw}")
        if i % 25 == 0:
            gc.save()
            print(f"  … {i}/{len(items)}, запросов {gc.client.requests}, "
                  f"{time.time() - started:.0f} c")
    gc.save()
    print("\nточность:", dict(sorted(stats.items())))
    print(f"запросов к Nominatim: {gc.client.requests}, {time.time() - started:.0f} c")


if __name__ == "__main__":
    main()
