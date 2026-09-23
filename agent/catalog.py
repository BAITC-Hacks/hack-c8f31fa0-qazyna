"""Каталог товаров: локальный кэш, живой API ekt.kz или демо-выборка.

Все инструменты работают с единым нормализованным форматом товара:
{sku, name, category, brand, price_kzt, unit, min_order_qty,
 stock: {склад: кол-во}, specs: {характеристика: значение},
 certificates: [{title, url}]}

Формат API проверен по data/api_sample_list.json и api_sample_detail.json.
"""
from __future__ import annotations

import json
import logging
import os
import re
from difflib import get_close_matches
from pathlib import Path

logger = logging.getLogger(__name__)

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
    "NOMINALNYY_OTKLYUCHAYUSHCHIY_DIFFERENTSIALNYY_TOK": "Ток утечки",
    "KHARAKTERISTIKA_SRABATYVANIYA": "Характеристика",
    "MOSHCHNOST_W": "Мощность, Вт",
    "TSVETOVAYA_TEMPERATURA": "Цветовая температура",
    "SVETOVOY_POTOK_LM": "Световой поток, лм",
    "STEPEN_ZASHCHITY": "Степень защиты",
    "SECHENIE_MM2": "Сечение, мм²",
    "MATERIAL_ZHILY": "Материал жилы",
    "MATERIAL_IZOLYATSII_I_OBOLOCHKI": "Материал изоляции",
    "TIP_TOKA_UTECHKI": "Тип тока утечки",
    "CHUVSTVITELNOST_PO_TOKU_UTECHKI": "Чувствительность по току утечки",
    "NOMINALNYY_TOK_A_1": "Номинальный ток",
    "TSVET": "Цвет",
    "KOLICHESTVO_ZHIL": "Число жил",
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
        "category": props.get("OBYEM") or props.get("TIP_USTROYSTVA") or "",
        "brand": (props.get("TORGOVAYA_MARKA") or "").strip(),
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


def load_from_api(max_pages: int = 5, per_page: int | None = None) -> list[dict]:
    """Загружает страницы списка; детали запрашивает Catalog по требованию."""
    items = []
    seen = set()
    for page in range(1, max_pages + 1):
        data = _api_get("products", {"page": page, **({"per_page": per_page} if per_page else {})})
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


def product_kind(product: dict) -> str:
    """Группа для поиска из названия/типа товара; не подмена данных API."""
    text = (product.get("name", "") + " " + product.get("category", "")).lower()
    if re.search(r"диф[.\s-]*авт|авдт|дифференциальный автомат", text):
        return "дифавтомат"
    if re.search(r"\bузо\b|защитного отключения|\bвд[1-9]", text):
        return "узо"
    if re.search(r"автомат|\b[вb][аa]\s*\d|\bав\s|\barmat\b", text):
        return "автомат"
    if re.search(r"\bкабель\b|\bпровод\b|ввг|\bnym\b|\bпвс\b", text) and "канал" not in text:
        return "кабель"
    if re.search(r"розет|\bроз[.\s]", text):
        return "розетка"
    if re.search(r"светильник|освещение|прожектор|\bled\b", text):
        return "светильник"
    return ""


def accessory_kind(product: dict) -> str:
    """Тип сопутствующего товара, а не упоминание его в описании автомата."""
    patterns = {
        "din_rail": r"^(?:(?:din|дин)[\s-]*рейк|рейк\w*[\s-]+(?:din|дин))",
        "box": r"^(?:бокс\b|боксы\b|щит(?:ок)?\s+(?:модуль|распредел))",
        "conduit": r"^(?:гофр[аы]\b|гофротруб\w*\b|гофрированн\w*\s+труб|труб\w*\s+(?:\w+\s+){0,2}гофр)",
    }
    for field in ("category", "name"):
        for kind, pattern in patterns.items():
            if re.search(pattern, product.get(field, "").strip(), re.I):
                return kind
    return ""


def electrical_values(product: dict) -> dict:
    """Сопоставляет реальные и демо-поля; имя используется лишь при отсутствии поля."""
    specs = product.get("specs", {})
    result = {}
    for field, keys in {"current": ("Номинальный ток", "Номинальный ток, А"),
                        "poles": ("Число полюсов",),
                        "leakage": ("Ток утечки", "Ток утечки, мА", "Чувствительность по току утечки")}.items():
        value = next((str(specs[k]) for k in keys if specs.get(k) not in (None, "")), "")
        if value:
            if field == "poles":
                result[field] = value.upper().replace("Р", "P").replace("P+", "+").removesuffix("P").strip()
            else:
                match = re.search(r"\d+(?:[.,]\d+)?", value)
                if match:
                    result[field] = float(match[0].replace(",", "."))
    name = product.get("name", "")
    for field, pattern in {"current": r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*[aа](?!\w)",
                           "poles": r"(?<!\w)([1-4]\s*[pрпф](?:\+N)?)(?!\w)",
                           "leakage": r"(?<!\w)(\d+)\s*м[aа](?!\w)"}.items():
        match = re.search(pattern, name, re.I)
        if field not in result and match:
            result[field] = (match[1].upper().replace("Р", "P").replace("Ф", "P").replace("П", "P").replace(" ", "").replace("P+", "+").removesuffix("P")
                             if field == "poles" else float(match[1].replace(",", ".")))
    return result


class Catalog:
    def __init__(self, products: list[dict] | None = None):
        self.products = products if products is not None else load_catalog()
        self.by_sku = {p["sku"].lower(): p for p in self.products}

    def _ensure_detail(self, product: dict) -> dict:
        if product.get("_detail_pending"):
            import requests
            try:
                raw = _api_get("products/detail", {"id": product["id"]})
            except requests.RequestException as exc:
                logger.warning("catalog detail id=%s failed: %s", product["id"], type(exc).__name__)
                cache = DATA_DIR / "catalog_cache.json"
                cached = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else []
                match = next((p for p in cached if p.get("id") == product["id"]
                              and not p.get("_detail_pending")), None)
                if match is None:
                    raise RuntimeError("API недоступен, полной карточки в кэше нет") from None
                product.update(match)
                product["data_warning"] = "API недоступен: данные из локального кэша, остаток требует проверки"
                return product
            if str(raw.get("id")) != str(product["id"]):
                raise ValueError("API вернул карточку другого товара")
            detail = normalize_api_product(raw)
            if detail["_detail_pending"]:
                raise ValueError("API вернул неполную карточку товара")
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
            tokens.update(_tokens(product_kind(p)))
            vocabulary.update(tokens)
            # Сохраняем и отдельные слова фраз для запроса «выключатель».
            entries.append((p, tokens | _normalize_synonyms(tokens)))
        vocabulary = {word for word in vocabulary if word.isalpha() and len(word) >= 4}
        # Исправляем до свёртки фраз: «автоматическй выключатель» тоже синоним.
        q = _normalize_synonyms(_correct_typos(_tokens(query), vocabulary))
        requested = electrical_values({"name": query})
        amp = re.search(r"(\d+)\s*ампер", query.lower())
        if amp:
            requested["current"] = float(amp[1])
        kind = product_kind({"name": query})
        if not kind:
            kind = next((k for k in ("автомат", "узо", "розетка") if k in q), "")
        brands = {p["brand"].strip().lower() for p in self.products if p["brand"]} | {"iek", "ekf", "schneider", "legrand"}
        requested_brands = {b for b in brands if re.search(r"(?<!\w)" + re.escape(b) + r"(?!\w)", query.lower())}
        scored = []
        for p, hay in entries:
            if kind and product_kind(p) != kind:
                continue
            values = electrical_values(p)
            if any(values.get(k) != v for k, v in requested.items()):
                continue
            if requested_brands and not all(b in (p["brand"] + " " + p["name"]).lower() for b in requested_brands):
                continue
            score = len(q & hay)
            if score:
                scored.append((score, total_stock(p) > 0, p))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [self._ensure_detail(p) for _, _, p in scored[:limit]]

    def accessories(self, sku: str, limit_per_kind: int = 2) -> list[dict]:
        """Категории дополнений и доступные позиции; совместимость требует проверки."""
        base = self.get(sku)
        if base is None or accessory_kind(base):
            return []
        kinds = {"автомат": ("din_rail", "box"), "кабель": ("conduit",)}.get(product_kind(base), ())
        groups = {kind: [] for kind in kinds}
        for p in self.products:
            kind = accessory_kind(p)
            if p is base or kind not in groups or len(groups[kind]) >= limit_per_kind:
                continue
            self._ensure_detail(p)
            if accessory_kind(p) == kind and total_stock(p) >= max(p.get("min_order_qty", 1), 1):
                groups[kind].append(p)
        return [{"kind": kind, "products": products} for kind, products in groups.items()]

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
            kind = product_kind(base)
            if total_stock(p) <= 0 or (product_kind(p) != kind if kind else
                                      not base["category"] or p["category"] != base["category"]):
                continue
            base_values, values = electrical_values(base), electrical_values(p)
            if kind in {"автомат", "узо", "дифавтомат"}:
                required = {"current", "poles"} | ({"leakage"} if kind != "автомат" else set())
                if any(k not in base_values or values.get(k) != base_values[k] for k in required):
                    continue
            if kind == "кабель":
                required_specs = ("Сечение, мм²", "Число жил", "Материал жилы", "Материал изоляции")
                if any(not base["specs"].get(k) or p["specs"].get(k) != base["specs"][k] for k in required_specs):
                    continue
            same = [k for k, v in base["specs"].items() if p["specs"].get(k) == v]
            diff = {k: f"{base['specs'][k]} → {p['specs'].get(k, '—')}" for k in base["specs"] if k not in same}
            if p["brand"] != base["brand"]:
                diff["Марка"] = f"{base['brand'] or 'не указана'} → {p['brand'] or 'не указана'}"
            curve_pattern = r"(?:х-ка|характеристика)\s*([BCD])"
            base_curve = re.search(curve_pattern, base["name"], re.I)
            other_curve = re.search(curve_pattern, p["name"], re.I)
            if base_curve and (not other_curve or other_curve[1].upper() != base_curve[1].upper()):
                diff["Характеристика срабатывания из названия"] = f"{base_curve[1].upper()} → {other_curve[1].upper() if other_curve else 'требует проверки'}"
            for key, label in {"current": "Номинальный ток", "poles": "Число полюсов", "leakage": "Ток утечки"}.items():
                if key in base_values and base_values[key] == values.get(key) and label not in same:
                    same.append(label)
            if not same:
                continue
            out.append({
                "product": p,
                "match_score": round(len(same) / max(len(base["specs"]) + len(base_values), 1), 2),
                "matching_specs": same,
                "differences": diff,
                "price_diff_kzt": (p["price_kzt"] - base["price_kzt"]) if p["price_kzt"] is not None and base["price_kzt"] is not None else None,
            })
        out.sort(key=lambda a: (a["match_score"], -(abs(a["price_diff_kzt"]) if a["price_diff_kzt"] is not None else float("inf"))), reverse=True)
        return out[:limit]
