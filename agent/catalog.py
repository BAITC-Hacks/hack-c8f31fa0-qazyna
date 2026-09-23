"""Каталог товаров: живой API ekt.kz или демо-выборка (data/catalog_sample.json).

Все инструменты работают с единым нормализованным форматом товара:
{sku, name, category, brand, price_kzt, unit, min_order_qty,
 stock: {склад: кол-во}, specs: {характеристика: значение},
 certificates: [{title, url}]}

TODO (для Codex): после первого запроса к живому API посмотреть реальный JSON
и поправить normalize_api_product() под настоящие имена полей.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------- загрузка ----------

def normalize_api_product(raw: dict) -> dict:
    """Приводит товар из API ekt.kz к нашему формату. Имена полей — предположение, проверить!"""
    stock = raw.get("stock") or raw.get("stocks") or raw.get("rests") or {}
    if isinstance(stock, list):  # [{"warehouse": "...", "qty": 5}, ...]
        stock = {s.get("warehouse") or s.get("name", "склад"): s.get("qty") or s.get("quantity", 0) for s in stock}
    elif isinstance(stock, (int, float)):
        stock = {"Склад": stock}
    specs = raw.get("specs") or raw.get("properties") or raw.get("characteristics") or {}
    if isinstance(specs, list):
        specs = {s.get("name"): s.get("value") for s in specs if s.get("name")}
    certs = raw.get("certificates") or raw.get("certs") or []
    if isinstance(certs, str):
        certs = [{"title": "Сертификат", "url": certs}]
    return {
        "sku": str(raw.get("sku") or raw.get("article") or raw.get("articul") or raw.get("id")),
        "id": raw.get("id"),
        "name": raw.get("name") or raw.get("title", ""),
        "category": raw.get("category") or raw.get("category_name", ""),
        "brand": raw.get("brand") or raw.get("manufacturer", ""),
        "price_kzt": raw.get("price_kzt") or raw.get("price"),
        "unit": raw.get("unit", "шт"),
        "min_order_qty": raw.get("min_order_qty") or raw.get("min_qty") or 1,
        "stock": {k: int(v or 0) for k, v in stock.items()},
        "specs": specs,
        "certificates": certs,
        "url": raw.get("url"),
    }


def load_from_api(max_pages: int = 5) -> list[dict]:
    import requests

    user, pwd = os.environ["EKT_API_USER"], os.environ["EKT_API_PASSWORD"]
    base = os.getenv("EKT_API_BASE", "https://ekt.kz/api")
    items: list[dict] = []
    for page in range(1, max_pages + 1):
        r = requests.get(f"{base}/products", params={"page": page}, auth=(user, pwd), timeout=15)
        r.raise_for_status()
        data = r.json()
        batch = data.get("data") or data.get("items") or data.get("products") or (data if isinstance(data, list) else [])
        if not batch:
            break
        items += [normalize_api_product(x) for x in batch]
    return items


def load_catalog() -> list[dict]:
    if os.getenv("USE_LIVE_API") == "1" and os.getenv("EKT_API_USER"):
        try:
            return load_from_api()
        except Exception as e:  # демо не должно падать из-за сети
            print(f"[catalog] API недоступен ({e}), использую демо-выборку")
    return json.loads((DATA_DIR / "catalog_sample.json").read_text(encoding="utf-8"))


def load_terms() -> dict:
    return json.loads((DATA_DIR / "purchase_terms.json").read_text(encoding="utf-8"))


# ---------- логика ----------

def total_stock(p: dict) -> int:
    return sum(p["stock"].values())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\wа-яё,.]+", text.lower().replace(",", ".")))


class Catalog:
    def __init__(self, products: list[dict] | None = None):
        self.products = products if products is not None else load_catalog()
        self.by_sku = {p["sku"].lower(): p for p in self.products}

    def get(self, sku: str) -> dict | None:
        return self.by_sku.get(sku.strip().lower())

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Простой поиск по артикулу/названию/бренду/характеристикам (подсчёт совпавших слов)."""
        exact = self.get(query)
        if exact:
            return [exact]
        q = _tokens(query)
        scored = []
        for p in self.products:
            hay = _tokens(" ".join([p["sku"], p["name"], p["category"], p["brand"],
                                    " ".join(f"{k} {v}" for k, v in p["specs"].items())]))
            score = len(q & hay)
            if score:
                scored.append((score, total_stock(p) > 0, p))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [p for _, _, p in scored[:limit]]

    def analogs(self, sku: str, limit: int = 3) -> list[dict]:
        """Аналоги: та же категория, в наличии, максимум совпадающих ключевых характеристик.
        Возвращает объяснение — какие характеристики совпали и чем отличаются."""
        base = self.get(sku)
        if not base:
            return []
        out = []
        for p in self.products:
            if p is base or p["category"] != base["category"] or total_stock(p) <= 0:
                continue
            same = [k for k, v in base["specs"].items() if p["specs"].get(k) == v]
            diff = {k: f"{base['specs'][k]} → {p['specs'].get(k, '—')}" for k in base["specs"] if k not in same}
            if not same:
                continue
            out.append({
                "product": p,
                "match_score": round(len(same) / max(len(base["specs"]), 1), 2),
                "matching_specs": same,
                "differences": diff,
                "price_diff_kzt": (p["price_kzt"] or 0) - (base["price_kzt"] or 0),
            })
        out.sort(key=lambda a: (a["match_score"], -abs(a["price_diff_kzt"])), reverse=True)
        return out[:limit]
