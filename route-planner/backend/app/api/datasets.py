"""Реестр наборов данных: встроенные и загруженные.

ТЗ, п. 2.1: сервис загружает тестовые данные из CSV или JSON либо использует
встроенный демонстрационный набор. Встроенных четыре — три территории
заказчика «Билайн Бизнес» за 17.08.2026 и синтетический день с оборудованием
и складами. Загруженные наборы складываются в `data/uploads/` и живут до
перезапуска в реестре наравне со встроенными.
"""

from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
UPLOAD_DIR = DATA_DIR / "uploads"


@dataclass(frozen=True)
class DatasetInfo:
    key: str
    title: str
    path: Path
    kind: str            # customer | synthetic | uploaded
    note: str = ""

    def as_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "kind": self.kind,
                "note": self.note, "available": self.path.exists()}


BUILTIN: "OrderedDict[str, DatasetInfo]" = OrderedDict([
    ("beeline-vostok", DatasetInfo(
        "beeline-vostok", "Билайн Бизнес — Восток, 17.08.2026",
        DATA_DIR / "beeline" / "vostok" / "snapshot.json", "customer",
        "66 заявок, 12 бригад; выгрузка заказчика")),
    ("beeline-yugocentr", DatasetInfo(
        "beeline-yugocentr", "Билайн Бизнес — Югоцентр, 17.08.2026",
        DATA_DIR / "beeline" / "yugocentr" / "snapshot.json", "customer",
        "56 заявок, 11 бригад; выгрузка заказчика")),
    ("beeline-yugo-vostok", DatasetInfo(
        "beeline-yugo-vostok", "Билайн Бизнес — Юго-восток, 17.08.2026",
        DATA_DIR / "beeline" / "yugo-vostok" / "snapshot.json", "customer",
        "83 заявки, 12 бригад; Домодедово, Кашира и Ступино — область до Каширы")),
    ("synthetic", DatasetInfo(
        "synthetic", "Синтетический день с оборудованием и складами",
        DATA_DIR / "seed" / "snapshot.json", "synthetic",
        "70 заявок, 18 инженеров, четыре типа транспорта, дефицитный инструмент")),
])


class Registry:
    def __init__(self, upload_dir: Path = UPLOAD_DIR) -> None:
        self.items: "OrderedDict[str, DatasetInfo]" = OrderedDict(BUILTIN)
        self.upload_dir = Path(upload_dir)

    def get(self, key: str) -> DatasetInfo:
        info = self.items.get(key)
        if info is None or not info.path.exists():
            raise KeyError(key)
        return info

    def key_for(self, path: Path) -> str | None:
        for key, info in self.items.items():
            if info.path.resolve() == Path(path).resolve():
                return key
        return None

    def register_upload(self, snapshot: dict, name: str) -> DatasetInfo:
        """Сохранить загруженный снимок на диск и включить в реестр."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-z0-9а-яё]+", "-", name.lower()).strip("-")[:40] or "upload"
        key = f"upload-{stamp}-{slug}"
        path = self.upload_dir / key / "snapshot.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
        info = DatasetInfo(key, snapshot["meta"].get("title", name), path, "uploaded",
                           f"{len(snapshot['jobs'])} заявок, "
                           f"{len(snapshot['engineers'])} инженеров; загружено {stamp}")
        self.items[key] = info
        return info

    def listing(self) -> list[dict]:
        return [i.as_dict() for i in self.items.values()]
