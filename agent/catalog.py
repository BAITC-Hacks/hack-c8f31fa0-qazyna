"""Каталог товаров: локальный кэш, живой API ekt.kz или демо-выборка.

Все инструменты работают с единым нормализованным форматом товара:
{sku, name, category, brand, price_kzt, unit, min_order_qty,
 stock: {склад: кол-во}, specs: {характеристика: значение},
 certificates: [{title, url}]}

Формат API проверен по data/api_sample_list.json и api_sample_detail.json.
"""
from __future__ import annotations

import json
import os
import re
from difflib import get_close_matches
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------- загрузка ----------

# Технические свойства из полученной карточки. Служебные поля 1С не
# участвуют в сравнении аналогов, но сохраняются в api_properties.
SPEC_NAMES = {
    "KOLICHESTVO_POLYUSOV": "Число полюсов",
    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "Отключающая способность",
    "NOMINALNOE_NAPRYAZHENIE": "Номинальное напряжение",
    "NOMINALNYY_TOK": "Номинальный ток",
    "TIP_USTANOVKI": "Тип установки",
}


def normalize_api_product(raw: dict) -> dict:
    """Нормализует items списка или плоский ответ products/detail.

    В изученном API нет единицы измерения и сертификатов: не выдумываем
    их из названия/описания. quantity не прибавляется к stores повторно.
    """
    props = raw.get("properties") or {}
    stock = {}
    for store in raw.get("stores") or []:
        name = store.get("name") or str(store["id"])
        stock[name] = stock.get(name, 0) + (store.get("quantity") or 0)
    if not stock and raw.get("quantity") is not None:
        stock = {"Общий остаток": raw["quantity"]}
    return {
        "sku": str(raw.get("article") or props.get("CML2_ARTICLE") or raw["id"]),
        "id": raw["id"],
        "name": raw.get("name", ""),
        "category": props.get("OBYEM") or "",
        "brand": props.get("TORGOVAYA_MARKA") or "",
        "price_kzt": raw.get("price"),
        "unit": "",  # API не сообщает единицу измерения
        "min_order_qty": float(str(props.get("KRATNOST_MIN") or 1).replace(",", ".")),
        "stock": stock,
        "specs": {label: props[key] for key, label in SPEC_NAMES.items()
                  if props.get(key) is not None},
        "certificates": [],  # поле не обнаружено в реальном ответе
        "description": raw.get("description", ""),
        "api_properties": props,
        "url": raw.get("url"),
        "_detail_pending": "properties" not in raw or "quantity" not in raw,
    }


def _api_get(path: str, params: dict) -> dict:
    import requests

    base = os.getenv("EKT_API_BASE", "https://ekt.kz/api").rstrip("/")
    response = requests.get(
        f"{base}/{path}", params=params,
        auth=(os.environ["EKT_API_USER"], os.environ["EKT_API_PASSWORD"]),
        timeout=(15, 60),
    )
    response.raise_for_status()
    return response.json()


def load_from_api(max_pages: int = 5) -> list[dict]:
    """Загружает страницы списка; детали запрашивает Catalog по требованию."""
    items = []
    seen = set()
    for page in range(1, max_pages + 1):
        data = _api_get("products", {"page": page})
        batch = data["items"]
        if not batch:
            break
        new = [raw for raw in batch if raw["id"] not in seen]
        if not new:  # защита от API, повторяющего последнюю страницу
            break
        for raw in new:
            if raw["id"] not in seen:
                items.append(normalize_api_product(raw))
                seen.add(raw["id"])
        # count — размер текущей страницы, не общее число товаров.
        if data.get("per_page") and len(batch) < data["per_page"]:
            break
    return items


def load_catalog() -> list[dict]:
    cache = DATA_DIR / "catalog_cache.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    if os.getenv("USE_LIVE_API") == "1" and os.getenv("EKT_API_USER"):
        try:
            return load_from_api()
        except Exception as e:  # не выводим URL/учётные данные из исключения
            print(f"[catalog] API недоступен ({type(e).__name__}), использую демо-выборку")
    return json.loads((DATA_DIR / "catalog_sample.json").read_text(encoding="utf-8"))


def load_terms() -> dict:
    return json.loads((DATA_DIR / "purchase_terms.json").read_text(encoding="utf-8"))


# ---------- логика ----------

def total_stock(p: dict) -> int:
    return sum(p["stock"].values())


_SEARCH_SYNONYMS = {
    "автомат": ({"автоматы"}, {"автоматический", "выключатель"},
                {"автоматические", "выключатели"}),
    "узо": ({"устройство", "защитного", "отключения"},
            {"устройства", "защитного", "отключения"}),
    "ампер": ({"а"}, {"a"}, {"ампера"}, {"амперов"}),
}


def _tokens(text: str) -> set[str]:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    # Отделяем амперы от числа, сохраняя коды моделей и единицы мА/кА.
    text = re.sub(r"\b(\d+(?:\.\d+)?)([аa]|ампер(?:а|ов)?)\b", r"\1 \2", text)
    return set(re.findall(r"\w+(?:\.\w+)*", text))


def _normalize_synonyms(tokens: set[str]) -> set[str]:
    normalized = tokens.copy()
    for canonical, aliases in _SEARCH_SYNONYMS.items():
        for alias in aliases:
            if alias <= normalized:
                normalized.difference_update(alias)
                normalized.add(canonical)
    return normalized


def _correct_typos(tokens: set[str], vocabulary: set[str]) -> set[str]:
    corrected = set()
    for token in tokens:
        # Числа, обозначения моделей и короткие сокращения не исправляем.
        if token not in vocabulary and token.isalpha() and len(token) >= 4:
            matches = get_close_matches(token, vocabulary, n=1, cutoff=0.8)
            token = matches[0] if matches else token
        corrected.add(token)
    return corrected


class Catalog:
    def __init__(self, products: list[dict] | None = None):
        self.products = products if products is not None else load_catalog()
        self.by_sku = {p["sku"].lower(): p for p in self.products}

    def _ensure_detail(self, product: dict) -> dict:
        if product.get("_detail_pending"):
            raw = _api_get("products/detail", {"id": product["id"]})
            if str(raw.get("id")) != str(product["id"]):
                raise ValueError("API вернул карточку другого товара")
            detail = normalize_api_product(raw)
            if detail["_detail_pending"]:
                raise ValueError("API вернул неполную карточку товара")
            # Меняем объект на месте: ссылки из списка и индекса остаются актуальны.
            product.update(detail)
            self.by_sku[product["sku"].lower()] = product
        return product

    def get(self, sku: str) -> dict | None:
        product = self.by_sku.get(sku.strip().lower())
        return self._ensure_detail(product) if product is not None else None

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Поиск по артикулу/названию/бренду/характеристикам с синонимами и опечатками.

        Неизвестные слова запроса исправляем по словарю каталога и синонимов.
        Точный артикул имеет приоритет; при равном числе совпадений — наличие.
        """
        exact = self.get(query)
        if exact:
            return [exact]
        entries = []
        vocabulary = set(_SEARCH_SYNONYMS)
        for aliases in _SEARCH_SYNONYMS.values():
            for alias in aliases:
                vocabulary.update(alias)
        for p in self.products:
            tokens = _tokens(" ".join([p["sku"], p["name"], p["category"], p["brand"],
                                       " ".join(f"{k} {v}" for k, v in p["specs"].items())]))
            vocabulary.update(tokens)
            # Сохраняем и отдельные слова фраз для запроса «выключатель».
            entries.append((p, tokens | _normalize_synonyms(tokens)))
        vocabulary = {word for word in vocabulary if word.isalpha() and len(word) >= 4}
        # Исправляем до свёртки фраз: «автоматическй выключатель» тоже синоним.
        q = _normalize_synonyms(_correct_typos(_tokens(query), vocabulary))
        scored = []
        for p, hay in entries:
            score = len(q & hay)
            if score:
                scored.append((score, total_stock(p) > 0, p))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [self._ensure_detail(p) for _, _, p in scored[:limit]]

    def analogs(self, sku: str, limit: int = 3) -> list[dict]:
        """Аналоги: та же категория, в наличии, максимум совпадающих ключевых характеристик.
        Возвращает объяснение — какие характеристики совпали и чем отличаются."""
        base = self.get(sku)
        if not base:
            return []
        out = []
        for p in self.products:
            if p is base:
                continue
            # Список API не содержит категорию/характеристики: сравниваем
            # только после получения карточки кандидата.
            self._ensure_detail(p)
            if not base["category"] or p["category"] != base["category"] or total_stock(p) <= 0:
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
